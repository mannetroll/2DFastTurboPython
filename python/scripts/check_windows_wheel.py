"""Exercise the wheel's native resources without CUDA Toolkit paths or sources."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import zipfile

project = Path(__file__).resolve().parents[1]
version = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
wheel = project / f"dist/fastturbo-{version}-py3-none-win_amd64.whl"
target = Path(tempfile.mkdtemp(prefix="wheel-check-", dir=project / "build")).resolve()
with zipfile.ZipFile(wheel) as archive:
    assert "Tag: py3-none-win_amd64" in archive.read(f"fastturbo-{version}.dist-info/WHEEL").decode()
    for relative in ("app.py", "assets/header-hurricane.svg", "_native/fastturbo.dll"):
        assert archive.read(f"fastturbo/{relative}") == (project / "src/fastturbo" / relative).read_bytes(), relative
    assert all((target / name).resolve().is_relative_to(target) for name in archive.namelist())
    archive.extractall(target)
system = Path(os.environ["SystemRoot"])
environment = {k:v for k,v in os.environ.items() if not k.upper().startswith(("CUDA", "FASTTURBO", "PYTHONPATH"))}
environment.update(PATH=str(system)+os.pathsep+str(system / "System32"), PYTHONPATH=str(target))
program = """
import json, pathlib, fastturbo
from fastturbo import Configuration, Engine
assert pathlib.Path(fastturbo.__file__).resolve().is_relative_to(pathlib.Path.cwd())
with Engine(Configuration(resolution=34)) as engine:
    state = engine.advance(17)
    assert state['steps'] == 17 and state['is_finite']
print(json.dumps({'passed': True, 'module': fastturbo.__file__, 'steps': state['steps'],
                  'padded_resolution': engine.config.padded_resolution, 'backend': fastturbo.get_backend_info()}))
"""
result = subprocess.run([sys.executable, "-c", program], cwd=target, env=environment,
                        capture_output=True, text=True, timeout=60)
if result.returncode:
    raise RuntimeError(result.stdout[:2000] + result.stderr)
report = json.loads(result.stdout)
report.update(wheel=wheel.name, version=version, sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
(project / "results").mkdir(exist_ok=True)
(project / "results/wheel-validation.json").write_text(json.dumps(report, indent=2)+"\n")
print(json.dumps(report, indent=2))
