"""Run the existing numerical gates on the actual distributable with a clean PATH."""
import os
from pathlib import Path
import subprocess
import sys
from audit_runtime import clean_environment

root = Path(__file__).resolve().parents[2]
app = root/'windows/dist/2DFastTurbo.exe'
output = root/'windows/artifacts/packaging'
env = clean_environment()
for script, arguments in (
    ('numerics.py', ['--resolutions', '32', '34', '128', '--output', str(output/'numerics')]),
    ('native_reference.py', ['--output', str(output/'native')]),
):
    command = [sys.executable, str(root/'windows/tests'/script), '--app', str(app), *arguments]
    print('Running', script, 'against distribution, with Windows-only PATH', flush=True)
    subprocess.run(command, env=env, cwd=root, check=True)
print('PASS distribution numerical gates', flush=True)
