"""Linux CUDA checks; validation exports are never a production readback path."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastturbo import Configuration, Engine, get_backend_info
from fastturbo.engine import NativeError

pytestmark = [pytest.mark.gpu, pytest.mark.skipif(sys.platform != "linux", reason="Linux backend")]
ROOT = Path(__file__).resolve().parents[2]


def test_without_display_qt_or_library_path():
    environment = {k: v for k, v in os.environ.items() if k not in
                   {"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "LD_LIBRARY_PATH"}}
    script = """
import importlib.abc, sys
from pathlib import Path
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PySide6', 'shiboken6')):
            raise AssertionError('Headless execution imported Qt')
sys.meta_path.insert(0, NoQt())
from fastturbo import Configuration, Engine, get_backend_info
info = get_backend_info()
assert info['backend'] == 'CUDA / VkFFT'
assert info['capabilities']['headless']
with Engine(Configuration(resolution=1024, reynolds=50000)) as engine:
    assert engine.config.padded_resolution == 1536
    assert engine.advance(32)['steps'] == 32
    assert engine.inspect()['is_finite']
maps = Path('/proc/self/maps').read_text()
for library in ('libQt', 'libX11', 'libwayland', 'libGL.so', 'libEGL.so'):
    assert library not in maps, library
print(info)
"""
    subprocess.run([sys.executable, "-c", script], env=environment, check=True, timeout=120)


def test_recorded_native_windows_trajectory():
    source = ROOT / "reference-windows/benchmarks/2026-09-13/headless-1.json"
    reference = json.loads(source.read_text())
    c = reference["config"]
    config = Configuration(**{k: c[k] for k in Configuration.__dataclass_fields__ if k in c})
    with Engine(config) as engine:
        initial = engine.inspect()
        engine.advance(reference["stats"]["steps"])
        final = engine.inspect()
    errors = {}
    for phase, actual in (("initial_stats", initial), ("stats", final)):
        expected = reference[phase]
        assert actual["steps"] == expected["steps"]
        for key in ("simulation_time", "dt", "energy", "enstrophy", "palinstrophy", "initial_turnover_time"):
            # Different host libm, NVRTC and GPU compiler versions are involved.
            # Use the existing trajectory suite's 3 ppm FP32 tolerance, including
            # the long recorded trajectory; do not require bitwise host-libm parity.
            assert actual[key] == pytest.approx(expected[key], rel=3e-6, abs=1e-9)
            errors[f"{phase}.{key}"] = abs(actual[key] - expected[key]) / max(abs(expected[key]), 1e-30)
    evidence = Path(os.environ.get("FASTTURBO_EVIDENCE_DIR", ROOT / "python/evidence/linux"))
    output = evidence / "windows-parity.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"passed": True, "reference": str(source.relative_to(ROOT)),
        "config": c, "initial_stats": initial, "stats": final, "relative_errors": errors,
        "hardware": get_backend_info()}, indent=2) + "\n")


@pytest.mark.parametrize("n", [1024, 2048])
def test_large_grid_reference_and_snapshot_residency(tmp_path, swift_binary, n):
    native, python = tmp_path / "native.c64", tmp_path / "python.c64"
    subprocess.run([str(swift_binary), "--benchmark", "--resolution", str(n), "--steps", "129",
        "--reynolds", "50000", "--seed", "2", "--warmup", "0", "--spectrum", str(native)],
        check=True, capture_output=True, timeout=120)
    with Engine(Configuration(resolution=n, reynolds=50000)) as engine:
        before = engine.inspect()["allocated_bytes"]
        engine.advance(129)
        engine.export(spectrum=python)
        # A GPU snapshot returns only reductions/metadata through the ABI.
        frame = engine.export(snapshot=True)
        assert frame["grid_size"] == n
        assert all(isinstance(v, (int, float)) for v in frame.values())
        assert engine.inspect()["allocated_bytes"] == before
    assert python.read_bytes() == native.read_bytes()


def test_native_batch_worker_gil_and_close():
    get_backend_info()
    with Engine(Configuration(resolution=32)) as engine:
        with ThreadPoolExecutor(max_workers=2) as workers:
            completed = list(workers.map(lambda _: engine.advance(17)["steps"], range(4)))
        assert sorted(completed) == [17, 34, 51, 68]
    with pytest.raises(NativeError, match="closed"):
        engine.advance()


@pytest.mark.gui
@pytest.mark.parametrize("script", ["check_linux_rendering.py", "check_linux_workers.py"])
def test_native_rendering_validation(tmp_path, script):
    output = tmp_path / ("colors" if script == "check_linux_rendering.py" else "workers.json")
    subprocess.run([sys.executable, str(ROOT / "python/scripts" / script), "--output", str(output)],
                   check=True, timeout=120)


@pytest.mark.gui
def test_rendering_failure_stops_worker():
    environment = {**os.environ, "DISPLAY": ""}
    script = """
import time
from fastturbo.engine import NativeError, NativeUI
ui = NativeUI()
try:
    ui.call('configure', resolution=128)
    # With no display, renderer initialization must fail before using this ID.
    ui.attach(1)
    try:
        ui.call('start')
    except NativeError:
        pass  # Renderer can fail before or after the start request.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = ui.call()
        if state['error_message'] and not state['running']:
            assert 'display' in state['error_message'].lower()
            break
        time.sleep(.01)
    else:
        raise AssertionError('Renderer failure left the simulation running')
    for op in ('start', 'toggle', 'running', 'step'):
        try:
            ui.call(op, value=True)
        except NativeError:
            pass
        else:
            raise AssertionError('Resumed work after a fatal renderer error: ' + op)
finally:
    ui.close()
"""
    subprocess.run([sys.executable, "-c", script], env=environment, check=True, timeout=30)
