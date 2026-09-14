"""Preserve compact local test/benchmark evidence; never invent missing captures."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import zipfile

root = Path(__file__).resolve().parents[2]
raw = root/'windows/artifacts'
out = root/'windows/benchmarks/2026-09-13'
out.mkdir(parents=True, exist_ok=True)
files = {
    'gui-check.json': raw/'gui-check.json',
    'native-initialization.json': raw/'native/summary.json',
    'render-validation.json': raw/'colors/validation.json',
    'execution-comparison.json': raw/'execution-comparison.json',
    'runs.json': raw/'benchmark/runs.json',
    'runs.csv': raw/'benchmark/runs.csv',
    'windows-gui.png': raw/'benchmark/windows-gui.png',
    'core-tests.log': root/'windows/build/Testing/Temporary/LastTest.log',
}
for directory in (raw/'numerics', raw/'numerics-n128'):
    for path in directory.glob('n*/validation.json'):
        files[f'{path.parent.name}.json'] = path
for mode in ('headless', 'gui'):
    for run in range(1, 4):
        files[f'{mode}-{run}.json'] = raw/f'benchmark/{mode}-{run}.json'
for name, source in files.items():
    shutil.copyfile(source, out/name)
capture = raw/'captures/windows'
with zipfile.ZipFile(out/'windows-captures.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(capture.rglob('*')):
        if path.is_file():
            archive.write(path, path.relative_to(capture))
files['windows-captures.zip'] = out/'windows-captures.zip'

numerical = [json.loads((out/name).read_text()) for name in files
             if name.startswith('n') and ('-stress.' in name or '-advective.' in name)]
assert len(numerical) == 6 and all(group['all_passed'] for group in numerical)
native = json.loads((out/'native-initialization.json').read_text())
gui = json.loads((out/'gui-check.json').read_text())
colors = json.loads((out/'render-validation.json').read_text())
execution = json.loads((out/'execution-comparison.json').read_text())
assert all(x['passed'] for x in (native, gui, colors, execution))
assert 'Test Passed.' in (out/'core-tests.log').read_text()
runs = json.loads((out/'runs.json').read_text(encoding='utf-8-sig'))
performance = {}
for mode in ('headless', 'gui'):
    records = [r for r in runs if r['mode'] == mode]
    rates = [r['R_turbo'] for r in records]
    performance[mode] = {'runs': len(records), 'mean_R_turbo': statistics.mean(rates),
                         'minimum_R_turbo': min(rates), 'maximum_R_turbo': max(rates),
                         'mean_steps_per_s': statistics.mean(r['steps_per_s'] for r in records),
                         'mean_presented_fps': statistics.mean(r['presented_fps'] for r in records)}

summary = {'recorded_utc': datetime.now(timezone.utc).isoformat(),
           'machine': {'os': 'Windows 10 Pro 10.0.19045', 'gpu': 'RTX 3090 24 GiB',
                       'driver': '616.56 WDDM', 'cuda_toolkit': '13.0.88',
                       'msvc': '19.44.35221', 'display': 'SDR, 640px canvas, 100% DPI'},
           'numerical_checks': sum(len(g['tests']) for g in numerical),
           'numerical_passed': True, 'gui_checks': len(gui['checks']),
           'gui_passed': gui['passed'], 'render_checks': len(colors['checks']),
           'render_maximum_byte_error': max(c['maximum_byte_error'] for c in colors['checks']),
           'native_resolutions': [c['n'] for c in native['checks']],
           'native_max_relative_l2': max(c['native_relative_l2'] for c in native['checks']),
           'gui_headless_bitwise_identical': execution['bitwise_identical'],
           'direct_metal_comparison': 'pending: no Metal captures available',
           'performance': performance,
           'current_executable_sha256': hashlib.sha256((root/'windows/build/2DFastTurbo.exe').read_bytes()).hexdigest(),
           'source_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for folder in ('windows/src', 'reference-macos/2DFastTurbo/Sources')
                             for p in sorted((root/folder).rglob('*')) if p.is_file()},
           'evidence_sha256': {name: hashlib.sha256((out/name).read_bytes()).hexdigest() for name in files}}
(out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
print(json.dumps({k: v for k, v in summary.items() if not k.endswith('sha256')}, indent=2))
