#!/usr/bin/env python3
"""Screen numerical methods at matched accuracy, then time validated finalists.

This is deliberately a sequential driver: competing GPU jobs invalidate the
comparison. Full defaults cover 2 spectra, 2 seeds and 4 physical checkpoints;
use --quick for a short feasibility run, never a production performance claim.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from typing import Any

import numpy as np

from validate import (METHODS, OPERATORS, FourierGrid, SolverResult, executable_path,
                      long_diffusion_check, nested, pao_fixture, run_solver)


CFL_VALUES = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
METRICS = ("vorticity", "energy", "enstrophy")


def reference_gate(previous_difference: dict[str, float],
                   pair_difference: dict[str, float],
                   cross_difference: dict[str, float],
                   baseline_error: dict[str, float], *, floor: float,
                   calibration_passed: bool) -> dict[str, Any]:
    """Empirical convergence evidence, not a rigorous reference-error bound."""
    uncertainty = {key: max(pair_difference[key], cross_difference[key]) for key in METRICS}
    limits = {key: max(floor, 0.1*baseline_error[key]) for key in METRICS}
    convergence_limits = {key: max(0.8*previous_difference[key],
                                    floor if calibration_passed else 0) for key in METRICS}
    decreasing = all(pair_difference[key] <= convergence_limits[key] for key in METRICS)
    accurate_enough = all(uncertainty[key] <= limits[key] for key in METRICS)
    return {"converged": calibration_passed and decreasing and accurate_enough,
            "calibration_passed": calibration_passed,
            "decreasing_or_calibrated_plateau": decreasing,
            "uncertainty_within_budget": accurate_enough,
            "uncertainty_estimate": uncertainty,
            "successive_difference": pair_difference,
            "previous_successive_difference": previous_difference,
            "cross_method_difference": cross_difference,
            "uncertainty_limits": limits, "convergence_limits": convergence_limits,
            "uncertainty_kind": "empirical estimate; not a rigorous error bound"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metadata(paths: dict[str, Path | None]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python": sys.version,
        "numpy": np.__version__,
        "executables": {name: {"path": str(path), "sha256": sha256(path)}
                        for name, path in paths.items() if path is not None}}
    for name, command in (("hardware", ["system_profiler", "SPHardwareDataType", "SPDisplaysDataType", "-json"]),
                          ("power", ["pmset", "-g", "batt"]),
                          ("thermal", ["pmset", "-g", "therm"])):
        try:
            item = subprocess.run(command, capture_output=True, text=True, timeout=20)
            result[name] = item.stdout if item.returncode == 0 else item.stderr
        except (OSError, subprocess.TimeoutExpired) as error:
            result[name] = str(error)
    return result


def validate_endpoint(result: SolverResult, endpoint: float) -> None:
    if abs(result.simulation_time-endpoint) > max(2e-7, endpoint*2e-7):
        raise ValueError(f"Requested endpoint {endpoint}, completed {result.simulation_time}")


def row_for(result: SolverResult, *, label: str, fixture: str, endpoint: float,
            method: str, cfl: float, phase: str, repeat: int = 0,
            operator_name: str = "stress") -> dict[str, Any]:
    validate_endpoint(result, endpoint)
    row = {"phase": phase, "label": label, "fixture": fixture, "endpoint": endpoint,
           "integrator": method, "operator": operator_name, "cfl": cfl, "repeat": repeat,
           "seconds": result.seconds, "simulation_time": result.simulation_time,
           "simtime_per_s": result.simulation_time/result.seconds,
           "steps": result.steps, "steps_per_s": result.steps/result.seconds,
           "reused_trajectory": result.reused}
    for key in ("presented_frames_per_second", "p95_frame_interval_ms", "longest_frame_interval_ms"):
        value = nested(result.report, f"presentation.{key}")
        if value is not None:
            row[key] = value
    return row


def save_rows(directory: Path, rows: list[dict]) -> None:
    (directory / "runs.json").write_text(json.dumps(rows, indent=2) + "\n")
    keys = sorted({key for row in rows for key in row})
    with (directory / "runs.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (dict, list)) else value
                             for key, value in row.items()})


def robust_summary(rows: list[dict]) -> dict[str, Any]:
    groups: dict[tuple, list[float]] = {}
    for row in rows:
        if row.get("phase") != "timing" or row.get("status") == "error":
            continue
        key = (row["label"], row["fixture"], row["integrator"], row["cfl"], row.get("operator", "stress"))
        groups.setdefault(key, []).append(row["simtime_per_s"])
    summaries = []
    for (label, fixture, method, cfl, operator_name), values in groups.items():
        median = statistics.median(values)
        mad = statistics.median(abs(value-median) for value in values)
        summaries.append({"label": label, "fixture": fixture, "integrator": method, "operator": operator_name,
                          "cfl": cfl, "runs": len(values), "median_simtime_per_s": median,
                          "relative_mad": mad/median, "min": min(values), "max": max(values)})
    for item in summaries:
        baseline = next((other for other in summaries if other["label"] == "metal-reference"
                         and other["fixture"] == item["fixture"]), None)
        if baseline is None or item is baseline:
            continue
        gain = item["median_simtime_per_s"]/baseline["median_simtime_per_s"]-1
        noise = 3*math.hypot(item["relative_mad"], baseline["relative_mad"])
        item["speedup_vs_metal"] = 1+gain
        item["gain_exceeds_noise_and_2_percent"] = gain > max(0.02, noise)
        item["noise_threshold"] = noise
    return {"groups": summaries,
            "timing_rule": "median of interleaved runs; gain must exceed 2% and 3 combined relative MADs"}


def rank_timed_candidates(groups: list[dict], eligible: list[tuple[str, str, float]], *,
                          label: str, fixture_count: int, minimum_runs: int) -> list[tuple[str, str, float]]:
    """Rank one presentation mode; never substitute headless data for missing GUI runs."""
    rates: dict[tuple[str, str, float], list[float]] = {}
    for group in groups:
        candidate = (group.get("operator", "stress"), group["integrator"], group["cfl"])
        if group["label"] == label and candidate in eligible and group["runs"] >= minimum_runs:
            rates.setdefault(candidate, []).append(group["median_simtime_per_s"])
    return sorted((candidate for candidate, values in rates.items() if len(values) == fixture_count),
                  key=lambda candidate: statistics.geometric_mean(rates[candidate]), reverse=True)


def compare(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    meta = metadata({"fast_turbo": args.app, "metal": args.metal,
                     "mlx": args.mlx, "gui": args.gui_app})
    meta["configuration"] = {key: str(value) if isinstance(value, Path) else value
                             for key, value in vars(args).items()}
    meta["accuracy_baseline"] = ("original Metal adapter, CFL=1.5, batch=8"
                                  if args.metal else "new-app IMEX RK3; original Metal not supplied")
    meta["limitations"] = ([] if args.metal else [
        "No predecessor executable supplied: results cannot establish parity with original Metal."])
    meta["limitations"] += ([] if args.gui_app else ["Visible 60 Hz throughput was not measured."])
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    rows: list[dict] = []
    candidates = [(operator_name, method, cfl) for operator_name in args.operators
                  for method in args.integrators for cfl in args.cfl]
    eligible = {candidate: True for candidate in candidates}
    screen_rejections: dict[tuple[str, str, float], dict[str, Any]] = {}
    candidate_rates = {candidate: [] for candidate in candidates}
    fixture_data: list[dict] = []
    reference_records: list[dict] = []
    baseline_app = args.metal or args.app
    cross_method = "ifHeunRK3" if args.reference_integrator == "etdrk4" else "etdrk4"
    calibration: list[dict] = []
    for method in (args.reference_integrator, cross_method):
        print(f"Calibrating long low-viscosity decay: {method}", flush=True)
        try:
            details = long_diffusion_check(args.app, output / "reference-calibration" / method,
                                          method, tolerance=args.reference_floor,
                                          operator_name=args.reference_operator, resume=args.resume)
            calibration.append({"integrator": method, "operator": args.reference_operator,
                                "passed": True, "details": details})
        except Exception as error:
            calibration.append({"integrator": method, "operator": args.reference_operator,
                                "passed": False, "error": str(error)})
            print(f"Reference calibration failed: {method}: {error}", flush=True)
    calibration_passed = all(item["passed"] for item in calibration)
    (output / "reference-calibration.json").write_text(json.dumps({
        "all_passed": calibration_passed, "floor": args.reference_floor,
        "tests": calibration}, indent=2)+"\n")

    def execute(directory: Path, field: np.ndarray, *, executable: Path = args.app,
                method: str = "imexRK3", cfl: float = 1.5, reynolds: float,
                endpoint: float, dt: float | None = None, gui: bool = False,
                initial: Path, operator_name: str | None = None,
                allow_resume: bool = True) -> SolverResult:
        run = run_solver(executable, field, directory, method=method, cfl=cfl,
                         reynolds=reynolds, batch=8, until=endpoint, dt=dt,
                         timeout=args.timeout, shared_initial=initial, gui=gui,
                         operator_name=operator_name, resume=args.resume and allow_resume)
        validate_endpoint(run, endpoint)
        return run

    for n in args.resolutions:
        grid = FourierGrid(n)
        for k0, reynolds in args.fixtures:
            for seed in args.seeds:
                name = f"n{n}-k{k0:g}-re{reynolds:g}-seed{seed}"
                print(f"Screening {name}", flush=True)
                directory = output / name
                directory.mkdir(parents=True, exist_ok=True)
                field = pao_fixture(n, k0, seed)
                initial = directory / "initial.f32"
                field.tofile(initial)
                initial_state = grid.transform(field)
                tau = grid.invariants(initial_state)["initial_turnover_time"]
                (directory / "fixture.json").write_text(json.dumps({
                    "resolution": n, "padded_resolution": grid.m, "k0": k0,
                    "reynolds": reynolds, "seed": seed, "initial_file": str(initial),
                    "initial_sha256": sha256(initial), "invariants": grid.invariants(initial_state),
                    "generator": "shared PCG64 Hermitian Pao fixture; unit rms speed",
                    "configuration_note": "Serialized input overrides each executable's initial spectrum/RNG flags."
                }, indent=2)+"\n")
                endpoints = [value*tau for value in args.turnovers]
                data = {"name": name, "grid": grid, "field": field, "initial": initial,
                        "reynolds": reynolds, "endpoint": endpoints[-1], "tau": tau}
                fixture_data.append(data)
                u, v = grid.velocity_spectra(initial_state)
                velocity_max = float(np.max(np.abs(grid.padded_physical(u))
                                             + np.abs(grid.padded_physical(v))))
                reference_dt = min(0.01*tau, args.reference_cfl*(2*math.pi/grid.m)/max(velocity_max, 1e-30))
                (directory / "reference-start.json").write_text(json.dumps({
                    "operator": args.reference_operator, "reference_cfl": args.reference_cfl,
                    "starting_dt": reference_dt, "initial_velocity_l1_max": velocity_max,
                    "rule": "min(0.01*initial_tau, reference_cfl*(2*pi/M)/max(abs(u)+abs(v)))",
                    "note": "This starting Courant convention uses padded spacing; refinement gates remain mandatory."
                }, indent=2)+"\n")

                for checkpoint, endpoint in enumerate(endpoints):
                    prefix = directory / f"checkpoint-{checkpoint}"
                    baseline = execute(prefix / "metal-reference", field, executable=baseline_app,
                                       reynolds=reynolds, endpoint=endpoint, initial=initial,
                                       operator_name=None if args.metal else "stress")
                    rows.append(row_for(baseline, label="metal-reference", fixture=name,
                                        endpoint=endpoint, method="imexRK3", cfl=1.5, phase="screen"))
                    coarse = execute(prefix / "reference-0", field,
                                     method=args.reference_integrator, reynolds=reynolds,
                                     endpoint=endpoint, dt=reference_dt, initial=initial,
                                     operator_name=args.reference_operator)
                    convergence = None
                    fine = coarse
                    previous_difference = None
                    levels: list[dict] = []
                    for refinement in range(1, args.max_refinements+1):
                        fine = execute(prefix / f"reference-{refinement}", field,
                                       method=args.reference_integrator, reynolds=reynolds,
                                       endpoint=endpoint, dt=reference_dt/2**refinement, initial=initial,
                                       operator_name=args.reference_operator)
                        difference = grid.error(fine.spectrum, coarse.spectrum)
                        baseline_error = grid.error(baseline.spectrum, fine.spectrum)
                        level = {"refinement": refinement, "dt": reference_dt/2**refinement,
                                 "successive_difference": difference, "baseline_error": baseline_error}
                        # h, h/2, h/4 are mandatory. A single adjacent pair can
                        # falsely agree when both updates lose tiny increments.
                        if previous_difference is not None:
                            cross = execute(prefix / f"cross-reference-{refinement}", field,
                                            method=cross_method, reynolds=reynolds, endpoint=endpoint,
                                            dt=reference_dt/2**refinement, initial=initial,
                                            operator_name=args.reference_operator)
                            cross_difference = grid.error(cross.spectrum, fine.spectrum)
                            gate = reference_gate(previous_difference, difference, cross_difference,
                                                  baseline_error, floor=args.reference_floor,
                                                  calibration_passed=calibration_passed)
                            level["gate"] = gate
                            convergence = {"fixture": name, "endpoint": endpoint,
                                           "reference_integrator": args.reference_integrator,
                                           "reference_operator": args.reference_operator,
                                           "cross_reference_integrator": cross_method,
                                           "refinement": refinement, "dt": reference_dt/2**refinement,
                                           "baseline_error": baseline_error, **gate}
                        levels.append(level)
                        if convergence is not None and convergence["converged"]:
                            break
                        coarse = fine
                        previous_difference = difference
                    assert convergence is not None
                    convergence["levels"] = levels
                    reference_records.append(convergence)
                    if checkpoint == len(endpoints)-1:
                        # Retain the exact final reference and original budget
                        # for checking the trajectories of timed GUI/headless runs.
                        data["reference_spectrum"] = fine.spectrum
                        data["reference_gate"] = convergence
                    (output / "references.json").write_text(json.dumps(reference_records, indent=2)+"\n")
                    for operator_name, method, cfl in candidates:
                        candidate = (operator_name, method, cfl)
                        label = f"{operator_name}-{method}-cfl{cfl:g}"
                        if args.prune_failed and candidate in screen_rejections:
                            reason = screen_rejections[candidate]
                            print(f"  t/tau={args.turnovers[checkpoint]:g} {label}: skipped after {reason['kind']}", flush=True)
                            rows.append({"phase": "screen", "label": "fast-turbo", "fixture": name,
                                         "endpoint": endpoint, "integrator": method, "cfl": cfl,
                                         "operator": operator_name, "status": "skipped-after-failure",
                                         "reason": reason})
                            save_rows(output, rows)
                            continue
                        print(f"  t/tau={args.turnovers[checkpoint]:g} {label}", flush=True)
                        try:
                            run = execute(prefix / label, field, method=method, cfl=cfl,
                                          reynolds=reynolds, endpoint=endpoint, initial=initial,
                                          operator_name=operator_name)
                            error = grid.error(run.spectrum, fine.spectrum)
                            passed = convergence["converged"] and all(
                                error[key] <= convergence["baseline_error"][key]
                                + 2*convergence["uncertainty_estimate"][key] + args.reference_floor
                                for key in METRICS)
                            row = row_for(run, label="fast-turbo", fixture=name, endpoint=endpoint,
                                          method=method, cfl=cfl, phase="screen", operator_name=operator_name)
                            row.update(error=error, accuracy_passed=passed)
                            rows.append(row)
                            eligible[candidate] &= passed
                            # A bad reference cannot establish that a candidate
                            # is inaccurate. Only independently gated failures
                            # justify skipping the candidate's later trajectories.
                            if convergence["converged"] and not passed:
                                screen_rejections.setdefault(candidate, {
                                    "kind": "accuracy failure against a valid reference",
                                    "fixture": name, "endpoint": endpoint, "error": error,
                                    "acceptance_limits": {
                                        key: convergence["baseline_error"][key]
                                        + 2*convergence["uncertainty_estimate"][key] + args.reference_floor
                                        for key in METRICS}})
                            if checkpoint == len(endpoints)-1:
                                candidate_rates[candidate].append(run.simulation_time/run.seconds)
                        except Exception as error:
                            eligible[candidate] = False
                            screen_rejections.setdefault(candidate, {
                                "kind": "solver execution failure", "fixture": name,
                                "endpoint": endpoint, "error": str(error)})
                            rows.append({"phase": "screen", "label": "fast-turbo", "fixture": name,
                                         "endpoint": endpoint, "integrator": method, "cfl": cfl,
                                         "operator": operator_name,
                                         "status": "error", "error": str(error)})
                        save_rows(output, rows)

    # Aggregate across resolutions and fixtures using a geometric mean.
    rankings = sorted((candidate for candidate in candidates if eligible[candidate]),
                      key=lambda candidate: statistics.geometric_mean(candidate_rates[candidate]), reverse=True)
    finalists = rankings[:args.finalists]
    if not finalists:
        print("No numerical candidate met the reference gate; timing established IMEX RK3 only.", flush=True)
        finalists = [("stress", "imexRK3", 1.5)]

    if not args.screen_only:
        for fixture in fixture_data:
            jobs = [("fast-turbo", args.app, method, cfl, False, operator_name)
                    for operator_name, method, cfl in finalists]
            if args.metal:
                jobs.append(("metal-reference", args.metal, "imexRK3", 1.5, False, None))
            if args.mlx:
                jobs.append(("mlx-reference", args.mlx, "ifHeunRK3", 1.5, False, None))
            if args.gui_app:
                jobs.extend(("fast-turbo-live", args.gui_app, method, cfl, True, operator_name)
                            for operator_name, method, cfl in finalists)
            for repeat in range(args.runs):
                # Rotate order to reduce systematic thermal/order advantage.
                offset = repeat % len(jobs)
                ordered = jobs[offset:] + jobs[:offset]
                for label, executable, method, cfl, gui, operator_name in ordered:
                    print(f"Timing {fixture['name']} {label} {operator_name or 'legacy'} {method} CFL={cfl:g}, run {repeat+1}", flush=True)
                    directory = (output / fixture["name"] / "timing"
                                 / f"{repeat}-{label}-{operator_name or 'legacy'}-{method}-cfl{cfl:g}")
                    try:
                        run = execute(directory, fixture["field"], executable=executable, method=method,
                                      cfl=cfl, reynolds=fixture["reynolds"], endpoint=fixture["endpoint"],
                                      initial=fixture["initial"], gui=gui, operator_name=operator_name,
                                      allow_resume=False)
                        row = row_for(run, label=label, fixture=fixture["name"],
                                      endpoint=fixture["endpoint"], method=method, cfl=cfl,
                                      phase="timing", repeat=repeat,
                                      operator_name=operator_name or ("advective" if label == "mlx-reference" else "stress"))
                        gate = fixture["reference_gate"]
                        error = fixture["grid"].error(run.spectrum, fixture["reference_spectrum"])
                        passed = gate["converged"] and all(
                            error[key] <= gate["baseline_error"][key]
                            + 2*gate["uncertainty_estimate"][key] + args.reference_floor
                            for key in METRICS)
                        row.update(error=error, accuracy_passed=passed)
                        rows.append(row)
                        if label in ("fast-turbo", "fast-turbo-live") and not passed:
                            eligible[(operator_name, method, cfl)] = False
                            print(f"Timed trajectory failed its original accuracy budget: {label} {operator_name} {method} CFL={cfl:g}: {error}", flush=True)
                    except Exception as error:
                        print(f"Timing failed: {label} {method} CFL={cfl:g}: {error}", flush=True)
                        rows.append({"phase": "timing", "label": label, "fixture": fixture["name"],
                                     "endpoint": fixture["endpoint"], "integrator": method, "cfl": cfl,
                                     "operator": operator_name or ("advective" if label == "mlx-reference" else "stress"),
                                     "repeat": repeat, "status": "error", "error": str(error)})
                        if label in ("fast-turbo", "fast-turbo-live"):
                            eligible[(operator_name, method, cfl)] = False
                    save_rows(output, rows)

    summary = robust_summary(rows)
    selection_eligible = [candidate for candidate in rankings if eligible[candidate]]
    selection_label = "fast-turbo-live" if args.gui_app else "fast-turbo"
    complete_headless = rank_timed_candidates(summary["groups"], selection_eligible, label="fast-turbo",
                                              fixture_count=len(fixture_data), minimum_runs=args.runs)
    timing_eligible = complete_headless if args.gui_app else selection_eligible
    timed_rankings = rank_timed_candidates(summary["groups"], timing_eligible, label=selection_label,
                                           fixture_count=len(fixture_data), minimum_runs=args.runs)
    selection = (timed_rankings[0] if timed_rankings else
                 selection_eligible[0] if selection_eligible and not args.gui_app and args.screen_only else None)
    if timed_rankings:
        selection_basis = ("geometric mean of interleaved GUI median physical-throughput across fixtures"
                           if args.gui_app else
                           "geometric mean of interleaved headless median physical-throughput across fixtures")
    else:
        selection_basis = ("GUI timing incomplete; no default selected" if args.gui_app else
                           "headless screening time-to-target; no complete finalist timings" if args.screen_only else
                           "headless timing incomplete; no default selected")
    full_matrix = (1024 in args.resolutions and set(args.fixtures) >= {(4, 50000), (10, 24000)}
                   and set(args.seeds) >= {2, 7} and set(args.turnovers) >= {0.25, 0.5, 1, 2}
                   and set(args.integrators) == set(METHODS) and set(args.cfl) >= set(CFL_VALUES)
                   and args.runs >= 5 and not args.screen_only)
    summary.update({"schema_version": 1, "all_references_converged": all(
        item["converged"] for item in reference_records),
        "reference_calibration_passed": calibration_passed,
        "reference_uncertainty_kind": "empirical estimate with three-level refinement and cross-method check; not a rigorous bound",
        "validated_candidates": [{"operator": operator_name, "integrator": method, "cfl": cfl}
                                 for operator_name, method, cfl in selection_eligible],
        "timing_rejected_candidates": [{"operator": operator_name, "integrator": method, "cfl": cfl}
                                       for operator_name, method, cfl in rankings
                                       if not eligible[(operator_name, method, cfl)]],
        "selected_candidate": ({"operator": selection[0], "integrator": selection[1], "cfl": selection[2]}
                               if selection else None),
        "selection_basis": selection_basis,
        "selection_mode": "live" if args.gui_app else "headless",
        "selection_has_complete_timing": bool(timed_rankings),
        "failed_timing_jobs": sum(row.get("phase") == "timing" and row.get("status") == "error"
                                  for row in rows),
        "pruning_enabled": args.prune_failed,
        "pruned_screen_trajectories": sum(row.get("status") == "skipped-after-failure" for row in rows),
        "screen_rejections": [{"operator": operator_name, "integrator": method, "cfl": cfl,
                               "reason": reason}
                              for (operator_name, method, cfl), reason in screen_rejections.items()],
        "original_metal_accuracy_comparison": args.metal is not None,
        "full_matrix": full_matrix, "limitations": meta["limitations"]})
    (output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def fixture_argument(value: str) -> tuple[float, float]:
    try:
        k0, reynolds = map(float, value.split(":"))
        if k0 > 0 and reynolds > 0:
            return k0, reynolds
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Fixture must be positive k0:reynolds, for example 4:50000")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=executable_path)
    parser.add_argument("--metal", type=executable_path, help="Matched-input original Metal benchmark adapter")
    parser.add_argument("--mlx", type=executable_path, help="Matched-input padded Astra benchmark adapter")
    parser.add_argument("--gui-app", type=executable_path, help="Native app supporting --gui-benchmark")
    parser.add_argument("--output", type=Path,
                        default=Path("Benchmarks")/datetime.now().strftime("comparison-%Y%m%d-%H%M%S"))
    parser.add_argument("--resolutions", nargs="+", type=int, default=[1024])
    parser.add_argument("--fixtures", nargs="+", type=fixture_argument, default=[(4, 50000), (10, 24000)])
    parser.add_argument("--seeds", nargs="+", type=int, default=[2, 7])
    parser.add_argument("--turnovers", nargs="+", type=float, default=[0.25, 0.5, 1, 2])
    parser.add_argument("--integrators", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--operators", nargs="+", choices=OPERATORS, default=["stress"],
                        help="Candidate nonlinear formulations; defaults to stress")
    parser.add_argument("--cfl", nargs="+", type=float, default=list(CFL_VALUES))
    parser.add_argument("--reference-integrator", choices=METHODS, default="etdrk4")
    parser.add_argument("--reference-operator", choices=OPERATORS, default="advective",
                        help="Reference/calibration operator; legacy adapters receive no operator flag")
    parser.add_argument("--reference-cfl", type=float, default=1.0,
                        help="Starting reference Courant factor on padded spacing; refinement still required")
    parser.add_argument("--max-refinements", type=int, default=4,
                        help="At least 2: always evaluate h, h/2, h/4 and a second method at the fine dt")
    parser.add_argument("--reference-floor", type=float, default=2e-6,
                        help="FP32 plateau allowance, conditional on independent analytic calibration (default 2e-6)")
    parser.add_argument("--finalists", type=int, default=3)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--screen-only", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="Reuse hashed reference/screen trajectories; finalist timings always run afresh")
    parser.add_argument("--prune-failed", action="store_true",
                        help="Skip later screens only after a proven candidate accuracy/execution failure")
    parser.add_argument("--quick", action="store_true", help="N128, one fixture/seed, 0.05 turnover, three CFLs, two repeats")
    args = parser.parse_args()
    if args.quick:
        args.resolutions, args.fixtures, args.seeds = [128], [(4, 50000)], [2]
        args.turnovers, args.cfl, args.runs = [0.05], [1, 1.5, 2], 2
    if (args.runs < 1 or args.finalists < 1 or args.max_refinements < 2
            or not math.isfinite(args.reference_floor) or args.reference_floor <= 0
            or not math.isfinite(args.reference_cfl) or args.reference_cfl <= 0
            or any(value <= 0 for value in args.cfl + args.turnovers)
            or args.turnovers != sorted(args.turnovers)):
        parser.error("Counts and timesteps must be positive; max-refinements>=2; turnover checkpoints must be sorted")
    summary = compare(args)
    return 0 if summary["all_references_converged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
