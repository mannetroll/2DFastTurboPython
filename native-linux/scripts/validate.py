#!/usr/bin/env python3
"""Run real NVIDIA desktop/numerical acceptance; archive exact commands and output."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import time

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('stage', choices=('desktop', 'tests', 'performance', 'lifecycle'))
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output = a.output.resolve()
a.output.mkdir(parents=True, exist_ok=True)
commands = []


def run(name, command, env=None):
    started = time.time()
    command = list(map(str, command))
    print(name, flush=True)
    with (a.output / (name + '.log')).open('w') as log:
        r = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    commands.append(dict(argv=command, cwd=str(ROOT), seconds=time.time()-started,
                         returncode=r.returncode, display=(env or os.environ).get('DISPLAY')))
    (a.output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    r.check_returncode()


if a.stage == 'desktop':
    run('desktop', ['./fastturbo-linux', '--self-test', '--output', a.output / 'desktop.json'])
elif a.stage == 'lifecycle':
    for exit_action in ('close-menu', 'quit-menu', 'ctrl-w', 'ctrl-q'):
        run(exit_action, ['./fastturbo-linux', '--self-test', '--test-exit', exit_action,
                          '--output', a.output / (exit_action + '.json')])
elif a.stage == 'tests':
    run('ctest', ['ctest', '--test-dir', 'build-linux', '--output-on-failure',
                  '--output-junit', a.output / 'ctest.xml'])
    gui = a.output / 'gui'
    gui.mkdir(exist_ok=True)
    for pattern in ('*.png', '*.json'):
        for file in (ROOT / 'build-linux/native-linux').glob(pattern):
            shutil.copy2(file, gui / file.name)
    env = dict(os.environ, FASTTURBO_CUDA_ARCH='86', FASTTURBO_LINUX_RENDERER='ON',
               FASTTURBO_EVIDENCE_DIR=str(a.output / 'python'),
               FASTTURBO_REFERENCE_EVIDENCE=str(a.output / 'reference'), QT_QPA_PLATFORM='xcb')
    run('pytest', ['python/.venv/bin/python', '-m', 'pytest', '-q', '-ra',
                   'python/tests', 'reference-linux/tests/test_reference.py',
                   '--junitxml=' + str(a.output / 'pytest.xml')], env)
    run('ldd', ['ldd', 'build-linux/fastturbo-linux'])
    run('nvidia-smi', ['nvidia-smi'])
    run('xrandr', ['xrandr', '--current'])
    run('xresources', ['xrdb', '-query'])
    (a.output / 'environment.json').write_text(json.dumps({k: os.environ.get(k) for k in ('DISPLAY', 'WAYLAND_DISPLAY', 'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP')}, indent=2) + '\n')
elif a.stage == 'performance':
    run('performance', ['python3', 'native-linux/scripts/compare.py', '--runs', '3',
                        '--seconds', '10', '--sizes', '2048', '4096', '--output', a.output / 'performance'])
    env = {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}
    run('2dbench', ['uv', 'run', '--project', 'python', '2dbench', '--seconds', '10', '--json'], env)
