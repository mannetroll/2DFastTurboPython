"""Actual GPU tests, with explicit file exports confined to validation."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import numpy as np
import pytest
from fastturbo import Configuration, Engine
from fastturbo.engine import NativeError, native_library
from validate import FourierGrid, cosine_fixture

pytestmark = pytest.mark.gpu
METHODS = ["imexRK3", "ifHeunRK3", "etdrk4"]
FORMS = ["stress", "advective"]


def spectrum(engine, path):
    engine.export(spectrum=path)
    return np.fromfile(path, dtype="<c8").reshape(engine.config.resolution, engine.config.resolution)


def fixture_file(tmp_path, field):
    path = tmp_path / "initial.f32"
    np.asarray(field, dtype="<f4").tofile(path)
    return path


def test_defaults_match_native():
    expected = Configuration().payload()
    actual = native_library().info["defaults"]
    for key, value in expected.items():
        assert actual[key] == value
    assert actual["padded_resolution"] == 1536
    assert Configuration.calibrated_reynolds(1024, 4) == pytest.approx(actual["reynolds"], rel=1e-14)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("n,dt,steps", [(32, 0.002, 17), (34, 0.001, 29), (128, None, 41)])
def test_python_swift_trajectory_parity(tmp_path, swift_binary, method, form, n, dt, steps):
    config = Configuration(resolution=n, reynolds=50000, seed=7, cfl=1, integrator=method, operator=form, fixed_dt=dt)
    command = [str(swift_binary), "--benchmark", "--resolution", str(n), "--reynolds", "50000",
               "--seed", "7", "--cfl", "1", "--integrator", method, "--operator", form,
               "--warmup", "0", "--steps", str(steps), "--batch", "8",
               "--spectrum", str(tmp_path / "swift.c64"), "--fields", str(tmp_path / "swift.f32")]
    if dt is not None:
        command += ["--fixed-dt", str(dt)]
    swift = json.loads(subprocess.check_output(command, text=True))
    with Engine(config) as engine:
        engine.advance(steps)
        stats = engine.inspect()
        frame = engine.export(spectrum=tmp_path / "python.c64", fields=tmp_path / "python.f32")
    a = np.fromfile(tmp_path / "python.c64", dtype="<c8")
    b = np.fromfile(tmp_path / "swift.c64", dtype="<c8")
    assert np.linalg.norm(a - b) / np.linalg.norm(b) < 3e-6
    np.testing.assert_allclose(a[[0, 1, n + 2, n * n - 1]], b[[0, 1, n + 2, n * n - 1]], rtol=3e-6, atol=1e-8)
    fields = np.fromfile(tmp_path / "python.f32", dtype="<f4").reshape(3, n, n)
    reference_fields = np.fromfile(tmp_path / "swift.f32", dtype="<f4").reshape(3, n, n)
    np.testing.assert_allclose(fields, reference_fields, rtol=3e-6, atol=3e-6)
    for key in ["energy", "enstrophy", "simulation_time", "dt", "initial_turnover_time"]:
        assert stats[key] == pytest.approx(swift["stats"][key], rel=3e-6, abs=1e-9)
    assert stats["steps"] == swift["stats"]["steps"] == steps
    assert frame["max_vorticity"] == pytest.approx(np.abs(reference_fields[0]).max(), rel=3e-6)
    assert frame["max_speed"] == pytest.approx(np.hypot(*reference_fields[1:]).max(), rel=3e-6)


@pytest.mark.parametrize("n", [32, 34, 256])
def test_fft_normalization_zero_nyquist_derivatives_poisson(tmp_path, n):
    grid = FourierGrid(n)
    x, y = np.meshgrid(np.arange(n) * 2 * np.pi / n, np.arange(n) * 2 * np.pi / n)
    field = 2 * np.cos(2*x + 3*y) + np.sin(5*x-y) + 3 + np.cos(n//2*x+y) + np.cos(x+n//2*y)
    field = field.astype("<f4")
    expected = grid.transform(field)
    with Engine(Configuration(resolution=n), initial=fixture_file(tmp_path, field)) as engine:
        actual = spectrum(engine, tmp_path / "spectrum.c64")
        frame = engine.export(fields=tmp_path / "fields.f32")
        stats = engine.inspect()
    assert np.max(np.abs(actual[~grid.mask])) == 0
    assert np.linalg.norm(actual-expected) / np.linalg.norm(expected) < 3e-6
    w, u, v = np.fromfile(tmp_path / "fields.f32", dtype="<f4").reshape(3, n, n)
    wh, uh, vh = np.fft.fft2([w, u, v]) / n**2
    for value in [wh-expected, 1j*(grid.kx*vh-grid.ky*uh)-expected, 1j*(grid.kx*uh+grid.ky*vh)]:
        assert np.linalg.norm(value) / np.linalg.norm(expected) < 2e-5
    expected_u, expected_v = grid.velocity_spectra(expected)
    np.testing.assert_allclose(uh, expected_u, atol=2e-7)
    np.testing.assert_allclose(vh, expected_v, atol=2e-7)
    assert stats["energy"] == pytest.approx(grid.invariants(expected)["energy"], rel=3e-6)
    assert frame["std_vorticity"] == pytest.approx(w.std(), rel=3e-6)
    assert stats["steps"] == frame["sequence"] == 0


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("form", FORMS)
def test_integrator_stages_and_energy_evolution(tmp_path, method, form):
    n = 32
    grid = FourierGrid(n)
    field = cosine_fixture(n).astype("<f4")
    initial = grid.transform(field)
    expected = grid.integrate(initial, until=0.1, dt=0.00025, viscosity=1/50000)
    refined = grid.integrate(initial, until=0.1, dt=0.000125, viscosity=1/50000)
    assert grid.error(expected, refined)["vorticity"] < 1e-10
    config = Configuration(resolution=n, reynolds=50000, integrator=method, operator=form, fixed_dt=0.002)
    with Engine(config, initial=fixture_file(tmp_path, field)) as engine:
        stats = engine.advance(50)
        actual = spectrum(engine, tmp_path / "spectrum.c64")
    assert stats["simulation_time"] == pytest.approx(0.1, abs=1e-8)
    errors = grid.error(actual, refined)
    assert max(errors.values()) < 2e-5, errors
    assert np.linalg.norm(actual-initial) > 0.005*np.linalg.norm(initial)


@pytest.mark.parametrize("n", [32, 34])
@pytest.mark.parametrize("form", FORMS)
def test_classical_padding_truncation_no_aliasing(tmp_path, n, form):
    grid = FourierGrid(n)
    field = cosine_fixture(n, high=True).astype("<f4")
    initial = grid.transform(field)
    derivative = grid.direct_nonlinear(initial, threshold=1e-7)
    np.testing.assert_allclose(grid.nonlinear(initial), derivative, atol=3e-8)
    expected = grid.integrate(initial, 0.04, 0.00025, viscosity=1/50000)
    config = Configuration(resolution=n, reynolds=50000, operator=form, fixed_dt=0.002)
    with Engine(config, initial=fixture_file(tmp_path, field)) as engine:
        assert engine.config.padded_resolution == 3*n//2
        engine.advance(20)
        actual = spectrum(engine, tmp_path / "spectrum.c64")
    assert max(grid.error(actual, expected).values()) < 2e-5
    assert abs(actual[5, 3]) > 0.001  # Retained difference mode really evolves.
    assert abs(actual[1, (-7) % n]) < 1e-5  # Excluded sum cannot wrap into retained band.


@pytest.mark.parametrize("method", METHODS)
def test_diffusion_live_parameters_and_batch_invariance(tmp_path, method):
    n = 32
    x, y = np.meshgrid(np.arange(n)*2*np.pi/n, np.arange(n)*2*np.pi/n)
    initial_file = fixture_file(tmp_path, 20*np.cos(6*x+8*y))
    config = Configuration(resolution=n, reynolds=10000, cfl=3, integrator=method)
    with Engine(config, initial=initial_file) as engine:
        engine.advance(4)
        before = engine.inspect()
        old = spectrum(engine, tmp_path / "old.c64")
        engine.set_parameters(reynolds=1000, cfl=1)
        unchanged = engine.inspect()
        assert unchanged["steps"] == before["steps"]
        assert unchanged["simulation_time"] == before["simulation_time"]
        assert unchanged["allocated_bytes"] == before["allocated_bytes"]
        assert np.array_equal(spectrum(engine, tmp_path / "unchanged.c64"), old)
        after = engine.advance(1)
        actual = spectrum(engine, tmp_path / "after.c64")
        assert after["dt"] < before["dt"] / 2
        expected = old[8, 6] * np.exp(-100*(after["simulation_time"]-before["simulation_time"])/1000)
        assert abs(actual[8, 6] - expected) < 1e-4
        engine.reset(initial=initial_file)
        for _ in range(32):
            single_stats = engine.advance(1)
        singles = spectrum(engine, tmp_path / "singles.c64")
        engine.reset(initial=initial_file)
        batch_stats = engine.advance(32)
        assert batch_stats["simulation_time"] == single_stats["simulation_time"]
        assert np.array_equal(spectrum(engine, tmp_path / "batch.c64"), singles)


def test_initialization_reset_and_uint64_seed(tmp_path, swift_binary):
    for seed in [0, 2, 2**64-1]:
        config = Configuration(resolution=32, seed=seed, fixed_dt=0.002)
        with Engine(config) as engine:
            first = spectrum(engine, tmp_path / "first.c64")
            engine.advance(17)
            engine.reset()
            assert np.array_equal(first, spectrum(engine, tmp_path / "reset.c64"))
        subprocess.run([str(swift_binary), "--benchmark", "--resolution", "32", "--seed", str(seed),
                        "--steps", "0", "--warmup", "0", "--spectrum", str(tmp_path / "swift.c64")], check=True, capture_output=True)
        assert np.array_equal(first, np.fromfile(tmp_path / "swift.c64", dtype="<c8").reshape(32, 32))


def test_rendering_all_choices_preserves_solver(tmp_path):
    with Engine(Configuration(resolution=32, fixed_dt=0.002)) as engine:
        engine.advance(10)
        before = spectrum(engine, tmp_path / "before.c64")
        stats = engine.inspect()
        for field in ["vorticity", "enstrophy", "speed"]:
            for palette in ["inferno", "ember", "aurora", "glacier"]:
                for mode in ["sigma", "exposure"]:
                    path = tmp_path / f"{field}-{palette}-{mode}.png"
                    engine.export(png=path, display=field, palette=palette, color_range=mode, flow=True, pixel_size=64)
                    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert np.array_equal(before, spectrum(engine, tmp_path / "after.c64"))
        assert engine.inspect() == stats


def test_long_run_finite_decay_and_exact_endpoint(tmp_path):
    with Engine(Configuration(resolution=256, fixed_dt=0.002)) as engine:
        before = engine.inspect()
        engine.advance(10000)
        after = engine.inspect()
        assert after["is_finite"] and after["steps"] == 10000
        assert after["simulation_time"] == pytest.approx(20, abs=2e-6)
        assert 0 < after["energy"] < before["energy"]
        assert 0 < after["enstrophy"] < before["enstrophy"]
        assert np.isfinite(spectrum(engine, tmp_path / "long.c64")).all()
    with Engine(Configuration(resolution=32, fixed_dt=0.007)) as engine:
        state = engine.advance(32, until=0.12345)
        assert state["steps"] == 18
        assert state["simulation_time"] == pytest.approx(0.12345, abs=1e-14)


def test_bad_input_and_closed_handle(tmp_path):
    for change in [{"resolution": 31}, {"resolution": 8}, {"reynolds": 0}, {"cfl": -1}, {"fixed_dt": 0}]:
        with pytest.raises(NativeError):
            Engine(replace(Configuration(), **change))
    with pytest.raises(ValueError):
        Engine(Configuration(seed=-1))
    with Engine(Configuration(resolution=32)) as engine:
        for value in [0, -1]:
            with pytest.raises(NativeError):
                engine.set_parameters(cfl=value)
        with pytest.raises(ValueError):
            engine.advance(-1)
    with pytest.raises(NativeError, match="closed"):
        engine.inspect()
