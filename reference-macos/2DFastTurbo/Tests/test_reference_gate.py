"""Acceptance regressions: reject false refinement plateaus and disagreements."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Scripts"))
from compare import rank_timed_candidates, reference_gate


def errors(value):
    return {key: value for key in ("vorticity", "energy", "enstrophy")}


class ReferenceGateTests(unittest.TestCase):
    def gate(self, previous, pair, cross, baseline=1e-4, calibrated=True):
        return reference_gate(errors(previous), errors(pair), errors(cross), errors(baseline),
                              floor=2e-6, calibration_passed=calibrated)

    def test_decreasing_three_level_reference_with_independent_agreement_passes(self):
        result = self.gate(previous=1e-5, pair=4e-6, cross=5e-6)
        self.assertTrue(result["converged"])
        self.assertEqual(result["uncertainty_estimate"]["vorticity"], 5e-6)

    def test_growing_roundoff_above_calibrated_floor_fails(self):
        result = self.gate(previous=5e-6, pair=8e-6, cross=2e-6, baseline=1e-3)
        self.assertFalse(result["converged"])
        self.assertFalse(result["decreasing_or_calibrated_plateau"])

    def test_roundoff_plateau_requires_independent_analytic_calibration(self):
        self.assertTrue(self.gate(0.5e-6, 1e-6, 1.5e-6, baseline=1e-6)["converged"])
        self.assertFalse(self.gate(0.5e-6, 1e-6, 1.5e-6, baseline=1e-6,
                                  calibrated=False)["converged"])
        # A frozen Float32 decay can give identical results at every dt.
        self.assertFalse(self.gate(0, 0, 0, calibrated=False)["converged"])

    def test_cross_method_disagreement_rejects_apparent_self_convergence(self):
        result = self.gate(previous=1e-5, pair=1e-6, cross=4e-5)
        self.assertFalse(result["converged"])
        self.assertFalse(result["uncertainty_within_budget"])

    def test_energy_and_enstrophy_must_pass_individually(self):
        cross = errors(1e-6)
        cross["enstrophy"] = 5e-5
        result = reference_gate(errors(1e-5), errors(1e-6), cross, errors(1e-4),
                                floor=2e-6, calibration_passed=True)
        self.assertFalse(result["converged"])


class UserExperienceSelectionTests(unittest.TestCase):
    @staticmethod
    def group(label, method, rate, fixture="primary", runs=5, operator_name="stress"):
        return {"label": label, "integrator": method, "operator": operator_name, "cfl": 1.5, "fixture": fixture,
                "median_simtime_per_s": rate, "runs": runs}

    def test_gui_winner_is_selected_even_when_headless_winner_differs(self):
        groups = [self.group("fast-turbo", "imexRK3", 100),
                  self.group("fast-turbo", "etdrk4", 90),
                  self.group("fast-turbo-live", "imexRK3", 70),
                  self.group("fast-turbo-live", "etdrk4", 80)]
        eligible = [("stress", "imexRK3", 1.5), ("stress", "etdrk4", 1.5)]
        self.assertEqual(rank_timed_candidates(groups, eligible, label="fast-turbo-live",
                                               fixture_count=1, minimum_runs=5)[0], ("stress", "etdrk4", 1.5))
        self.assertEqual(rank_timed_candidates(groups, eligible, label="fast-turbo",
                                               fixture_count=1, minimum_runs=5)[0], ("stress", "imexRK3", 1.5))

    def test_missing_gui_fixture_or_repeat_never_falls_back_to_headless(self):
        groups = [self.group("fast-turbo", "imexRK3", 100, "a"),
                  self.group("fast-turbo", "imexRK3", 100, "b"),
                  self.group("fast-turbo-live", "imexRK3", 80, "a"),
                  self.group("fast-turbo-live", "imexRK3", 80, "b", runs=4)]
        self.assertEqual(rank_timed_candidates(groups, [("stress", "imexRK3", 1.5)],
                                               label="fast-turbo-live", fixture_count=2,
                                               minimum_runs=5), [])

    def test_operator_results_cannot_be_combined_to_fill_missing_runs(self):
        groups = [self.group("fast-turbo-live", "imexRK3", 80, "a", operator_name="stress"),
                  self.group("fast-turbo-live", "imexRK3", 70, "b", operator_name="advective")]
        self.assertEqual(rank_timed_candidates(groups, [("stress", "imexRK3", 1.5),
                                                        ("advective", "imexRK3", 1.5)],
                                               label="fast-turbo-live", fixture_count=2,
                                               minimum_runs=5), [])


if __name__ == "__main__":
    unittest.main()
