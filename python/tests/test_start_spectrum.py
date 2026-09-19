"""Spectrum physics, reset semantics, and native configuration propagation."""
from dataclasses import replace

import numpy as np
import pytest

from fastturbo import Configuration, Engine
from fastturbo.engine import NativeError, NativeUI
from fastturbo.main import config_from, parser


def read_spectrum(engine, path):
    engine.export(spectrum=path)
    n = engine.config.resolution
    return np.fromfile(path, dtype="<c8").reshape(n, n)


def test_start_spectrum_options():
    assert config_from(parser().parse_args([])).start_spectrum == "PAO"
    assert config_from(parser().parse_args(["--start-spectrum", "KM3"])).payload()["start_spectrum"] == "KM3"
    with pytest.raises(ValueError, match="Start spectrum"):
        Configuration(start_spectrum="unknown").payload()


@pytest.mark.gpu
@pytest.mark.parametrize("n", [34, 128])
def test_km3_shell_law_cutoff_and_reset(tmp_path, n):
    config = Configuration(resolution=n, k0=4, seed=1, start_spectrum="KM3", fixed_dt=.0001)
    with Engine(config) as engine:
        initial = read_spectrum(engine, tmp_path / "initial.c64")
        engine.advance(7)
        assert not np.array_equal(initial, read_spectrum(engine, tmp_path / "advanced.c64"))
        engine.reset()
        assert np.array_equal(initial, read_spectrum(engine, tmp_path / "reset.c64"))
        assert engine.inspect()["steps"] == 0
    wave = np.fft.fftfreq(n) * n
    x, y = np.meshgrid(wave, wave)
    k2 = x*x + y*y
    radius = np.sqrt(k2)
    power = np.abs(initial.astype(np.complex128))**2 / np.where(k2 == 0, 1, k2)
    shell = np.floor(radius + .5).astype(int)
    energy = np.bincount(shell.ravel(), weights=power.ravel())
    k = np.arange(1, n//2)  # Production excludes the Nyquist axes.
    target = np.where(k < config.k0, (k/config.k0)**4/config.k0**3, 1/k.astype(float)**3)
    ratio = energy[k] / target
    np.testing.assert_allclose(ratio, np.full_like(ratio, ratio.mean()), rtol=2e-6)
    assert initial[0, 0] == 0
    assert np.all(initial[radius > n//2] == 0)
    assert np.all(initial[n//2] == 0) and np.all(initial[:, n//2] == 0)
    reverse = (-np.arange(n)) % n
    np.testing.assert_array_equal(initial, initial[np.ix_(reverse, reverse)].conj())
    for variant in (replace(config, seed=2), replace(config, start_spectrum="PAO")):
        with Engine(variant) as engine:
            assert not np.array_equal(initial, read_spectrum(engine, tmp_path / "variant.c64"))


@pytest.mark.gpu
@pytest.mark.parametrize("method", ["imexRK3", "ifHeunRK3", "etdrk4"])
@pytest.mark.parametrize("form", ["stress", "advective"])
def test_km3_evolves_and_imported_fields_override_choice(tmp_path, method, form):
    config = Configuration(resolution=32, k0=4, start_spectrum="KM3", integrator=method,
                           operator=form, fixed_dt=.0001)
    with Engine(config) as engine:
        before = engine.inspect()
        engine.advance(20)
        after = engine.inspect()
        assert after["steps"] == 20 and after["is_finite"]
        assert 0 < after["energy"] < before["energy"]
    x = np.arange(32) * (2*np.pi/32)
    field = np.broadcast_to(np.cos(3*x), (32, 32)).astype("<f4")
    path = tmp_path / "input.f32"
    field.tofile(path)
    with Engine(config, initial=path) as engine:
        km3 = read_spectrum(engine, tmp_path / "km3.c64")
    with Engine(replace(config, start_spectrum="PAO"), initial=path) as engine:
        np.testing.assert_array_equal(km3, read_spectrum(engine, tmp_path / "pao.c64"))


@pytest.mark.gpu
@pytest.mark.gui
def test_native_spectrum_validation_and_reporting():
    ui = NativeUI()
    try:
        assert ui.call("configure", start_spectrum="KM3")["config"]["start_spectrum"] == "KM3"
        assert ui.call("configure", seed="7")["config"]["start_spectrum"] == "KM3"
        with pytest.raises(NativeError, match="start spectrum|start_spectrum"):
            ui.call("configure", start_spectrum="unknown")
    finally:
        ui.close()
