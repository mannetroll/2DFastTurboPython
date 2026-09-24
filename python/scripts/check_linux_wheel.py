"""Run a Linux wheel away from sources and verify CUDA loads from that wheel."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', required=True, type=Path)
    parser.add_argument('--ptx', action='store_true', help='Force embedded PTX execution')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='fastturbo-wheel-') as directory:
        root = Path(directory)
        with zipfile.ZipFile(args.wheel) as archive:
            assert all((root / name).resolve().is_relative_to(root) for name in archive.namelist())
            archive.extractall(root)
        environment = {k: v for k, v in os.environ.items() if not k.startswith(
            ('CUDA', 'FASTTURBO', 'PYTHON', 'LD_', 'DISPLAY', 'WAYLAND_DISPLAY'))}
        environment.update(PYTHONPATH=directory, PATH='/usr/bin:/bin')
        if args.ptx:
            environment.update(CUDA_FORCE_PTX_JIT='1', CUDA_CACHE_DISABLE='1')
        program = '''
import json
from pathlib import Path
import fastturbo
from fastturbo import Configuration, Engine
root = Path.cwd()
assert Path(fastturbo.__file__).resolve().is_relative_to(root)
for method, size in [('etdrk4', 34), ('CNAB2', 32)]:
    with Engine(Configuration(resolution=size, integrator=method)) as engine:
        state = engine.advance(17)
        assert state['steps'] == 17 and state['is_finite'], state
paths = {line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines() if '/' in line}
for prefix in ('libcudart', 'libnvrtc.so', 'libnvrtc-builtins'):
    loaded = [Path(p) for p in paths if Path(p).name.startswith(prefix)]
    assert loaded and all(p.is_relative_to(root) for p in loaded), (prefix, loaded)
print(json.dumps({'passed': True, 'backend': fastturbo.get_backend_info(),
                  'cuda_libraries': sorted(p for p in paths if 'libnvrtc' in p or 'libcudart' in p)}))
'''
        result = subprocess.run([sys.executable, '-c', program], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        report = json.loads(result.stdout)
        report.update(wheel=args.wheel.name, forced_ptx=args.ptx)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
