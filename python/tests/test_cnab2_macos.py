"""Independent initialization/FFT checks for the shared native Metal CNAB2 core."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "reference-macos/2DFastTurbo/tools"
pytestmark = [pytest.mark.gpu, pytest.mark.skipif(sys.platform != "darwin", reason="Metal CNAB2")]


def run_native(tmp_path, n=32, steps=0, start="PAO", seed=1, update=100):
    output = tmp_path / f"{start}-{n}-{steps}-{seed}"
    # Catch attempts to call Python even when it happens to be installed globally.
    blocked = tmp_path / "no-python"
    blocked.mkdir(exist_ok=True)
    for name in ("python", "python3"):
        path = blocked / name
        path.write_text("#!/bin/sh\necho 'unexpected Python dependency' >&2\nexit 99\n")
        path.chmod(0o755)
    result = subprocess.run([str(TOOLS / "sim"), str(n), "10000", "10", str(steps), ".25", "metal",
        str(update), start, "CNAB2", "--json", str(output / "report.json"), "--state-out", str(output)],
        env={**os.environ, "PATH": f"{blocked}:/usr/bin:/bin", "SCIPYTURBO_SEED": str(seed)},
        capture_output=True, text=True, check=True, timeout=120)
    report = json.loads((output / "report.json").read_text())
    assert report["initializer"] == "native"
    return output, report, result.stdout


@pytest.mark.parametrize("seed", [1, 149, 5010])
def test_native_pao_matches_independent_historical_initializer(tmp_path, seed):
    output, report, _ = run_native(tmp_path, seed=seed)
    spec = importlib.util.spec_from_file_location("cnab2_initializer_reference", TOOLS / "turbo_simulator.py")
    reference = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = reference
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(reference)
        state = reference.create_dns_state(N=32, Re=10000, K0=10, CFL=.25, backend="cpu",
                                           seed=seed, start_spectrum="PAO", populate_compact_ur=False)
    actual = np.fromfile(output / "omega.c64", dtype="<c8").reshape(32, 16)
    expected = np.asarray(state.om2)
    assert np.linalg.norm(actual-expected)/np.linalg.norm(expected) < 2e-6
    assert report["config"]["seed"] == seed


def padded_velocity(omega):
    """Full complex Fourier reconstruction, including the unpaired cutoff mode."""
    n, half = omega.shape
    m = 3*n//2
    x = np.arange(half)[None, :]
    y = (np.fft.fftfreq(n)*n)[:, None]
    k2 = x*x+y*y
    potential = np.divide(omega.astype(np.complex128), k2, out=np.zeros_like(omega, dtype=np.complex128), where=k2!=0)
    compact = np.stack((-1j*y*potential, 1j*x*potential))
    full = np.zeros((2,m,m), np.complex128)
    for z in range(n):
        ky = z if z < half else z-n
        full[:,ky % m,:half] = compact[:,z,:]
        full[:,(-ky) % m,(-np.arange(1,half)) % m] = compact[:,z,1:].conj()
    # The real inverse of the historical unpaired coefficient equals a half
    # coefficient at each conjugate padded mode.
    full[:,half,0] = compact[:,half,0].conj()*.5
    full[:,m-half,0] = compact[:,half,0]*.5
    return (np.fft.ifft2(full).real*m*m).astype(np.float32)


@pytest.mark.parametrize("start", ["PAO", "KM3"])
def test_native_cutoff_inverse_cfl_and_first_step(tmp_path, start):
    output, report, _ = run_native(tmp_path, start=start)
    omega = np.fromfile(output / "omega.c64", dtype="<c8").reshape(32,16)
    velocity = np.fromfile(output / "velocity.f32", dtype="<f4").reshape(2,48,48)
    expected_velocity = padded_velocity(omega)
    assert np.linalg.norm(velocity-expected_velocity)/np.linalg.norm(expected_velocity) < 3e-6
    if start == "KM3":
        assert abs(omega[16,0].imag) > 0
    dx = np.float32(2)*np.float32(np.pi)/np.float32(32)
    maximum = (np.abs(expected_velocity[0])/dx+np.abs(expected_velocity[1])/dx).max()
    assert report["initial"]["CFLM"] == pytest.approx(float(maximum), rel=3e-6)
    dt = np.float32(report["initial"]["DT"])
    u,v = velocity
    products = np.fft.fft2(np.stack((u*u,v*v,u*v)))/(48*48)
    indices = np.r_[np.arange(16),np.arange(32,48)]
    a,b,c = products[:,indices,:16]
    a[16]=b[16]=c[16]=0
    x=np.arange(16)[None,:]; y=(np.fft.fftfreq(32)*32)[:,None]
    nonlinear=(a-b)*(y*x)+c*(y*y-x*x)
    arg=(x*x+y*y)*float(np.float32(.5)*np.float32(1/10000)*dt)
    expected=(omega*(1-arg)+nonlinear*dt)/(1+arg)
    final, _, _ = run_native(tmp_path, steps=1, start=start)
    actual=np.fromfile(final / "omega.c64", dtype="<c8").reshape(32,16)
    assert np.linalg.norm(actual-expected)/np.linalg.norm(expected) < 3e-6


def test_km3_initial_diagnostics_match_recorded_cuda(tmp_path):
    _, report, _ = run_native(tmp_path, n=512, start="KM3")
    assert report["initial"]["Energy"] == pytest.approx(.9968169, rel=3e-6)
    assert report["initial"]["WiWi"] == pytest.approx(481.14905, rel=3e-6)
    assert report["initial"]["CFLM"] == pytest.approx(327.3059082, rel=3e-6)
