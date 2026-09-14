"""Run the authoritative project's independent FP64 acceptance suite on CUDA."""
import argparse
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root/'windows/.python'))
sys.path.insert(0, str(root/'reference-macos/2DFastTurbo/Scripts'))
from validate import validate_gpu, METHODS

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app', type=Path, default=root/'windows/build/2DFastTurbo.exe')
    p.add_argument('--output', type=Path, default=root/'windows/artifacts/numerics')
    p.add_argument('--resolutions', type=int, nargs='+', default=[32, 34])
    args = p.parse_args()
    reports = []
    for n in args.resolutions:
        for form in ('stress', 'advective'):
            reports.append(validate_gpu(args.app.resolve(), (args.output/f'n{n}-{form}').resolve(), n, METHODS, form))
    summary = {'all_passed': all(r['all_passed'] for r in reports),
               'reference': 'Unmodified macOS Scripts/validate.py: independent FP64 advective RK4',
               'direct_metal_capture_available': False,
               'groups': [{'resolution': r['resolution'], 'operator': r['operator'],
                           'passed': r['all_passed'], 'tests': len(r['tests'])} for r in reports]}
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    return 0 if summary['all_passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
