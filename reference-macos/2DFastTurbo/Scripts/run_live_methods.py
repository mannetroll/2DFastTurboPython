#!/usr/bin/env python3
"""Run 2DFastTurbo time-stepping methods live on one N1024 turbulence fixture.

Existing solver processes are suspended reversibly during measurement and resumed
in finally. Each native app owns its window and produces its own completed-work
clock, presentation timestamps, imported-field hash, and final spectrum.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import time

FIXTURE = 'n1024-k4-re50000-seed2'
SOLVERS = {'2DFastTurbo', 'MLXAstra', 'Metal2DTurbo'}


def save(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def executable(app):
    with (app / 'Contents/Info.plist').open('rb') as f:
        name = plistlib.load(f)['CFBundleExecutable']
    return app / 'Contents/MacOS' / name


def solver_processes():
    result = []
    output = subprocess.check_output(['ps', '-axo', 'pid=,stat=,comm='], text=True)
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3: continue
        pid, state, command = fields
        if '/Contents/MacOS/' in command and Path(command).name in SOLVERS:
            result.append({'pid': int(pid), 'state': state, 'executable': command})
    return result


def owned_run_processes(output):
    found = []
    listing = subprocess.check_output(['ps', '-axo', 'pid=,args='], text=True)
    for line in listing.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and '--gui-benchmark' in fields[1] and str(output) in fields[1]:
            if '/Contents/MacOS/' in fields[1]: found.append(int(fields[0]))
    return found


def stop_owned_run(output):
    pids = owned_run_processes(output)
    for pid in pids:
        try: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass
    deadline = time.monotonic() + 3
    while pids and time.monotonic() < deadline:
        pids = list(set(pids).intersection(owned_run_processes(output)))
        if pids: time.sleep(0.1)
    for pid in pids:
        try: os.kill(pid, signal.SIGKILL)
        except ProcessLookupError: pass


def validate_result(report, run, initial_sha, endpoint, spectrum):
    identifier = run['id']
    if report.get('passed') is not True or report.get('mode') != 'gui-benchmark':
        raise RuntimeError(f'{identifier}: missing successful live benchmark evidence: {report.get("error")}')
    if report.get('initial_sha256') != initial_sha:
        raise RuntimeError(f'{identifier}: imported initial field hash differs')
    config = report.get('config', {})
    for key, value in {'resolution': 1024, 'padded_resolution': 1536, 'reynolds': 50000,
                       'k0': 4, 'seed': 2, 'cfl': run['cfl']}.items():
        actual = float(config.get(key, report.get(key, math.nan)))
        if not math.isclose(actual, value, rel_tol=2e-7, abs_tol=2e-7):
            raise RuntimeError(f'{identifier}: {key} expected {value}, reported {actual}')
    for key in ['integrator', 'operator']:
        if config.get(key, report.get(key)) != run[key]:
            raise RuntimeError(f'{identifier}: {key} differs from the requested method')
    stats = report.get('stats', {})
    if not math.isclose(float(stats.get('simulation_time', math.nan)), endpoint, rel_tol=2e-7, abs_tol=2e-7):
        raise RuntimeError(f'{identifier}: completed simulation time differs from the endpoint')
    if not spectrum.is_file() or spectrum.stat().st_size != 1024 * 1024 * 8:
        raise RuntimeError(f'{identifier}: final spectrum must contain N x N complex64 values')
    canvas, presentation = report.get('canvas', {}), report.get('presentation', {})
    for axis in ['width', 'height']:
        size = float(canvas.get(f'{axis}_points', presentation.get(f'canvas_{axis}_points', math.nan)))
        if not math.isclose(size, 640, rel_tol=0, abs_tol=1):
            raise RuntimeError(f'{identifier}: canvas {axis} differs: {size}')
    if canvas.get('window_visible', presentation.get('window_visible')) is not True:
        raise RuntimeError(f'{identifier}: benchmark window was not visible')
    for key, expected in [('display', 'vorticity'), ('palette', 'ember'), ('flow_overlay', False)]:
        if report.get(key) != expected:
            raise RuntimeError(f'{identifier}: {key} differs from the common appearance')
    if not math.isclose(float(report.get('exposure', math.nan)), 1.2, rel_tol=0, abs_tol=1e-6):
        raise RuntimeError(f'{identifier}: exposure differs from the common appearance')
    for value in [report.get('seconds'), report.get('simulation_time_per_second'),
                  report.get('steps_per_second'), presentation.get('presented_frames_per_second')]:
        if value is None or not math.isfinite(float(value)) or float(value) <= 0:
            raise RuntimeError(f'{identifier}: missing finite positive completed-work/presentation rate')


def resume_solvers(paused):
    restored, errors = [], []
    try:
        active = {process['pid']: process for process in solver_processes()}
    except Exception as error:
        # A failed process listing must not strand applications we suspended.
        active = None
        errors.append(f'Could not verify process identities during restoration: {error}')
    for process in paused:
        pid = process['pid']
        if active is not None and (pid not in active or active[pid]['executable'] != process['executable']):
            continue
        try:
            os.kill(pid, signal.SIGCONT)
            restored.append(pid)
        except ProcessLookupError: pass
        except OSError as error: errors.append(f'Could not resume PID {pid}: {error}')
    return restored, errors


def suspend_competitors(paused):
    known = {(process['pid'], process['executable']) for process in paused}
    count = 0
    for process in solver_processes():
        if 'T' in process['state']: continue
        try:
            os.kill(process['pid'], signal.SIGSTOP)
            key = process['pid'], process['executable']
            if key not in known:
                paused.append(process)
                known.add(key)
            count += 1
        except ProcessLookupError: pass
    return count


def running_competitors(output):
    owned = set(owned_run_processes(output))
    return [process for process in solver_processes()
            if 'T' not in process['state'] and process['pid'] not in owned]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--app', '--turbo-app', dest='turbo_app', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--only', help='Comma-separated case IDs for a smoke run')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0: raise ValueError('--timeout must be positive and finite')
    root = Path(__file__).resolve().parents[1]
    evidence = args.output.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    reference = root / 'Benchmarks/full-comparison-2026-09-09'
    fixture = json.loads((reference / FIXTURE / 'fixture.json').read_text())
    initial = reference / FIXTURE / 'initial.f32'
    initial_sha = digest(initial)
    if initial_sha != fixture['initial_sha256']: raise ValueError('Reference initial field hash differs from its metadata')
    endpoint = 2 * fixture['invariants']['initial_turnover_time']
    apps = {'turbo': args.turbo_app.resolve()}
    cases = []
    for method, short, cfl in [('etdrk4', 'etd', 3), ('imexRK3', 'imex', 1), ('ifHeunRK3', 'if', 1)]:
        cases.append(dict(case_id=f'turbo-{short}-stress', app='turbo', label='2DFastTurbo',
                          integrator=method, operator='stress', cfl=cfl))
    if args.only:
        selected = set(args.only.split(','))
        if not selected.issubset({c['case_id'] for c in cases}): raise ValueError('Unknown case ID')
        cases = [c for c in cases if c['case_id'] in selected]
    if args.runs < 1: raise ValueError('--runs must be positive')
    exe_hashes = {kind: digest(executable(apps[kind])) for kind in {c['app'] for c in cases}}
    runs = []
    for repeat in range(args.runs):
        offset = repeat % len(cases)
        for case in cases[offset:] + cases[:offset]:
            run_id = f"{case['case_id']}-r{repeat+1}"
            runs.append({**case, 'id': run_id, 'repeat': repeat,
                         'result_file': f'{run_id}/result.json', 'spectrum_file': f'{run_id}/spectrum.c64'})
    manifest = dict(fixture=FIXTURE, endpoint=endpoint, initial_file=str(initial),
                    initial_sha256=initial_sha,
                    reference_directory=str(reference), expected_runs=args.runs,
                    appearance=dict(display='vorticity', palette='ember', exposure=1.2,
                                    canvas_points=640, display_target_hz=60, flow_overlay=False),
                    executable_sha256=exe_hashes, runs=runs)
    previous = evidence / 'manifest.json'
    if previous.exists() and json.loads(previous.read_text()) != manifest:
        raise ValueError('Manifest changed; use a new output directory for new configurations or executables')
    save(previous, manifest)
    paused = []
    metadata = dict(started_utc=datetime.now(timezone.utc).isoformat(), apps={k:str(v) for k,v in apps.items()},
                    executable_sha256=exe_hashes, initial_sha256=initial_sha,
                    macos=subprocess.check_output(['sw_vers', '-productVersion'], text=True).strip(),
                    power=subprocess.check_output(['pmset', '-g', 'batt'], text=True),
                    thermal_start=subprocess.check_output(['pmset', '-g', 'therm'], text=True),
                    suspended_solver_processes=paused, completed=[])
    current = None
    def interrupted(signum, frame): raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        suspend_competitors(paused)
        save(evidence / 'metadata.json', metadata)
        if paused:
            print(f'Suspended {len(paused)} competing solver instance(s); will resume on completion.', flush=True)
            time.sleep(1)
        for index, run in enumerate(runs):
            if suspend_competitors(paused):
                save(evidence / 'metadata.json', metadata)
                time.sleep(1)
            app = apps[run['app']]
            if digest(executable(app)) != exe_hashes[run['app']]:
                raise RuntimeError('Benchmark executable changed during the matrix')
            directory = evidence / run['id']
            directory.mkdir(parents=True, exist_ok=True)
            result = evidence / run['result_file']
            spectrum = evidence / run['spectrum_file']
            command_args = ['--gui-benchmark', '--resolution', '1024', '--reynolds', '50000',
                            '--k0', '4', '--seed', '2', '--integrator', run['integrator'],
                            '--operator', run['operator'], '--cfl', str(run['cfl']),
                            '--initial', str(initial), '--until', repr(endpoint),
                            '--gui-warmup-seconds', '2', '--canvas-size', '640',
                            '--display', 'vorticity', '--palette', 'ember', '--exposure', '1.2',
                            '--spectrum', str(spectrum), '--output', str(result)]
            fingerprint = dict(executable_sha256=exe_hashes[run['app']], initial_sha256=initial_sha,
                               command_sha256=hashlib.sha256(json.dumps(command_args).encode()).hexdigest())
            if args.resume and result.exists() and spectrum.exists():
                old = json.loads(result.read_text())
                recorded = json.loads((directory / 'fingerprint.json').read_text())
                expected = {**fingerprint, 'result_sha256': digest(result), 'spectrum_sha256': digest(spectrum)}
                if recorded != expected:
                    raise RuntimeError(f'{run["id"]}: completed run fingerprint differs; use a new output directory')
                validate_result(old, run, initial_sha, endpoint, spectrum)
                print(f'[{index+1}/{len(runs)}] Reusing {run["id"]}', flush=True)
                metadata['completed'].append(run['id'])
                save(evidence / 'metadata.json', metadata)
                continue
            if result.exists() or spectrum.exists():
                raise RuntimeError(f'Result already exists for {run["id"]}; choose --resume or a new directory')
            command = ['open', '-n', '-W', '--stdout', str(directory/'stdout.txt'),
                       '--stderr', str(directory/'stderr.txt'), str(app), '--args', *command_args]
            save(directory / 'command.json', command)
            save(directory / 'fingerprint.json', fingerprint)
            print(f'[{index+1}/{len(runs)}] {run["id"]}', flush=True)
            started = time.monotonic()
            last_process_check = started
            with (directory / 'launch.log').open('w') as launch_log:
                current = subprocess.Popen(command, stdout=launch_log, stderr=subprocess.STDOUT)
                while current.poll() is None:
                    if time.monotonic() - started > args.timeout:
                        stop_owned_run(result)
                        current.terminate()
                        raise TimeoutError(f'{run["id"]} exceeded {args.timeout}s')
                    if time.monotonic() - last_process_check >= 1:
                        competitors = running_competitors(result)
                        if competitors:
                            metadata.setdefault('contaminated_runs', []).append(
                                dict(id=run['id'], competing_processes=competitors))
                            raise RuntimeError(f'{run["id"]}: another solver appeared during the live run; timing rejected')
                        last_process_check = time.monotonic()
                    time.sleep(0.5)
                if current.returncode != 0:
                    raise RuntimeError(f'Launch failed for {run["id"]}: {current.returncode}')
            current = None
            competitors = running_competitors(result)
            if competitors:
                metadata.setdefault('contaminated_runs', []).append(
                    dict(id=run['id'], competing_processes=competitors))
                raise RuntimeError(f'{run["id"]}: competing solver found at completion; timing rejected')
            report = json.loads(result.read_text())
            validate_result(report, run, initial_sha, endpoint, spectrum)
            save(directory / 'fingerprint.json', {**fingerprint, 'result_sha256': digest(result),
                                                  'spectrum_sha256': digest(spectrum)})
            rate = report['simulation_time_per_second']
            print(f'  {rate:.6f} simulated time/wall s; {report["steps_per_second"]:.2f} steps/s', flush=True)
            metadata['completed'].append(run['id'])
            save(evidence / 'metadata.json', metadata)
            time.sleep(1)
        metadata['finished_utc'] = datetime.now(timezone.utc).isoformat()
    except BaseException as error:
        metadata['interrupted_utc'] = datetime.now(timezone.utc).isoformat()
        metadata['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        cleanup_errors = []
        try:
            if current is not None:
                stop_owned_run(result)
                current.terminate()
                try: current.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    current.kill()
                    current.wait(timeout=3)
        except Exception as error: cleanup_errors.append(f'Could not stop owned benchmark: {error}')
        finally:
            restored, errors = resume_solvers(paused)
            metadata['restored_solver_processes'] = restored
            cleanup_errors.extend(errors)
        try: metadata['thermal_end'] = subprocess.check_output(['pmset', '-g', 'therm'], text=True)
        except Exception as error: cleanup_errors.append(f'Could not collect final thermal state: {error}')
        if cleanup_errors:
            metadata['cleanup_errors'] = cleanup_errors
            for error in cleanup_errors: print(error, file=sys.stderr, flush=True)
        save(evidence / 'metadata.json', metadata)


if __name__ == '__main__': main()
