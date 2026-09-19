"""The GUI/API CNAB2 option must preserve the native comparison trajectory."""
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from fastturbo import Configuration, Engine, get_backend_info
from fastturbo.engine import NativeError, NativeUI
from fastturbo.main import config_from, parser

pytestmark = pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="CNAB2 requires Linux CUDA or macOS Metal")
ROOT = Path(__file__).resolve().parents[2]


def spectrum(engine, path):
    engine.export(spectrum=path)
    return np.fromfile(path, dtype="<c8").reshape(engine.config.resolution, engine.config.resolution)


def test_cnab2_defaults_and_cli():
    assert Configuration().payload()["cfl"] == 3
    assert Configuration(integrator="CNAB2").payload()["cfl"] == .25
    assert config_from(parser().parse_args(["--integrator", "CNAB2"])).cfl == .25
    assert config_from(parser().parse_args(["--integrator", "CNAB2", "--cfl", ".1"])).cfl == .1


@pytest.mark.gpu
@pytest.mark.parametrize("n,start", [(128, "PAO"), (512, "KM3")])
def test_cnab2_reference_parity_batching_and_snapshots(tmp_path, n, start):
    reference = tmp_path / "reference"
    if sys.platform == "darwin":
        command = [str(ROOT / "reference-macos/2DFastTurbo/tools/sim"),
            str(n), "10000", "10", "1001", ".25", "metal", "100", start, "CNAB2",
            "--state-out", str(reference), "--json", str(tmp_path / "reference.json")]
    else:
        command = [str(ROOT / "build-linux/reference-linux/fastturbo_reference"),
            str(n), "10000", "10", "1001", ".25", "cuda", "100", start, "CNAB2",
            "--dump", str(reference), "--output", str(tmp_path / "reference.json")]
    subprocess.run(command,
        env={**os.environ, "SCIPYTURBO_SEED": "1"}, capture_output=True, text=True, check=True, timeout=60)
    expected = -np.fromfile(reference / "omega.c64", dtype="<c8").reshape(n, n//2)
    report = json.loads((tmp_path / "reference.json").read_text())
    config = Configuration(resolution=n, k0=10, reynolds=10000, seed=1,
                           integrator="CNAB2", start_spectrum=start)
    assert "CNAB2" in get_backend_info()["integrators"]
    with Engine(config) as engine:
        for count in [1, 99, 201, 700]:
            stats = engine.advance(count)
            engine.export(snapshot=True)  # Reading/rendering must not change the trajectory.
        assert stats["steps"] == 1001
        assert stats["simulation_time"] == report["final"]["T"]
        actual = spectrum(engine, tmp_path / "actual.c64")
        np.testing.assert_array_equal(actual[:, :n//2], expected)
        frame = engine.export(fields=tmp_path / "fields.f32")
        fields = np.fromfile(tmp_path / "fields.f32", dtype="<f4").reshape(3, n, n)
        expected_field = np.fft.ifft2(actual.astype(np.complex128)).real*n*n
        assert np.linalg.norm(fields[0]-expected_field)/np.linalg.norm(expected_field) < 3e-6
        x,y=np.meshgrid(np.fft.fftfreq(n)*n,np.fft.fftfreq(n)*n)
        k2=x*x+y*y
        potential=np.divide(actual.astype(np.complex128),k2,out=np.zeros((n,n),np.complex128),where=k2!=0)
        expected_velocity=np.fft.ifft2(np.stack((1j*y*potential,-1j*x*potential))).real*n*n
        assert np.linalg.norm(fields[1:]-expected_velocity)/np.linalg.norm(expected_velocity) < 3e-6
        assert frame["sequence"] == 1001 and frame["grid_size"] == n
        assert np.isfinite(fields).all() and np.max(np.abs(fields[1:])) > 0
        engine.reset()
        assert engine.inspect()["steps"] == 0
        initial = -np.fromfile(reference / "initial-omega.c64", dtype="<c8").reshape(n, n//2)
        np.testing.assert_array_equal(spectrum(engine, tmp_path / "reset.c64")[:, :n//2], initial)
        engine.advance(1001)
        np.testing.assert_array_equal(spectrum(engine, tmp_path / "single-batch.c64"), actual)


@pytest.mark.gpu
def test_cnab2_import_fixed_step_endpoint_and_live_controls(tmp_path):
    n=32
    x=np.arange(n)*2*np.pi/n
    field=np.broadcast_to(np.cos(3*x),(n,n)).astype("<f4")
    initial=tmp_path / "input.f32";field.tofile(initial)
    config=Configuration(resolution=n, reynolds=100, integrator="CNAB2", fixed_dt=.002)
    with Engine(config, initial=initial) as engine:
        before=spectrum(engine,tmp_path / "before.c64")
        stats=engine.advance(100,until=.0103)
        assert stats["steps"] == 6 and stats["simulation_time"] == .0103
        actual=spectrum(engine,tmp_path / "after.c64")
        assert abs(actual[0,3]/before[0,3]-np.exp(-9*.01*.0103)) < 3e-6
        engine.advance(100,until=.0203)
        assert engine.inspect()["simulation_time"] == .0203
        saved=spectrum(engine,tmp_path / "saved.c64")
        step=engine.inspect()["steps"]
        engine.set_parameters(reynolds=200,cfl=.1)
        assert engine.inspect()["steps"] == step and engine.inspect()["reynolds"] == 200
        np.testing.assert_array_equal(spectrum(engine,tmp_path / "live.c64"),saved)
        assert engine.advance(3)["steps"] == step+3
        engine.reset(initial=initial)
        assert engine.inspect()["steps"] == 0
    np.zeros((n,n),dtype="<f4").tofile(initial)
    with Engine(config,initial=initial) as engine:
        assert engine.advance(10)["is_finite"]
        assert engine.inspect()["energy"] == 0


@pytest.mark.gpu
@pytest.mark.parametrize("changes", [{"resolution":34},{"seed":0},{"seed":5011},{"operator":"advective"}])
def test_cnab2_rejects_unsupported_reference_configuration(changes):
    with pytest.raises(NativeError,match="CNAB2 requires"):
        Engine(Configuration(**{"resolution":32,"integrator":"CNAB2",**changes}))


@pytest.mark.gpu
@pytest.mark.gui
def test_cnab2_native_ui_configuration():
    ui=NativeUI()
    try:
        ui.call("configure",operator="advective")
        state=ui.call("configure",integrator="CNAB2",cap_cfl=True)
        assert state["config"]["operator"] == "stress" and state["config"]["cfl"] == .25
        assert ui.call("set",cfl=.1)["config"]["cfl"] == pytest.approx(.1)
        ui.call("configure",seed="5010")
        assert ui.call("seed")["config"]["seed"] == "1"
        with pytest.raises(NativeError,match="CNAB2 requires"):
            ui.call("configure",resolution=3072)
        state=ui.call("configure",integrator="etdrk4",cap_cfl=True)
        assert state["config"]["cfl"] >= 1
    finally:
        ui.close()
