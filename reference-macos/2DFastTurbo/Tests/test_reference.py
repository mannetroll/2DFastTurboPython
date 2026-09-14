"""CPU-only mathematical checks for the independent validation implementation."""
import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Scripts"))
from validate import FourierGrid, cosine_fixture, pao_fixture


class ReferenceTests(unittest.TestCase):
    def test_velocity_has_canonical_curl_and_zero_divergence(self):
        grid = FourierGrid(32)
        spectrum = grid.transform(cosine_fixture(32))
        u, v = grid.velocity_spectra(spectrum)
        np.testing.assert_allclose(1j*(grid.kx*v-grid.ky*u), spectrum, atol=1e-15)
        np.testing.assert_allclose(1j*(grid.kx*u+grid.ky*v), 0, atol=1e-15)

    def test_high_modes_match_direct_convolution_without_aliasing(self):
        for n in (32, 34, 48):
            with self.subTest(n=n):
                grid = FourierGrid(n)
                state = grid.transform(cosine_fixture(n, high=True))
                direct = grid.direct_nonlinear(state)
                np.testing.assert_allclose(grid.nonlinear(state), direct, atol=2e-13)
                np.testing.assert_allclose(grid.stress_nonlinear(state), direct, atol=2e-13)
                self.assertGreater(abs(direct[5, 3]), 0.001)
                # p+q falls outside the retained band and cannot wrap to -7,1.
                self.assertLess(abs(direct[1, (-7) % n]), 1e-13)

    def test_zero_mean_nyquist_projection_and_padding_roundtrip(self):
        for n in (32, 34):
            grid = FourierGrid(n)
            x, y = np.meshgrid(np.arange(n)*2*math.pi/n, np.arange(n)*2*math.pi/n)
            input_field = cosine_fixture(n, high=True) + 2 + np.cos(n//2*x+y)
            state = grid.transform(input_field)
            np.testing.assert_allclose(grid.truncate(grid.padded_physical(state)), state, atol=2e-15)
            self.assertEqual(float(np.max(np.abs(state[~grid.mask]))), 0)

    def test_rk4_analytic_diffusion_and_time_refinement(self):
        grid = FourierGrid(16)
        x, y = np.meshgrid(np.arange(16)*2*math.pi/16, np.arange(16)*2*math.pi/16)
        state = grid.transform(2*np.cos(2*x+3*y))
        exact = state*np.exp(-0.08*grid.k2*0.2)
        coarse = grid.integrate(state, 0.2, 0.02, viscosity=0.08)
        fine = grid.integrate(state, 0.2, 0.01, viscosity=0.08)
        coarse_error = np.linalg.norm(coarse-exact)
        fine_error = np.linalg.norm(fine-exact)
        self.assertGreater(coarse_error/fine_error, 15)
        self.assertLess(grid.error(fine, exact)["vorticity"], 3e-10)

    def test_inviscid_advection_conserves_energy_and_enstrophy(self):
        grid = FourierGrid(32)
        state = grid.transform(cosine_fixture(32))
        after = grid.integrate(state, 0.2, 0.002)
        error = grid.error(after, state)
        self.assertGreater(error["vorticity"], 0.01)
        self.assertLess(error["energy"], 1e-11)
        self.assertLess(error["enstrophy"], 1e-11)

    def test_shared_fixture_is_deterministic_and_unit_rms_speed(self):
        a, b = pao_fixture(64, 4, 2), pao_fixture(64, 4, 2)
        self.assertEqual(a.tobytes(), b.tobytes())
        self.assertFalse(np.array_equal(a, pao_fixture(64, 4, 7)))
        grid = FourierGrid(64)
        state = grid.transform(a)
        self.assertAlmostEqual(2*grid.invariants(state)["energy"], 1, places=7)


if __name__ == "__main__":
    unittest.main()
