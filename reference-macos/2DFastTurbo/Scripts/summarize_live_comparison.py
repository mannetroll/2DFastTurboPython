#!/usr/bin/env python3
"""Audit live benchmark endpoints and summarize repeated physical throughput.

The evidence directory supplies manifest.json with fixture, endpoint,
initial_file, reference_directory, expected_runs, and runs. Each run records
id, label, integrator, operator, cfl, repeat, result_file, and spectrum_file.
Paths are relative to the evidence directory unless absolute. Spectra must be
N x N little-endian complex64 Fourier-series coefficients. This tool launches
no app and does not change any raw benchmark output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any

import numpy as np

from validate import FourierGrid, nested


METRICS = ("vorticity", "energy", "enstrophy")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve(directory: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else directory / path).resolve()


def positive(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def close(actual: Any, expected: float, name: str, tolerance: float = 2e-7) -> None:
    value = float(actual)
    if not math.isfinite(value) or abs(value - expected) > max(tolerance, tolerance * abs(expected)):
        raise ValueError(f"{name}: expected {expected}, reported {actual}")


def spectrum(path: Path, n: int, report: dict) -> np.ndarray:
    raw = np.fromfile(path, dtype="<c8")
    if raw.size != n * n:
        raise ValueError(f"{path}: expected {n*n} complex nodes, found {raw.size}")
    result = raw.reshape(n, n).astype(np.complex128)
    normalization = nested(report, "spectrum_normalization", "config.spectrum_normalization",
                           default="fourier-series")
    if normalization in ("dft", "unnormalized", "fft"):
        result /= n * n
    elif normalization not in ("fourier-series", "normalized"):
        raise ValueError(f"{path}: unknown spectrum normalization {normalization!r}")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{path}: spectrum contains nonfinite values")
    return result


def summarize(directory: Path) -> dict:
    manifest = read_json(directory / "manifest.json")
    fixture_name = manifest["fixture"]
    endpoint = positive(manifest["endpoint"], "endpoint")
    expected_runs = int(manifest["expected_runs"])
    if expected_runs < 1:
        raise ValueError("expected_runs must be positive")
    reference_directory = resolve(directory, manifest["reference_directory"])
    fixture = read_json(reference_directory / fixture_name / "fixture.json")
    n = int(fixture["resolution"])
    grid = FourierGrid(n)
    initial_file = resolve(directory, manifest["initial_file"])
    initial_sha = sha256(initial_file)
    if initial_sha != fixture["initial_sha256"]:
        raise ValueError("The imported field differs from the reference fixture")
    field = np.fromfile(initial_file, dtype="<f4")
    if field.size != n*n or not np.all(np.isfinite(field)):
        raise ValueError("The imported field must contain N x N finite float32 values")
    tau = grid.invariants(grid.transform(field.reshape(n, n)))["initial_turnover_time"]
    close(tau, fixture["invariants"]["initial_turnover_time"], "initial turnover time", 1e-12)

    references = [item for item in read_json(reference_directory / "references.json")
                  if item["fixture"] == fixture_name]
    matches = [(index, item) for index, item in enumerate(references)
               if abs(item["endpoint"] - endpoint) < 1e-12]
    if len(matches) != 1:
        raise ValueError("Exactly one reference checkpoint must match the requested endpoint")
    checkpoint, reference = matches[0]
    if reference.get("converged") is not True:
        raise ValueError("Reference checkpoint has not passed the convergence gate")
    reference_path = (reference_directory / fixture_name / f"checkpoint-{checkpoint}"
                      / f"reference-{reference['refinement']}")
    reference_report = read_json(reference_path / "result.json")
    close(nested(reference_report, "stats.simulation_time", "simulation_time", "stats.time"),
          endpoint, "reference endpoint")
    expected = spectrum(reference_path / "spectrum.c64", n, reference_report)
    metadata = read_json(reference_directory / "metadata.json")
    floor = float(metadata["configuration"]["reference_floor"])
    limits = {key: reference["baseline_error"][key]
              + 2 * reference["uncertainty_estimate"][key] + floor for key in METRICS}

    checked, groups = [], {}
    identifiers = set()
    for item in manifest["runs"]:
        identifier = item["id"]
        if identifier in identifiers:
            raise ValueError(f"Duplicate run id: {identifier}")
        identifiers.add(identifier)
        result_file = resolve(directory, item["result_file"])
        spectrum_file = resolve(directory, item["spectrum_file"])
        report = read_json(result_file)
        if report.get("passed") is not True or report.get("mode") != "gui-benchmark":
            raise ValueError(f"{identifier}: a successful live GUI benchmark is required")
        if nested(report, "initial_sha256", "config.initial_sha256") != initial_sha:
            raise ValueError(f"{identifier}: initial_sha256 is missing or mismatches the imported field")
        for name, value in (("resolution", n), ("padded_resolution", 3*n//2),
                            ("k0", fixture["k0"]), ("reynolds", fixture["reynolds"]),
                            ("seed", fixture["seed"]), ("cfl", item["cfl"])):
            close(nested(report, f"config.{name}", name), value, f"{identifier}: {name}")
        for name in ("integrator", "operator"):
            observed = nested(report, f"config.{name}", name,
                              "config.nonlinear_formulation" if name == "operator" else name)
            if observed != item[name]:
                raise ValueError(f"{identifier}: {name} requested {item[name]!r}, reported {observed!r}")
        actual_time = positive(nested(report, "stats.simulation_time", "simulation_time", "stats.time"),
                               f"{identifier}: simulation time")
        close(actual_time, endpoint, f"{identifier}: endpoint")
        seconds = positive(nested(report, "seconds", "elapsed_seconds", "stats.seconds"),
                           f"{identifier}: elapsed seconds")
        step_count = positive(nested(report, "stats.steps", "steps", "stats.iteration"),
                              f"{identifier}: steps")
        if not step_count.is_integer():
            raise ValueError(f"{identifier}: steps must be an integer")
        fps = positive(nested(report, "presentation.presented_frames_per_second",
                              "presentation.frames_per_second", "presentation.display_fps"),
                       f"{identifier}: presented frames per second")
        frames = positive(nested(report, "presentation.frames", "presentation.presented_frames"),
                          f"{identifier}: presented frames")
        p95 = positive(nested(report, "presentation.p95_frame_interval_ms"),
                       f"{identifier}: p95 frame interval")
        errors = grid.error(spectrum(spectrum_file, n, report), expected)
        passed = all(errors[key] <= limits[key] for key in METRICS)
        record = {**item, "seconds": seconds, "simulation_time": actual_time, "steps": int(step_count),
                  "simtime_per_s": actual_time / seconds,
                  "initial_turnovers_per_s": actual_time / tau / seconds,
                  "steps_per_s": step_count / seconds, "mean_dt": actual_time / step_count,
                  "display_fps": fps, "presented_frames": int(frames),
                  "p95_frame_interval_ms": p95, "error": errors, "accuracy_passed": passed,
                  "accuracy_status": "pass" if passed else "fail",
                  "result_sha256": sha256(result_file), "spectrum_sha256": sha256(spectrum_file),
                  "initial_sha256": initial_sha}
        checked.append(record)
        group_key = (item["label"], item["integrator"], item["operator"], float(item["cfl"]))
        groups.setdefault(group_key, []).append(record)

    if not groups:
        raise ValueError("The manifest contains no benchmark runs")
    summaries = []
    for (label, method, operator, cfl), records in groups.items():
        if len(records) != expected_runs or len({row["repeat"] for row in records}) != expected_runs:
            raise ValueError(f"{label}/{method}/{operator}/CFL{cfl:g}: expected {expected_runs} distinct runs")
        result = {"label": label, "integrator": method, "operator": operator, "cfl": cfl,
                  "runs": len(records), "accuracy_passed": all(row["accuracy_passed"] for row in records),
                  "accuracy_pass_count": sum(row["accuracy_passed"] for row in records),
                  "max_error": {key: max(row["error"][key] for row in records) for key in METRICS}}
        for name in ("simtime_per_s", "initial_turnovers_per_s", "steps_per_s", "mean_dt", "display_fps",
                     "seconds", "steps", "p95_frame_interval_ms"):
            result[f"median_{name}"] = statistics.median(row[name] for row in records)
        rates = [row["simtime_per_s"] for row in records]
        median = result["median_simtime_per_s"]
        result.update(min_simtime_per_s=min(rates), max_simtime_per_s=max(rates),
                      relative_mad=statistics.median(abs(rate-median) for rate in rates)/median)
        summaries.append(result)
    summaries.sort(key=lambda row: row["median_simtime_per_s"], reverse=True)
    result = {"fixture": fixture_name, "resolution": n, "padded_resolution": 3*n//2,
              "k0": fixture["k0"], "reynolds": fixture["reynolds"], "seed": fixture["seed"],
              "endpoint": endpoint, "initial_turnover_time": tau, "initial_turnovers": endpoint/tau,
              "initial_sha256": initial_sha, "expected_runs_per_group": expected_runs,
              "reference_spectrum": str(reference_path / "spectrum.c64"),
              "reference_spectrum_sha256": sha256(reference_path / "spectrum.c64"),
              "reference_gate": reference, "acceptance_limits": limits,
              "accuracy_rule": "Relative errors <= original Metal error + 2*reference uncertainty + floor",
              "reference_floor": floor, "groups": summaries,
              "timing_rule": "Per-case medians of live runs; rates use completed simulation time / measured wall time",
              "turnover_rule": "Initial turnover time comes from the shared serialized field, fixed across methods",
              "median_note": "Medians are calculated separately; median steps/s times median dt need not equal median simulated time/s"}
    (directory / "checked-runs.json").write_text(json.dumps(checked, indent=2, allow_nan=False) + "\n")
    (directory / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.directory.resolve())
    for row in result["groups"]:
        print(f"{row['label']} {row['integrator']} {row['operator']} CFL {row['cfl']:g}: "
              f"{row['median_simtime_per_s']:.6f} simtime/wall-s; "
              f"{row['median_display_fps']:.2f} display FPS; "
              f"accuracy {row['accuracy_pass_count']}/{row['runs']}")


if __name__ == "__main__":
    main()
