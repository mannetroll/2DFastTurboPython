"""Collect compact, reproducible validation and timing evidence for the port."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

project = Path(__file__).resolve().parents[1]
destination = project / "evidence/windows"
destination.mkdir(parents=True, exist_ok=True)
files = {
    "gui.json": "results/gui-all-resolutions.json",
    "workers.json": "results/windows-workers-final.json",
    "package.json": "results/package-validation.json",
    "wheel.json": "results/wheel-validation.json",
    "performance.json": "results/windows-performance-final/summary.json",
    "performance-runs.json": "results/windows-performance-final/runs.json",
    "performance-runs.csv": "results/windows-performance-final/runs.csv",
    "initial-performance-runs.json": "results/windows-performance-initial/runs.json",
    "profile.json": "results/profile.json",
    "gui.png": "results/gui-window.png",
}
for name, source in files.items():
    shutil.copy2(project / source, destination / name)
test_bytes = (project / "results-complete-tests.log").read_bytes()
test_log = test_bytes.decode("utf-16" if test_bytes.startswith(b"\xff\xfe") else "utf-8-sig")
assert "52 passed" in test_log and "100% tests passed, 0 tests failed out of 2" in test_log
gui = json.loads((destination / "gui.json").read_text())
assert gui["passed"] and all(gui["checks"].values())
for name in ("workers", "package", "wheel"):
    assert json.loads((destination / f"{name}.json").read_text())["passed"]
subprocess.run(["git", "diff", "--exit-code", "--", "reference-macos", "reference-windows"], cwd=project.parent, check=True)
manifest = json.loads((project / "src/fastturbo/_native/build-manifest.json").read_text())
artifacts = [project / "dist/2DFastTurbo-Python-Windows.zip",
             project / "dist/fastturbo-0.1.0-py3-none-win_amd64.whl",
             project / "dist/fastturbo-0.1.0.tar.gz",
             project / "src/fastturbo/_native/fastturbo.dll",
             project / "build/windows/reference/2DFastTurbo.exe"]
performance = json.loads((destination / "performance.json").read_text())
assert performance["dll_sha256"] == hashlib.sha256(artifacts[-2].read_bytes()).hexdigest()
assert performance["native_sha256"] == hashlib.sha256(artifacts[-1].read_bytes()).hexdigest()
result = {"passed": True, "platform": platform.platform(), "python": platform.python_version(),
    "cuda": manifest["nvcc"], "msvc": manifest["msvc"], "architectures": manifest["architectures"],
    "python_tests": 52, "native_ctest_checks": 2, "gui_checks": len(gui["checks"]),
    "reference_trees_unchanged": True, "source_archive_wheel_build": "uv build completed from the source archive",
    "macos_runtime_retested": False, "artifacts": {str(p.relative_to(project)):
        {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in artifacts}}
(destination / "validation.json").write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps(result, indent=2))
