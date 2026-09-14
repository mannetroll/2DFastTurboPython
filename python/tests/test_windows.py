"""Windows binding, native worker, and exported-render validation."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
from fastturbo import Configuration, Engine, create_engine
from fastturbo.engine import NativeError, native_library

pytestmark = [pytest.mark.gpu, pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")]
ROOT = Path(__file__).resolve().parents[2]


def test_windows_backend_and_shared_abi():
    library = native_library()
    assert library.info["abi_version"] == 1
    assert library.info["backend"] == "CUDA / VkFFT"
    assert "NVIDIA" in library.info["gpu"]
    with create_engine(Configuration(resolution=34)) as engine:
        assert engine.config.padded_resolution == 51
        assert engine.advance(3)["steps"] == 3
        assert engine.inspect()["is_finite"]


@pytest.mark.parametrize("method", ["imexRK3", "ifHeunRK3", "etdrk4"])
@pytest.mark.parametrize("form", ["stress", "advective"])
def test_bitwise_native_parity_and_unicode_exports(tmp_path, swift_binary, method, form):
    # Full UInt64 parsing and non-ASCII paths exercise the Windows ABI boundary.
    native_file = tmp_path / "native.c64"
    python_file = tmp_path / "flöde-渦.c64"
    subprocess.run([str(swift_binary), "--benchmark", "--resolution", "128", "--steps", "129",
        "--fixed-dt", "0.001", "--reynolds", "50000", "--seed", str(2**64-1),
        "--integrator", method, "--operator", form, "--warmup", "0", "--spectrum", str(native_file)],
        check=True, capture_output=True, timeout=90)
    with Engine(Configuration(resolution=128, seed=2**64-1, reynolds=50000,
                              integrator=method, operator=form, fixed_dt=.001)) as engine:
        engine.advance(129)
        engine.export(spectrum=python_file)
    assert python_file.read_bytes() == native_file.read_bytes()


def test_rendered_pixels_against_independent_shader_equations(tmp_path):
    manifest = []
    with Engine(Configuration(resolution=32)) as engine:
        engine.advance(11)
        engine.export(fields=tmp_path / "fields.f32")
        for field_index, field in enumerate(["vorticity", "speed", "enstrophy"]):
            for palette_index, palette in enumerate(["aurora", "ember", "glacier", "inferno"]):
                for range_index, color_range in enumerate(["sigma", "exposure"]):
                    filename = f"{field}-{palette}-{color_range}.png"
                    engine.export(png=tmp_path / filename, display=field, palette=palette,
                                  color_range=color_range, sigma=2.1, exposure=1.7, pixel_size=57)
                    manifest.append({"file": filename, "field": field_index, "palette": palette_index,
                                     "range": range_index, "sigma": 2.1, "exposure": 1.7})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    subprocess.run([sys.executable, str(ROOT / "reference-windows/tests/render_reference.py"),
                    "--directory", str(tmp_path), "--n", "32"], check=True, timeout=30)


@pytest.mark.parametrize("n", [1024, 2048])
def test_large_grid_native_parity(tmp_path, swift_binary, n):
    native_file, python_file = tmp_path / "native.c64", tmp_path / "python.c64"
    subprocess.run([str(swift_binary), "--benchmark", "--resolution", str(n), "--steps", "129",
        "--reynolds", "50000", "--seed", "2", "--warmup", "0", "--spectrum", str(native_file)],
        check=True, capture_output=True, timeout=90)
    with Engine(Configuration(resolution=n, reynolds=50000)) as engine:
        engine.advance(129)
        engine.export(spectrum=python_file)
    assert python_file.read_bytes() == native_file.read_bytes()


@pytest.mark.gui
def test_native_workers_continue_without_python_events(tmp_path):
    # Separate process owns Qt and native HWND lifetime. No processEvents during
    # the measurement: both the solver and renderer must run without Python.
    subprocess.run([sys.executable, str(ROOT / "python/scripts/check_windows_workers.py"),
                    "--output", str(tmp_path / "workers.json")], check=True, timeout=90)


def test_invalid_native_commands_and_worker_close():
    from concurrent.futures import ThreadPoolExecutor
    native_library()  # Initialize on the main thread before releasing the GIL.
    with Engine(Configuration(resolution=32)) as engine:
        for op, kwargs in [("advance", {"steps": -1}), ("unknown", {}),
                           ("export", {"png": "unused.png", "palette": "invalid"})]:
            with pytest.raises(NativeError):
                engine._call(op, **kwargs)
        with ThreadPoolExecutor(max_workers=2) as pool:
            completed = list(pool.map(lambda _: engine.advance(17)["steps"], range(4)))
        assert sorted(completed) == [17, 34, 51, 68]
    with pytest.raises(NativeError, match="closed"):
        engine.advance()
