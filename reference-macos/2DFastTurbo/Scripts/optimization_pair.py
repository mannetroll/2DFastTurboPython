#!/usr/bin/env python3
"""Sequential, interleaved performance screening of two frozen solver builds.

By default each timed run completes at least 20 active wall seconds, including
its final GPU batch. Physical endpoints are retained as timing evidence.
Separate deterministic checkpoints establish numerical validation coverage.
--turnovers explicitly selects the older fixed-physical-endpoint mode.

This records repeatability, endpoint and presentation checks, and spectral
agreement. It does not supply the independent accuracy/reference gates required
by optimizing.txt and never promotes a candidate automatically. Existing apps
are not suspended. Use an otherwise idle machine and a fresh output directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import shutil
import signal
import statistics
import subprocess
import sys
import time

import numpy as np

from validate import FourierGrid, nested, pao_fixture


def save(path: Path, value) -> None:
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def capture(command: list[str]) -> dict:
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return dict(command=command, returncode=run.returncode,
                    stdout=run.stdout, stderr=run.stderr)
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(command=command, error=str(error))


def executable(path: Path) -> Path:
    if path.suffix == '.app':
        with (path / 'Contents/Info.plist').open('rb') as source:
            name = plistlib.load(source)['CFBundleExecutable']
        path = path / 'Contents/MacOS' / name
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f'No executable at {path}')
    return path.resolve()


def positive(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError('Expected a finite positive value')
    return parsed


def fixture_spec(value: str) -> tuple[int, float]:
    try:
        k0, reynolds = value.split(':')
        result = int(k0), positive(reynolds)
        if result[0] < 1:
            raise ValueError('k0 must be positive')
        return result
    except (ValueError, argparse.ArgumentTypeError) as error:
        raise argparse.ArgumentTypeError('Expected positive integer k0:positive Re') from error


def close(actual, expected, label: str, *, tolerance=2e-7) -> None:
    if actual is None or not math.isclose(float(actual), expected,
                                         rel_tol=tolerance, abs_tol=tolerance):
        raise ValueError(f'{label}: expected {expected}, reported {actual}')


def prepare_fixture(root: Path | None, output: Path, n: int, k0: int,
                    reynolds: float, seed: int, turnovers: float | None) -> dict:
    identifier = f'n{n}-k{k0}-re{reynolds:g}-seed{seed}'
    directory = output / 'fixtures' / identifier
    directory.mkdir(parents=True)
    initial = directory / 'initial.f32'
    expected = dict(resolution=n, padded_resolution=3*n//2,
                    k0=k0, reynolds=reynolds, seed=seed)
    source = root / identifier if root else None
    if source is not None and source.exists():
        metadata = json.loads((source / 'fixture.json').read_text())
        for key, value in expected.items():
            close(metadata.get(key), value, f'{identifier}: fixture {key}', tolerance=0)
        source_field = source / 'initial.f32'
        if digest(source_field) != metadata['initial_sha256']:
            raise ValueError(f'{identifier}: source fixture hash differs from metadata')
        shutil.copyfile(source_field, initial)
        if digest(initial) != metadata['initial_sha256']:
            raise ValueError(f'{identifier}: source fixture changed while copying')
        provenance = dict(kind='copied-serialized-fixture', directory=str(source))
    else:
        field = pao_fixture(n, k0, seed)
        field.tofile(initial)
        grid = FourierGrid(n)
        metadata = dict(invariants=grid.invariants(grid.transform(field)))
        provenance = dict(kind='pao_fixture', generator_source_sha256=digest(
            Path(__file__).with_name('validate.py')), numpy_version=np.__version__)
    if initial.stat().st_size != n*n*4:
        raise ValueError(f'{identifier}: expected N x N little-endian Float32 input')
    field = np.memmap(initial, mode='r', dtype='<f4')
    if not np.isfinite(field).all():
        raise ValueError(f'{identifier}: nonfinite fixture')
    tau = float(metadata['invariants']['initial_turnover_time'])
    if not math.isfinite(tau) or tau <= 0:
        raise ValueError(f'{identifier}: invalid initial turnover time')
    result = dict(**expected, id=identifier, initial_file=str(initial),
                  initial_sha256=digest(initial), invariants=metadata['invariants'],
                  provenance=provenance, turnovers=turnovers,
                  endpoint=turnovers*tau if turnovers is not None else None)
    save(directory / 'fixture.json', result)
    return result


def command_for(args, binary: Path, fixture: dict, directory: Path) -> list[str]:
    command = [str(binary), '--gui-benchmark' if args.gui else '--benchmark',
               '--resolution', str(fixture['resolution']),
               '--reynolds', format(fixture['reynolds'], 'g'),
               '--k0', str(fixture['k0']), '--seed', str(fixture['seed']),
               '--integrator', 'etdrk4', '--operator', 'stress', '--cfl', '3',
               '--initial', fixture['initial_file'], '--output', str(directory/'result.json'),
               '--spectrum', str(directory/'spectrum.c64')]
    if args.wall_seconds is not None:
        command += ['--wall-seconds', repr(args.wall_seconds)]
    else:
        # Freeze the physical endpoint in explicitly requested turnover mode.
        # The solver's Float32 reduction of tau can differ from fixture tau.
        command += ['--until', repr(fixture['endpoint'])]
    if args.gui:
        command += ['--gui-warmup-seconds', '2',
                    '--canvas-size', '640', '--display', 'vorticity',
                    '--palette', 'ember', '--exposure', '1.2']
    else:
        command += ['--warmup', '8', '--batch', '8', '--snapshot-hz', '0']
    return command


def owned_gui_processes(binary: Path, marker: Path) -> dict[int, int]:
    """Identify only this run by both executable and unique output argument."""
    output = subprocess.check_output(['ps', '-axo', 'pid=,rss=,args='], text=True)
    found = {}
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        pid, rss, command = fields
        if (command.startswith(str(binary) + ' ') and '--gui-benchmark' in command
                and str(marker) in command):
            found[int(pid)] = int(rss)*1024
    return found


def stop_owned_gui(binary: Path, marker: Path) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = owned_gui_processes(binary, marker)
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 3
        while pids and time.monotonic() < deadline:
            time.sleep(0.1)
            pids = owned_gui_processes(binary, marker)
        if not pids:
            return
    raise RuntimeError('Owned GUI benchmark did not stop; sequential execution cannot continue')


def run_process(command: list[str], directory: Path, binary: Path,
                app: Path | None, timeout: float) -> dict:
    stdout, stderr = directory/'stdout.txt', directory/'stderr.txt'
    marker = directory/'result.json'
    launch = command
    if app is not None:
        launch = ['open', '-n', '-W', '--stdout', str(stdout), '--stderr', str(stderr),
                  str(app), '--args', *command[1:]]
    save(directory/'command.json', dict(launch=launch, solver=command))
    # RSS samples are a lower bound on the process peak, separate from the
    # solver's reported allocation counts. Apply the same sampler to both builds.
    samples, process = [], None
    started = time.monotonic()
    execution = dict(started_utc=datetime.now(timezone.utc).isoformat(),
                     memory_sample_interval_seconds=1)
    interrupted = False
    try:
        with stdout.open('w') as out, stderr.open('w') as err:
            process = subprocess.Popen(launch, stdout=out if app is None else subprocess.DEVNULL,
                                       stderr=err if app is None else subprocess.DEVNULL,
                                       start_new_session=True)
            while True:
                remaining = timeout - (time.monotonic()-started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(launch, timeout)
                try:
                    execution['returncode'] = process.wait(timeout=min(1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    try:
                        if app is not None:
                            sample = owned_gui_processes(binary, marker)
                            samples.append(dict(elapsed=time.monotonic()-started,
                                                processes={str(k): v for k, v in sample.items()}))
                        else:
                            report = subprocess.run(['ps', '-o', 'rss=', '-p', str(process.pid)],
                                                    capture_output=True, text=True, timeout=3)
                            if report.returncode == 0 and report.stdout.strip():
                                samples.append(dict(elapsed=time.monotonic()-started,
                                                    processes={str(process.pid): int(report.stdout)*1024}))
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        execution.setdefault('memory_sample_errors', []).append(str(error))
    except BaseException as error:
        execution['error'] = str(error) or type(error).__name__
        interrupted = isinstance(error, KeyboardInterrupt)
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        execution['driver_wall_seconds'] = time.monotonic()-started
        execution['rss_samples'] = samples
        values = [rss for sample in samples for rss in sample['processes'].values()]
        execution['sampled_peak_rss_bytes'] = max(values) if values else None
        save(directory/'execution.json', execution)
        if app is not None:
            # Launch Services apps are outside the launcher's process group.
            stop_owned_gui(binary, marker)
    if interrupted:
        raise KeyboardInterrupt(execution['error'])
    return execution


def validate_report(report: dict, fixture: dict, spectrum: Path, gui: bool,
                    wall_seconds: float | None = None) -> dict:
    expected_mode = 'gui-benchmark' if gui else 'headless'
    if report.get('mode') != expected_mode or (gui and report.get('passed') is not True):
        raise ValueError(f'Expected successful {expected_mode} result: {report.get("error")}')
    if report.get('initial_sha256') != fixture['initial_sha256']:
        raise ValueError('Imported initial field hash differs')
    for key in ('resolution', 'padded_resolution', 'reynolds', 'k0', 'seed'):
        close(nested(report, f'config.{key}', key), fixture[key], key)
    close(nested(report, 'config.cfl', 'cfl'), 3, 'cfl')
    for key, expected in [('integrator', 'etdrk4'), ('operator', 'stress')]:
        if nested(report, f'config.{key}', key) != expected:
            raise ValueError(f'Reported {key} differs from {expected}')
    if 'fixed_dt' not in report.get('config', {}) or report['config']['fixed_dt'] is not None:
        raise ValueError('Expected explicit adaptive-timestep configuration')
    stats = report.get('stats', {})
    if nested(stats, 'is_finite', 'finite') is not True:
        raise ValueError('Missing finite final statistics')
    simulation_time = float(stats.get('simulation_time', math.nan))
    if not math.isfinite(simulation_time) or simulation_time <= 0:
        raise ValueError('Missing finite positive completed physical endpoint')
    endpoint_key = 'until' if gui else 'target_time'
    if wall_seconds is not None:
        close(report.get('target_wall_seconds'), wall_seconds, 'requested wall duration', tolerance=0)
        if endpoint_key not in report or report[endpoint_key] is not None:
            raise ValueError('Wall-time mode must report a null physical target')
    else:
        close(simulation_time, fixture['endpoint'], 'completed endpoint')
        close(report.get(endpoint_key), fixture['endpoint'], 'requested endpoint')
    tau = float(stats.get('initial_turnover_time', math.nan))
    if not math.isfinite(tau) or tau <= 0:
        raise ValueError('Missing finite positive initial turnover time')
    fixture_tau = fixture['invariants']['initial_turnover_time']
    if not gui:
        close(nested(report, 'initial_stats.initial_turnover_time'), tau,
              'initial/final turnover-time invariant', tolerance=0)
    metric = 'initial_turnovers_per_second' if gui else 'turnovers_per_s'
    sim_metric = 'simulation_time_per_second' if gui else 'simtime_per_s'
    row = dict(initial_turnover_time_reported=tau, initial_turnover_time_fixture=fixture_tau,
               initial_turnover_time_relative_difference=tau/fixture_tau-1)
    for name, key in [('seconds', 'seconds'), ('throughput', metric), ('simtime_per_s', sim_metric)]:
        value = float(report.get(key, math.nan))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'Missing finite positive {key}')
        row[name] = value
    if wall_seconds is not None:
        if row['seconds'] < wall_seconds:
            raise ValueError(f'Timed run completed before {wall_seconds:g} active wall seconds')
        row.update(target_wall_seconds=wall_seconds,
                   wall_overshoot_seconds=row['seconds']-wall_seconds,
                   wall_overshoot_fraction=(row['seconds']-wall_seconds)/wall_seconds)
        if report.get('final_batch_seconds') is not None:
            final_batch = float(report['final_batch_seconds'])
            if not math.isfinite(final_batch) or final_batch < 0:
                raise ValueError('Invalid measured final GPU batch duration')
            row.update(final_batch_seconds=final_batch,
                       wall_overshoot_beyond_final_batch_seconds=max(
                           0, row['wall_overshoot_seconds']-final_batch))
            if report.get('final_inspection_seconds') is not None:
                inspection = float(report['final_inspection_seconds'])
                if not math.isfinite(inspection) or inspection < 0:
                    raise ValueError('Invalid final inspection duration')
                row.update(final_inspection_seconds=inspection,
                           wall_overshoot_beyond_final_batch_and_inspection_seconds=max(
                               0, row['wall_overshoot_seconds']-final_batch-inspection))
            # advanceSync duration does not include final inspection, and GUI
            # presentation may finish afterward. Preserve the residual rather
            # than reject valid completion using an invented latency threshold.
    close(row['simtime_per_s'], stats['simulation_time']/row['seconds'], 'simulation time rate')
    close(row['throughput'], stats['simulation_time']/stats['initial_turnover_time']/row['seconds'],
          'initial turnover rate')
    row.update(simulation_time=simulation_time, endpoint=simulation_time,
               completed_initial_turnovers=simulation_time/tau, steps=stats.get('steps'),
               allocated_metal_bytes=nested(report, 'hardware.allocated_metal_bytes'),
               owned_solver_buffer_bytes=nested(report, 'hardware.owned_solver_buffer_bytes',
                                                'stats.allocated_bytes'))
    n = fixture['resolution']
    if not spectrum.is_file() or spectrum.stat().st_size != n*n*8:
        raise ValueError('Final spectrum must contain N x N complex64 values')
    if not np.isfinite(np.memmap(spectrum, dtype='<c8', mode='r')).all():
        raise ValueError('Final spectrum contains nonfinite values')
    if not gui and report.get('spectrum_normalization') != 'fourier-series':
        raise ValueError('Unexpected spectrum normalization')
    if gui:
        close(report.get('gui_warmup_seconds'), 2, 'GUI warmup')
        if report.get('batch_policy') != 'adaptive-8ms-1to32' or report['config'].get('batch') is not None:
            raise ValueError('GUI benchmark did not use the normal adaptive batch policy')
        if report.get('controls', {}).get('reset_at_zero') is not True:
            raise ValueError('Missing warmup/reset evidence')
        canvas, presentation = report.get('canvas', {}), report.get('presentation', {})
        for axis in ('width', 'height'):
            value = canvas.get(f'{axis}_points')
            if value is None or abs(float(value)-640) > 1:
                raise ValueError(f'Canvas {axis} differs from 640 points')
        if canvas.get('window_visible') is not True:
            raise ValueError('GUI window was not visible')
        for key, value in [('display', 'vorticity'), ('palette', 'ember'), ('flow_overlay', False)]:
            if report.get(key) != value:
                raise ValueError(f'Presentation setting {key} differs')
        close(report.get('exposure'), 1.2, 'exposure')
        close(presentation.get('display_target_hz'), 60, 'display target')
        for key in ('presented_frames_per_second', 'p95_frame_interval_ms', 'longest_frame_interval_ms'):
            value = float(presentation.get(key, math.nan))
            if not math.isfinite(value) or value < 0 or (key == 'presented_frames_per_second' and value == 0):
                raise ValueError(f'Missing valid presentation metric {key}')
            row[key] = value
        row['canvas'] = canvas
    else:
        close(report.get('warmup_steps'), 8, 'warmup steps', tolerance=0)
        close(report['config'].get('batch'), 8, 'headless batch', tolerance=0)
        close(report.get('snapshot_hz'), 0, 'snapshot frequency', tolerance=0)
        close(nested(report, 'initial_stats.simulation_time'), 0, 'reset time', tolerance=0)
        close(nested(report, 'initial_stats.steps'), 0, 'reset steps', tolerance=0)
    return row


def distribution(values: list[float]) -> dict | None:
    if not values:
        return None
    median = statistics.median(values)
    mad = statistics.median(abs(value-median) for value in values)
    return dict(count=len(values), median=median, mad=mad,
                relative_mad=mad/median if median else None, min=min(values), max=max(values),
                values=values)


def summarize(fixtures: list[dict], rows: list[dict], args) -> dict:
    cases = []
    keys = ('throughput', 'simtime_per_s', 'seconds', 'simulation_time',
            'completed_initial_turnovers', 'wall_overshoot_seconds', 'wall_overshoot_fraction',
            'final_batch_seconds', 'wall_overshoot_beyond_final_batch_seconds',
            'final_inspection_seconds', 'wall_overshoot_beyond_final_batch_and_inspection_seconds',
            'sampled_peak_rss_bytes',
            'allocated_metal_bytes', 'owned_solver_buffer_bytes',
            'presented_frames_per_second', 'p95_frame_interval_ms', 'longest_frame_interval_ms')
    for fixture in fixtures:
        case_rows = [row for row in rows if row['fixture'] == fixture['id']]
        case = dict(fixture=fixture['id'], resolution=fixture['resolution'], builds={})
        valid = [row for row in case_rows if row.get('valid')]
        for build in ('baseline', 'candidate'):
            runs = [row for row in valid if row['build'] == build]
            case['builds'][build] = {key: distribution([run[key] for run in runs if run.get(key) is not None])
                                     for key in keys}
        baseline = case['builds']['baseline']['throughput']
        candidate = case['builds']['candidate']['throughput']
        complete = all(item is not None and item['count'] == args.runs for item in (baseline, candidate))
        case.update(complete=complete, failed_runs=[row['id'] for row in case_rows if not row.get('valid')])
        if baseline and candidate:
            ratio = candidate['median']/baseline['median']
            noise = 3*math.hypot(baseline['relative_mad'], candidate['relative_mad'])
            case.update(median_ratio=ratio, gain_percent=100*(ratio-1), noise_threshold=noise,
                        gain_exceeds_noise_and_1_percent=complete and args.runs >= (3 if args.gui else 5) and ratio > max(1.01, 1 + noise),
                        regression_within_2_percent=complete and ratio >= 0.98)
        pairs = []
        for repeat in range(1, args.runs+1):
            pair = {row['build']: row for row in valid if row['repeat'] == repeat}
            if set(pair) == {'baseline', 'candidate'}:
                same_endpoint = (pair['baseline']['simulation_time'] == pair['candidate']['simulation_time']
                                 and pair['baseline']['steps'] == pair['candidate']['steps'])
                pairs.append(dict(repeat=repeat, same_completed_endpoint_and_steps=same_endpoint,
                                  bitwise_equal=(pair['baseline']['spectrum_sha256'] ==
                                                 pair['candidate']['spectrum_sha256']) if same_endpoint else None,
                                  baseline_endpoint=pair['baseline']['simulation_time'],
                                  candidate_endpoint=pair['candidate']['simulation_time'],
                                  baseline_sha256=pair['baseline']['spectrum_sha256'],
                                  candidate_sha256=pair['candidate']['spectrum_sha256']))
        case['spectrum_pairs'] = pairs
        comparable = [pair for pair in pairs if pair['same_completed_endpoint_and_steps']]
        case['comparable_spectrum_pairs'] = len(comparable)
        case['all_completed_pairs_bitwise_equal'] = (all(pair['bitwise_equal'] for pair in pairs)
                                                    if pairs and len(comparable) == len(pairs) else None)
        cases.append(case)
    scores = {}
    for n in args.resolutions:
        group = [case for case in cases if case['resolution'] == n]
        complete = bool(group) and all(case['complete'] and 'median_ratio' in case for case in group)
        scores[str(n)] = dict(complete=complete, geometric_mean_ratio=math.exp(
            statistics.mean(math.log(case['median_ratio']) for case in group)) if complete else None)
    return dict(schema_version=2, mode='gui' if args.gui else 'headless',
                timing_mode='wall-seconds' if args.wall_seconds is not None else 'turnovers',
                target_wall_seconds=args.wall_seconds, target_turnovers=args.turnovers,
                metric='initial_turnovers_per_second' if args.gui else 'turnovers_per_s',
                cases=cases, by_resolution=scores, requested_runs_per_build=args.runs,
                all_runs_complete=len(rows) == len(fixtures)*2*args.runs and all(row.get('valid') for row in rows),
                promotion_accepted=False,
                limitations=[
                    'Performance and output screening only; independent accuracy/reference gates have not been evaluated.',
                    'Finite output and bitwise agreement do not establish numerical accuracy.',
                    'Promotion requires a >1% gain beyond combined noise at either N1024 or N2048, the N2048 regression gate, independent accuracy at deterministic matched checkpoints within the 20-second validation budget, and presentation review.',
                    'RSS is sampled once per second and is a lower bound on peak resident memory; Metal allocation counters are endpoint observations.',
                    'No other processes were suspended; inspect recorded process/power/thermal state for interference.',
                    'Wall-time runs may finish at different physical endpoints. Independent accuracy checks use matched checkpoints; disclose their coverage without claiming accuracy at untested timing endpoints.',
                    'Wall overshoot and any measured final-batch duration are retained explicitly. Residual overshoot can include final inspection/presentation; no arbitrary upper latency cutoff is applied.',
                    'Reported throughput uses solver-reduced initial tau; its difference from fixture tau is retained per run. Explicit --turnovers mode uses a common --until endpoint from serialized-fixture tau.',
                ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--gui', action='store_true', help='Both paths must be .app bundles; launch visible benchmarks')
    parser.add_argument('--resolutions', nargs='+', type=int, default=[1024, 2048])
    parser.add_argument('--fixtures', nargs='+', type=fixture_spec, default=[(4, 10000.0)])
    parser.add_argument('--seeds', nargs='+', type=int, default=[2])
    parser.add_argument('--runs', type=int, help='Interleaved pairs per case (default: 3 live, 5 headless)')
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument('--wall-seconds', type=positive,
                        help='Minimum active wall duration per run, including final GPU completion (default 20)')
    timing.add_argument('--turnovers', type=positive,
                        help='Legacy mode: multiply fixture initial tau to set a common --until endpoint')
    parser.add_argument('--fixture-root', type=Path, help='Copy matching CASE/initial.f32 plus fixture.json; generate missing cases')
    parser.add_argument('--timeout', type=positive, default=1200.0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.runs is None:
        args.runs = 3 if args.gui else 5
    if args.wall_seconds is None and args.turnovers is None:
        args.wall_seconds = 20.0
    if args.runs < 1 or any(n < 16 or n > 4096 or n % 2 for n in args.resolutions):
        parser.error('Runs must be positive; resolutions must be even and in 16...4096')
    if any(seed < 0 or seed >= 2**64 for seed in args.seeds):
        parser.error('Seeds must fit UInt64')
    for name in ('resolutions', 'fixtures', 'seeds'):
        if len(set(getattr(args, name))) != len(getattr(args, name)):
            parser.error(f'--{name} must not contain duplicates')
    paths = {name: getattr(args, name).expanduser().resolve() for name in ('baseline', 'candidate')}
    if args.gui and any(path.suffix != '.app' for path in paths.values()):
        parser.error('--gui requires .app bundles for baseline and candidate')
    binaries = {name: executable(path) for name, path in paths.items()}
    hashes = {name: digest(path) for name, path in binaries.items()}
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory must be new or empty; existing evidence is never overwritten')
    output.mkdir(parents=True, exist_ok=True)
    fixture_root = args.fixture_root.expanduser().resolve() if args.fixture_root else None
    if fixture_root is not None and not fixture_root.is_dir():
        parser.error('--fixture-root must be an existing directory')
    metadata = dict(started_utc=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
                    python=sys.version, numpy=np.__version__, harness_sha256=digest(Path(__file__)),
                    executables={name: dict(path=str(path), sha256=hashes[name]) for name, path in binaries.items()},
                    options={key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()})
    for key, command in [('hardware', ['system_profiler', 'SPHardwareDataType', 'SPDisplaysDataType', '-json']),
                         ('power_start', ['pmset', '-g', 'batt']), ('thermal_start', ['pmset', '-g', 'therm']),
                         ('processes_start', ['ps', '-axo', 'pid=,stat=,%cpu=,comm=']),
                         ('workspace_git_head', ['git', 'rev-parse', 'HEAD']),
                         ('workspace_git_status', ['git', 'status', '--short'])]:
        metadata[key] = capture(command)
    save(output/'metadata.json', metadata)
    fixtures, rows = [], []
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        # Finish and hash every input before launching either build.
        for n in args.resolutions:
            for k0, reynolds in args.fixtures:
                for seed in args.seeds:
                    fixtures.append(prepare_fixture(fixture_root, output, n, k0, reynolds, seed, args.turnovers))
        plan = []
        for repeat in range(1, args.runs+1):
            for index, fixture in enumerate(fixtures):
                order = ('baseline', 'candidate') if (repeat+index) % 2 else ('candidate', 'baseline')
                for build in order:
                    plan.append(dict(id=f"{fixture['id']}/r{repeat}-{build}",
                                     fixture=fixture['id'], repeat=repeat, build=build))
        save(output/'manifest.json', dict(fixtures=fixtures, runs=plan, executable_sha256=hashes,
                                         gui=args.gui, integrator='etdrk4', operator='stress', cfl=3,
                                         target_wall_seconds=args.wall_seconds, target_turnovers=args.turnovers))
        fixture_map = {fixture['id']: fixture for fixture in fixtures}
        for index, planned in enumerate(plan):
            row = dict(planned, valid=False)
            rows.append(row)
            directory = output/planned['id']
            directory.mkdir(parents=True)
            fixture, build = fixture_map[planned['fixture']], planned['build']
            binary, spectrum = binaries[build], directory/'spectrum.c64'
            print(f"[{index+1}/{len(plan)}] {planned['id']}", flush=True)
            try:
                if digest(binary) != hashes[build] or digest(Path(fixture['initial_file'])) != fixture['initial_sha256']:
                    raise RuntimeError('Executable or frozen input changed during comparison')
                command = command_for(args, binary, fixture, directory)
                fingerprint = dict(executable_sha256=hashes[build], initial_sha256=fixture['initial_sha256'],
                                   command_sha256=hashlib.sha256(json.dumps(command).encode()).hexdigest())
                save(directory/'fingerprint.json', fingerprint)
                execution = run_process(command, directory, binary, paths[build] if args.gui else None, args.timeout)
                row.update(driver_wall_seconds=execution['driver_wall_seconds'],
                           sampled_peak_rss_bytes=execution['sampled_peak_rss_bytes'])
                if execution.get('error') or execution.get('returncode') != 0:
                    raise ValueError(execution.get('error', f"Process exited {execution.get('returncode')}"))
                result_file = directory/'result.json'
                if not result_file.exists():
                    # Retain the original stdout even for compatible CLI adapters.
                    save(result_file, json.loads((directory/'stdout.txt').read_text()))
                report = json.loads(result_file.read_text())
                row.update(validate_report(report, fixture, spectrum, args.gui, args.wall_seconds))
                if digest(binary) != hashes[build] or digest(Path(fixture['initial_file'])) != fixture['initial_sha256']:
                    raise RuntimeError('Executable or frozen input changed during the run')
                row.update(valid=True, spectrum_sha256=digest(spectrum), result_sha256=digest(result_file),
                           spectrum_file=str(spectrum), result_file=str(result_file))
                save(directory/'fingerprint.json', {**fingerprint, 'spectrum_sha256': row['spectrum_sha256'],
                                                    'result_sha256': row['result_sha256']})
                print(f"  {row['throughput']:.6g} initial turnovers/s", flush=True)
            except KeyboardInterrupt:
                row['error'] = 'Interrupted'
                raise
            except RuntimeError as error:
                # Identity changes or failed cleanup invalidate further sequential runs.
                row['error'] = str(error)
                raise
            except Exception as error:
                row['error'] = str(error)
                print(f'  FAILED: {error}', file=sys.stderr, flush=True)
            finally:
                save(directory/'screening.json', row)
                save(output/'runs.json', rows)
                save(output/'summary.json', summarize(fixtures, rows, args))
    except (Exception, KeyboardInterrupt) as error:
        metadata['error'] = str(error) or type(error).__name__
        print(metadata['error'], file=sys.stderr, flush=True)
    finally:
        metadata.update(finished_utc=datetime.now(timezone.utc).isoformat(),
                        thermal_end=capture(['pmset', '-g', 'therm']), power_end=capture(['pmset', '-g', 'batt']))
        save(output/'metadata.json', metadata)
        save(output/'runs.json', rows)
        summary = summarize(fixtures, rows, args)
        summary['execution_error'] = metadata.get('error')
        save(output/'summary.json', summary)
    return 0 if summary['all_runs_complete'] and not metadata.get('error') else 1


if __name__ == '__main__':
    raise SystemExit(main())
