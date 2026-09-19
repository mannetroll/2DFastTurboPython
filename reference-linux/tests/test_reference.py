"""Optional development checks. Native CMake/CTest/run never requires Python."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np
import pytest
from fastturbo import Configuration, Engine

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get("FASTTURBO_REFERENCE_BINARY", ROOT / "build-linux/reference-linux/fastturbo_reference"))
ENV = {k: v for k, v in os.environ.items()
       if k not in {"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "LD_LIBRARY_PATH"}}


def run(*args, seed=1):
    return subprocess.run([str(BINARY), *map(str, args)], env={**ENV, "SCIPYTURBO_SEED": str(seed)},
                          capture_output=True, text=True, check=True, timeout=180)


@pytest.fixture(scope="module")
def historical_initializer():
    # Call only the original coefficient generator. No CPU FFT or timestep.
    path = ROOT / "reference-macos/2DFastTurbo/tools/turbo_simulator.py"
    import sys
    spec = importlib.util.spec_from_file_location("historical_dns", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
    return module._pao_build_ur_and_stats_impl


def norms(actual, expected):
    delta = actual.astype(np.complex128) - expected.astype(np.complex128)
    return {"max_abs": float(np.max(np.abs(delta))),
            "max_rel": float(np.max(np.abs(delta) / np.maximum(np.abs(expected), 1e-30))),
            "rms": float(np.sqrt(np.mean(np.abs(delta)**2))),
            "relative_l2": float(np.linalg.norm(delta) / max(np.linalg.norm(expected), 1e-30))}


def record(name, report):
    if folder := os.environ.get("FASTTURBO_REFERENCE_EVIDENCE"):
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(json.dumps(report, indent=2) + "\n")


@pytest.mark.parametrize("n,seed,k0", [(32, 1, 10), (64, 149, 7.5), (32, 5010, 10), (512, 1, 10)])
def test_legacy_initial_arrays(tmp_path, historical_initializer, n, seed, k0):
    run(n, 10000, k0, 0, .25, "cuda", 100, "PAO", "CNAB2", "--dump", tmp_path,
        "--output", tmp_path / "report.json", seed=seed)
    alfa = np.arange(n // 2, dtype=np.float32)
    gamma = np.concatenate((np.arange(n // 2), np.arange(-n // 2, 0))).astype(np.float32)
    with np.errstate(under="ignore"):
        ur, seed_out, visc, energy, wiwi, *_ = historical_initializer(
            n, n, np.float32(k0), np.float32(10000), seed, alfa, gamma, False)
    velocity = np.ascontiguousarray(ur.transpose(2, 1, 0))
    diff = gamma[:, None] * velocity[0] - alfa[None, :] * velocity[1]
    omega = (-diff.imag + 1j * diff.real).astype(np.complex64)
    results = {}
    for name, expected in (("omega", omega), ("velocity", velocity)):
        actual = np.fromfile(tmp_path / f"initial-{name}.c64", dtype="<c8").reshape(expected.shape)
        errors = norms(actual, expected)
        assert errors["relative_l2"] < 3e-6, errors
        np.testing.assert_allclose(actual, expected, rtol=3e-6, atol=1e-8)
        results[name] = errors
    initial = json.loads((tmp_path / "report.json").read_text())["initial"]
    assert initial["seed_out"] == seed_out
    assert initial["viscosity"] == float(visc)
    assert initial["Energy"] == pytest.approx(energy, rel=3e-6)
    assert initial["WiWi"] == pytest.approx(wiwi, rel=3e-6)
    record(f"initializer-n{n}-seed{seed}", results)


def test_legacy_determinism_and_checkpoint_cadence(tmp_path):
    for name in ("a", "b"):
        folder = tmp_path / name
        run(32, 10000, 10, 260, .25, "cuda", 1000, "PAO", "CNAB2", "--dump", folder,
            "--output", tmp_path / (name + ".json"), seed=5010)
    for file in ("initial-omega.c64", "omega.c64", "history.c64", "velocity.f32"):
        assert (tmp_path / "a" / file).read_bytes() == (tmp_path / "b" / file).read_bytes()
        assert np.isfinite(np.fromfile(tmp_path / "a" / file, dtype="<f4")).all()
    report = json.loads((tmp_path / "a.json").read_text())
    assert [r["iteration"] for r in report["iterations"]] == [1, 260]
    assert report["final"]["steps"] == 260
    assert (tmp_path / "a/initial-omega.c64").read_bytes() != (tmp_path / "a/omega.c64").read_bytes()


@pytest.mark.parametrize("n,seed,k0", [(32, 149, 7.5), (512, 1, 10)])
@pytest.mark.parametrize("steps", [0, 7])
@pytest.mark.parametrize("spectrum", ["PAO", "KM3"])
def test_legacy_velocity_matches_real_fourier_sum(tmp_path, n, seed, k0, steps, spectrum):
    run(n, 10000, k0, steps, .25, "cuda", 100, spectrum, "CNAB2", "--dump", tmp_path, seed=seed)
    if steps == 0:
        coefficients = np.fromfile(tmp_path / "initial-velocity.c64", dtype="<c8").reshape(2, n, n//2)
        if spectrum == "KM3":
            assert coefficients[0, n//2, 0] != 0  # Exercise the unpaired cutoff mode.
    else:
        omega = np.fromfile(tmp_path / "omega.c64", dtype="<c8").reshape(n, n//2).astype(np.complex128)
        x = np.arange(n//2)[None, :]
        y = (np.fft.fftfreq(n) * n)[:, None]
        k2 = x*x + y*y
        potential = np.divide(omega, k2, out=np.zeros_like(omega), where=k2 != 0)
        coefficients = np.stack((-1j*y*potential, 1j*x*potential))

    # Independent complex128 C2C inverse: retain the original kx=0 column,
    # complete the omitted negative kx half, and take the real Fourier sum.
    # Using irfft here would itself assume symmetry of the unpaired KM3 mode.
    m = 3*n//2
    full = np.zeros((2, m, m), dtype=np.complex128)
    full[:, :n//2, :n//2] = coefficients[:, :n//2]
    full[:, n:, :n//2] = coefficients[:, n//2:]
    full[:, :, m//2+1:] = full[:, (-np.arange(m)) % m, 1:m//2][:, :, ::-1].conj()
    expected = np.fft.ifft2(full).real * (m*m)
    actual = np.fromfile(tmp_path / "velocity.f32", dtype="<f4").reshape(2, m, m)
    errors = norms(actual, expected)
    assert errors["relative_l2"] < 3e-6, errors
    record(f"real-fourier-sum-{spectrum}-n{n}-seed{seed}-steps{steps}", errors)


@pytest.mark.parametrize("n,method,form,warmup,start_spectrum", [
    (34, method, form, 0, "PAO") for method in ("imexRK3", "ifHeunRK3", "etdrk4")
    for form in ("stress", "advective")
] + [(1024, "etdrk4", "stress", 3, "PAO"), (2048, "etdrk4", "stress", 3, "PAO"),
     (34, "etdrk4", "stress", 0, "KM3")])
def test_full_state_binding_parity(tmp_path, n, method, form, warmup, start_spectrum):
    native, binding = tmp_path / "native.c64", tmp_path / "binding.c64"
    field, binding_field = tmp_path / "native.f32", tmp_path / "binding.f32"
    steps = 129 if n >= 1024 else 17
    config = Configuration(resolution=n, reynolds=50000, seed=7, cfl=1, integrator=method,
                           operator=form, start_spectrum=start_spectrum)
    with Engine(config) as engine:
        engine.advance(warmup + steps)
        engine.export(spectrum=binding, fields=binding_field)
        expected = engine.inspect()
    result = run("--benchmark", "--n", n, "--steps", steps, "--warmup", warmup,
                 "--reynolds", 50000, "--seed", 7, "--cfl", 1, "--integrator", method,
                 "--operator", form, "--start-spectrum", start_spectrum, "--spectrum", native, "--fields", field,
                 "--compare-spectrum", binding, "--json")
    report = json.loads(result.stdout)
    assert report["parity"]["status"] == "PASS"
    assert report["start_spectrum"] == start_spectrum
    assert report["completed_timed_steps"] == steps
    assert report["completed_total_steps"] == warmup + steps
    assert report["gpu_event_ms"] > 0 and report["ms_per_step"] > 0
    for key in ("simulation_time", "dt", "energy", "enstrophy", "palinstrophy"):
        assert report["stats"][key] == pytest.approx(expected[key], rel=3e-6, abs=1e-9)
    a, b = np.fromfile(native, dtype="<c8"), np.fromfile(binding, dtype="<c8")
    errors = norms(a, b)
    assert errors["relative_l2"] < 3e-6
    fields = np.fromfile(field, dtype="<f4")
    expected_fields = np.fromfile(binding_field, dtype="<f4")
    np.testing.assert_allclose(fields, expected_fields, rtol=3e-6, atol=3e-6)
    record(f"binding-n{n}-{method}-{form}-{start_spectrum}", {"spectrum": errors, "fields": norms(fields, expected_fields),
                                           "stats": report["stats"], "status": "PASS"})


def test_canonical_legacy_log(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "reference-macos/2DFastTurbo/tools"))
    from compare_sim import compare
    result = run(512, 10000, 10, 1001, .25, "cuda", 100, "PAO", "CNAB2", seed=1)
    log = tmp_path / "legacy.txt"
    log.write_text(result.stdout)
    assert compare(ROOT / "reference-macos/2DFastTurbo/tools/sim_512_1001_metal_macOS.txt", log)


@pytest.mark.parametrize("n", [32, 512])
def test_km3_legacy_initializer_and_production_layout(tmp_path, n):
    run(n, 10000, 10, 0, .25, "cuda", 100, "KM3", "CNAB2", "--dump", tmp_path,
        "--output", tmp_path / "km3.json")
    report = json.loads((tmp_path / "km3.json").read_text())
    assert report["config"]["start_spectrum"] == "KM3"
    velocity = np.fromfile(tmp_path / "initial-velocity.c64", dtype="<c8").reshape(2, n, n//2)
    x = np.arange(n//2)[None, :]
    y = (np.fft.fftfreq(n) * n)[:, None]
    np.testing.assert_allclose(x * velocity[0] + y * velocity[1], 0, atol=2e-7)
    if n == 512:
        # cudaturbo/mem_cuda: N512, Re10000, K0=10, seed1, KM3.
        assert report["initial"]["Energy"] == pytest.approx(.9968169, rel=3e-6)
        assert report["initial"]["WiWi"] == pytest.approx(481.14905, rel=3e-6)
        assert report["initial"]["CFLM"] == pytest.approx(327.3059082, rel=3e-6)
    with Engine(Configuration(resolution=n, reynolds=10000, k0=10, seed=1,
                              cfl=.25, start_spectrum="KM3")) as engine:
        engine.export(spectrum=tmp_path / "production.c64")
    actual = np.fromfile(tmp_path / "production.c64", dtype="<c8").reshape(n, n)[:, :n//2]
    expected = -np.fromfile(tmp_path / "initial-omega.c64", dtype="<c8").reshape(n, n//2)
    expected[n//2] = 0  # Production excludes Nyquist and uses positive curl.
    np.testing.assert_allclose(actual, expected, rtol=3e-6, atol=1e-7)


def test_runtime_dependencies_and_reject_changed_parity_data(tmp_path):
    source = Path("reference-windows/benchmarks/2026-09-13/headless-1.json")
    destination = tmp_path / source
    destination.parent.mkdir(parents=True)
    reference = json.loads((ROOT / source).read_text())
    reference["stats"]["energy"] *= 2
    destination.write_text(json.dumps(reference))
    legacy = Path("reference-macos/2DFastTurbo/tools/sim_512_1001_metal_macOS.txt")
    (tmp_path / legacy).parent.mkdir(parents=True)
    (tmp_path / legacy).write_bytes((ROOT / legacy).read_bytes())
    with subprocess.Popen([str(BINARY), "--validate", "--json", "--reference-root", str(tmp_path)],
                          env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        libraries = set()
        deadline = time.monotonic() + 180
        while process.poll() is None:
            assert time.monotonic() < deadline, "Native validation timed out"
            try:
                libraries.update(line.split()[-1] for line in Path(f"/proc/{process.pid}/maps").read_text().splitlines()
                                 if ".so" in line)
            except FileNotFoundError:
                break
            time.sleep(.1)
        output, error = process.communicate(timeout=10)
    assert process.returncode == 1, error
    report = json.loads(output)
    assert report["parity"]["status"] == "FAIL"
    assert report["parity"]["cases"][0]["status"] == "FAIL"
    assert any("libnvrtc" in path for path in libraries)
    assert any("libcuda.so" in path for path in libraries)
    for name in ("libpython", "libQt", "libX11", "libwayland", "libGL.so", "libEGL.so", "libvulkan"):
        assert not any(name in path for path in libraries), name
    record("runtime-libraries", {"loaded_libraries": sorted(libraries), "status": "PASS"})
