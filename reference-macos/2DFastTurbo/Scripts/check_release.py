#!/usr/bin/env python3
"""Sequential release acceptance on the app's native seed-2 factory field.

Seven mandatory checks cover the zero-step default configuration/native
N1024 export; N128/256/512 at four turnovers; N2048/4096 at sixteen steps with
snapshots; and N1024 at forty turnovers. Stability runs omit --initial.
The optional eighth check compares both the direct native and re-imported
N1024 trajectories at two turnovers against the same converged reference and
legacy accuracy budget. Export/import error must remain below the reference
floor. These results are distinct from the PCG64 selection matrix fixtures.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import time
from typing import Any

import numpy as np

from compare import METRICS, reference_gate, validate_endpoint
from validate import (METHODS, OPERATORS, FourierGrid, SolverResult, executable_path,
                      file_hash, long_diffusion_check, run_solver)


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"Expected finite{' positive' if positive else ''} {label}, got {value}")
    return result


def check_fields(path: Path, n: int) -> dict[str, Any]:
    expected = 3*n*n*4
    if path.stat().st_size != expected:
        raise ValueError(f"Snapshot size {path.stat().st_size}; expected {expected} bytes")
    fields = np.memmap(path, dtype="<f4", mode="r", shape=(3, n*n))
    maxima = []
    for plane in fields:
        maximum = 0.0
        for offset in range(0, n*n, 262144):
            block = plane[offset:offset+262144]
            if not np.all(np.isfinite(block)):
                raise ValueError("Snapshot contains a nonfinite vorticity or velocity sample")
            maximum = max(maximum, float(np.max(np.abs(block))))
        maxima.append(maximum)
    return {"bytes": expected, "layout": "Float32 [3,N,N]; omega,u,v planes in [y,x] order",
            "max_abs_omega_u_v": maxima, "all_finite": True}


def factory_run(args: argparse.Namespace, selected: dict[str, Any], directory: Path,
                n: int, *, steps: int | None = None, turnovers: float | None = None,
                initial_export: bool = False, export_spectrum: bool = False,
                verify_defaults: bool = False) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    result_file, fields = directory/"result.json", directory/"fields.f32"
    command = [str(args.app), "--benchmark", "--batch", "8", "--warmup", "0" if initial_export else "8",
               "--output", str(result_file), "--fields", str(fields)]
    if not verify_defaults:
        command += ["--resolution", str(n), "--operator", selected["operator"],
                    "--integrator", selected["integrator"], "--cfl", str(selected["cfl"]),
                    "--seed", "2", "--k0", "4", "--reynolds", "50000"]
    if (steps is None) == (turnovers is None):
        raise ValueError("Specify exactly one factory endpoint")
    command += ["--steps", str(steps)] if steps is not None else ["--turnovers", str(turnovers)]
    if initial_export or export_spectrum:
        command += ["--spectrum", str(directory/"spectrum.c64")]
        (directory/"spectrum.c64").unlink(missing_ok=True)
    save(directory/"command.json", command)
    result_file.unlink(missing_ok=True)
    fields.unlink(missing_ok=True)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired as error:
        def decoded(value: Any) -> str:
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        (directory/"stdout.txt").write_text(decoded(error.stdout))
        (directory/"stderr.txt").write_text(decoded(error.stderr) + f"\nTimed out after {args.timeout}s.\n")
        raise
    (directory/"stdout.txt").write_text(completed.stdout)
    (directory/"stderr.txt").write_text(completed.stderr)
    if completed.returncode:
        raise RuntimeError(f"Factory solver exited {completed.returncode}: {completed.stderr[-1500:]}")
    report = json.loads(result_file.read_text())
    config, before, after = report["config"], report["initial_stats"], report["stats"]
    if (report.get("initial_source") != "seeded-pao" or report.get("initial_file") is not None
            or report.get("initial_sha256") is not None):
        raise ValueError("Factory run did not use its native seeded initializer")
    expected_config = {"resolution": n, "padded_resolution": 3*n//2, "seed": 2, "k0": 4,
                       "reynolds": 50000, "operator": selected["operator"],
                       "integrator": selected["integrator"], "fixed_dt": None}
    if any(config.get(key) != value for key, value in expected_config.items()):
        raise ValueError(f"{'Unmodified defaults do not match selection' if verify_defaults else 'Unexpected factory configuration'}: {config}")
    if abs(finite_number(config["cfl"], "CFL", positive=True)-selected["cfl"]) > 1e-6:
        raise ValueError("Factory run did not use the selected CFL")
    if before.get("is_finite") is not True or after.get("is_finite") is not True:
        raise ValueError("Solver did not report finite initial and final states")
    tau = finite_number(before["initial_turnover_time"], "initial turnover time", positive=True)
    completed_time = finite_number(after["simulation_time"], "completed time")
    finite_number(after["dt"], "completed timestep", positive=True)
    if before["steps"] != 0 or before["simulation_time"] != 0:
        raise ValueError("Warmup was not reset before the factory run")
    if steps is not None:
        if after["steps"] != steps or (steps == 0 and completed_time != 0):
            raise ValueError("Factory step count or initial clock differs from the request")
        if steps > 0 and completed_time <= 0:
            raise ValueError("Factory steps made no physical progress")
    else:
        endpoint = turnovers*tau
        validate_endpoint(SolverResult(np.empty(0, dtype=np.complex128), report, command), endpoint)
        target = finite_number(report["target_time"], "reported target", positive=True)
        if abs(target-endpoint) > max(2e-7, endpoint*2e-7) or after["steps"] <= 0:
            raise ValueError("Factory turnover endpoint or step count is invalid")
    ratios = {}
    for key in ("energy", "enstrophy"):
        initial = finite_number(before[key], "initial "+key, positive=True)
        final = finite_number(after[key], "final "+key)
        if final < 0 or final > initial*(1+args.invariant_tolerance):
            raise ValueError(f"Unforced {key} grew beyond tolerance: initial={initial}, final={final}")
        ratios[key] = final/initial
    return {"resolution": n, "padded_resolution": 3*n//2,
            "initial_source": "native factory seeded-pao; no --initial",
            "unmodified_defaults_verified": verify_defaults,
            "steps": after["steps"], "simulation_time": completed_time,
            "turnovers": completed_time/tau, "initial_turnover_time": tau,
            "final_over_initial": ratios, "invariant_growth_tolerance": args.invariant_tolerance,
            "snapshot": check_fields(fields, n), "result_file": str(result_file)}


def factory_accuracy(args: argparse.Namespace, selected: dict[str, Any], directory: Path,
                     export: Path, reference_floor: float, reference_cfl: float) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    n = 1024
    grid = FourierGrid(n)
    fields = np.memmap(export/"fields.f32", dtype="<f4", mode="r", shape=(3, n, n))
    field = np.array(fields[0], dtype=np.float32, copy=True)
    initial = directory/"initial.f32"
    field.astype("<f4").tofile(initial)
    export_report = json.loads((export/"result.json").read_text())
    tau = float(export_report["initial_stats"]["initial_turnover_time"])
    endpoint = 2*tau
    initial_state = grid.transform(field)
    if export_report.get("spectrum_normalization") != "fourier-series":
        raise ValueError("Native factory spectrum is not declared Fourier-series normalized")
    native_spectrum = np.fromfile(export/"spectrum.c64", dtype="<c8").reshape(n, n)
    if not np.all(np.isfinite(native_spectrum)):
        raise ValueError("Native factory spectrum contains nonfinite values")
    roundtrip = grid.error(initial_state, native_spectrum)
    roundtrip_gate = {"error": roundtrip, "limit": reference_floor,
                      "passed": all(math.isfinite(roundtrip[key]) and roundtrip[key] <= reference_floor
                                    for key in METRICS)}
    save(directory/"factory-roundtrip.json", roundtrip_gate)
    if not roundtrip_gate["passed"]:
        raise ValueError("Factory export/import error exceeds the reference floor; see factory-roundtrip.json")
    calibration = []
    for method in ("etdrk4", "ifHeunRK3"):
        details = long_diffusion_check(args.app, directory/"calibration"/method, method,
                                      tolerance=reference_floor, operator_name="advective")
        calibration.append({"integrator": method, "passed": True, "details": details})
    save(directory/"calibration.json", calibration)

    def execute(name: str, *, app: Path = args.app, method: str = "etdrk4",
                operator_name: str | None = "advective", cfl: float = 1.5,
                dt: float | None = None) -> SolverResult:
        run = run_solver(app, field, directory/name, method=method, operator_name=operator_name,
                         reynolds=50000, cfl=cfl, batch=8, dt=dt, until=endpoint,
                         timeout=args.timeout, shared_initial=initial)
        validate_endpoint(run, endpoint)
        if run.report["stats"].get("is_finite") is not True:
            raise ValueError(f"{name} did not report a finite state")
        return run

    u, v = grid.velocity_spectra(initial_state)
    maximum = float(np.max(np.abs(grid.padded_physical(u))+np.abs(grid.padded_physical(v))))
    h = min(0.01*tau, reference_cfl*(2*math.pi/grid.m)/max(maximum, 1e-30))
    save(directory/"fixture.json", {
        "generator": "native FastTurbo factory seed2 exported as physical Float32 vorticity",
        "serialized_initial_sha256": file_hash(initial), "export_result": str(export/"result.json"),
        "export_executable_sha256": file_hash(args.app), "factory_turnover_time": tau,
        "endpoint": endpoint, "turnovers": 2, "roundtrip_error": roundtrip,
        "reference_starting_dt": h, "reference_cfl_on_padded_spacing": reference_cfl,
        "reference_floor": reference_floor,
        "scope": "Identical serialized factory samples; separate from PCG64 matrix fixtures."})
    baseline = execute("legacy-metal", app=args.metal, method="imexRK3", operator_name=None)
    candidate = execute("selected", method=selected["integrator"],
                        operator_name=selected["operator"], cfl=selected["cfl"])
    native_directory = directory/"selected-native"
    native_details = factory_run(args, selected, native_directory, n, turnovers=2, export_spectrum=True)
    native_report = json.loads((native_directory/"result.json").read_text())
    if native_report.get("spectrum_normalization") != "fourier-series":
        raise ValueError("Direct factory spectrum is not declared Fourier-series normalized")
    native_state = np.fromfile(native_directory/"spectrum.c64", dtype="<c8").reshape(n, n).astype(np.complex128)
    if not np.all(np.isfinite(native_state)):
        raise ValueError("Direct factory spectrum contains nonfinite values")
    native = SolverResult(native_state, native_report, json.loads((native_directory/"command.json").read_text()))
    validate_endpoint(native, endpoint)
    coarse = execute("reference-0", dt=h)
    previous = None
    gate = None
    levels = []
    for refinement in range(1, 5):
        fine = execute(f"reference-{refinement}", dt=h/2**refinement)
        difference = grid.error(fine.spectrum, coarse.spectrum)
        baseline_error = grid.error(baseline.spectrum, fine.spectrum)
        level = {"refinement": refinement, "dt": h/2**refinement,
                 "successive_difference": difference, "baseline_error": baseline_error}
        if previous is not None:
            cross = execute(f"cross-reference-{refinement}", method="ifHeunRK3", dt=h/2**refinement)
            gate = reference_gate(previous, difference, grid.error(cross.spectrum, fine.spectrum),
                                  baseline_error, floor=reference_floor, calibration_passed=True)
            level["gate"] = gate
        levels.append(level)
        save(directory/"reference-levels.json", levels)
        if gate is not None and gate["converged"]:
            break
        coarse, previous = fine, difference
    assert gate is not None
    imported_error = grid.error(candidate.spectrum, fine.spectrum)
    native_error = grid.error(native.spectrum, fine.spectrum)
    limits = {key: baseline_error[key]+2*gate["uncertainty_estimate"][key]+reference_floor
              for key in METRICS}
    passed = (gate["converged"] and roundtrip_gate["passed"]
              and all(imported_error[key] <= limits[key] and native_error[key] <= limits[key]
                      for key in METRICS))
    result = {"passed": passed, "reference_gate": gate, "levels": levels,
              "selected_native_error": native_error, "selected_reimported_error": imported_error,
              "legacy_error": baseline_error, "acceptance_limits": limits,
              "factory_export_roundtrip_gate": roundtrip_gate, "native_run": native_details,
              "endpoint": endpoint, "turnovers": 2, "initial_sha256": file_hash(initial),
              "scope": "direct native and re-imported factory trajectories against the same reference"}
    save(directory/"accuracy.json", result)
    if not passed:
        raise ValueError("Factory accuracy/reference gate failed; see accuracy/accuracy.json")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True, help="Completed comparison summary.json")
    parser.add_argument("--app", type=executable_path, required=True)
    parser.add_argument("--metal", type=executable_path, help="Required with --accuracy; captured legacy adapter")
    parser.add_argument("--output", type=Path, default=Path("Benchmarks/release-factory"))
    parser.add_argument("--accuracy", action="store_true", help="Also run the native-field 2-turnover accuracy gate")
    parser.add_argument("--timeout", type=float, default=900, help="Per solver process, in seconds")
    parser.add_argument("--invariant-tolerance", type=float, default=2e-4)
    args = parser.parse_args()
    if args.accuracy and args.metal is None:
        parser.error("--accuracy requires --metal; no new-solver substitute is used for legacy accuracy")
    if (not math.isfinite(args.timeout) or args.timeout <= 0
            or not math.isfinite(args.invariant_tolerance) or not 0 <= args.invariant_tolerance <= 0.01):
        parser.error("Require positive finite timeout and invariant tolerance between 0 and 0.01")
    summary = json.loads(args.summary.read_text())
    selected = summary.get("selected_candidate")
    try:
        selected_cfl = float(selected.get("cfl", 0)) if isinstance(selected, dict) else 0
    except (TypeError, ValueError):
        selected_cfl = 0
    if (not isinstance(selected, dict) or selected.get("operator") not in OPERATORS
            or selected.get("integrator") not in METHODS
            or not math.isfinite(selected_cfl) or selected_cfl <= 0):
        parser.error("Summary has no valid selected operator/integrator/CFL")
    selected = {**selected, "cfl": selected_cfl}
    if summary.get("all_references_converged") is not True:
        parser.error("Selected summary does not certify convergence of all matrix references")
    if summary.get("full_matrix") is not True or summary.get("selection_has_complete_timing") is not True:
        parser.error("Release acceptance requires the completed full matrix and complete selection timing")
    if summary.get("selection_mode") != "live":
        parser.error("Release acceptance requires selection from live GUI timings")
    if summary.get("original_metal_accuracy_comparison") is not True:
        parser.error("Release acceptance requires the original-Metal accuracy comparison")
    if selected not in summary.get("validated_candidates", []):
        parser.error("Selected candidate is missing from the matrix's validated candidates")
    metadata_file = args.summary.resolve().parent/"metadata.json"
    configuration = json.loads(metadata_file.read_text()).get("configuration", {}) if metadata_file.exists() else {}
    reference_floor = finite_number(configuration.get("reference_floor", 2e-6), "reference floor", positive=True)
    reference_cfl = finite_number(configuration.get("reference_cfl", 1.0), "reference CFL", positive=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running", "expected_check_count": 8 if args.accuracy else 7,
        "all_required_checks_passed": False,
        "scope": "native factory field release acceptance; not PCG64 matrix or throughput timing",
        "selected_candidate": selected, "selection_summary": str(args.summary.resolve()),
        "selection_summary_sha256": file_hash(args.summary.resolve()),
        "selection_has_complete_timing": summary.get("selection_has_complete_timing", False),
        "executables": {"app": {"path": str(args.app), "sha256": file_hash(args.app)}},
        "accuracy_requested": args.accuracy, "per_process_timeout_seconds": args.timeout,
        "mandatory_check_scope": "Factory defaults/export; N128/256/512 at 4 turnovers; N2048/4096 at 16 steps plus snapshots; N1024 at 40 turnovers.",
        "accuracy_check_scope": "Direct native and re-imported N1024 at 2 turnovers; export/import error below the reference floor; converged reference and legacy budget.",
        "invariant_growth_tolerance": args.invariant_tolerance, "tests": [],
        "limitation": "Finite fields and dissipative invariants are stability checks, not an accuracy proof."}
    if args.metal is not None:
        report["executables"]["metal"] = {"path": str(args.metal), "sha256": file_hash(args.metal)}
    save(output/"release.json", report)

    def record(name: str, body: Any) -> None:
        started = time.monotonic()
        try:
            details = body()
            item = {"name": name, "passed": True, "details": details}
        except Exception as error:
            item = {"name": name, "passed": False, "error": str(error)}
        item["wall_seconds"] = time.monotonic()-started
        report["tests"].append(item)
        report["all_required_checks_passed"] = (
            len(report["tests"]) == report["expected_check_count"]
            and all(test["passed"] for test in report["tests"]))
        save(output/"release.json", report)
        print(f'{"PASS" if item["passed"] else "FAIL"} {name}', flush=True)

    export = output/"factory-initial-n1024"
    record("factory-defaults-and-initial-n1024", lambda: factory_run(
        args, selected, export, 1024, steps=0, initial_export=True, verify_defaults=True))
    for n in (128, 256, 512):
        record(f"factory-n{n}-4turnovers", lambda n=n: factory_run(
            args, selected, output/f"factory-n{n}-4turnovers", n, turnovers=4))
    for n in (2048, 4096):
        record(f"factory-n{n}-16steps-snapshot", lambda n=n: factory_run(
            args, selected, output/f"factory-n{n}-16steps-snapshot", n, steps=16))
    record("factory-n1024-40turnovers", lambda: factory_run(
        args, selected, output/"factory-n1024-40turnovers", 1024, turnovers=40))
    if args.accuracy:
        def accuracy() -> dict[str, Any]:
            if not report["tests"][0]["passed"]:
                raise ValueError("Factory initial export failed; accuracy input is unavailable")
            return factory_accuracy(args, selected, output/"accuracy", export, reference_floor, reference_cfl)
        record("factory-n1024-2turnovers-accuracy", accuracy)
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    report["status"] = "complete"
    save(output/"release.json", report)
    return 0 if report["all_required_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
