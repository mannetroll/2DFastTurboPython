#!/usr/bin/env python3
"""Render completed comparison JSON into a concise, auditable Markdown report.

Uses only the Python standard library. It never launches a solver, reads large
spectral arrays, changes benchmark data, or substitutes a winner when selection
failed. Run after compare.py has finished writing summary.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any
from urllib.parse import quote


METRICS = ("vorticity", "energy", "enstrophy")
METHOD_NAMES = {"imexRK3": "IMEX RK3", "ifHeunRK3": "IF Heun RK3", "etdrk4": "ETD RK4"}


def read_json(path: Path, default: Any = None) -> Any:
    if path.exists():
        return json.loads(path.read_text())
    if default is not None:
        return default
    raise FileNotFoundError(f"Required completed benchmark artifact is missing: {path}")


def finite(value: Any) -> bool:
    return isinstance(value, (float, int)) and math.isfinite(value)


def fmt(value: Any, digits: int = 4) -> str:
    return f"{value:.{digits}g}" if finite(value) else "—"


def escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def link(label: str, path: Path, output: Path) -> str:
    relative = os.path.relpath(path, output.parent)
    return f"[{label}]({quote(Path(relative).as_posix(), safe='/.-_')})"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(map(escape, headers)) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(map(escape, row)) + " |" for row in rows)
    return "\n".join(lines)


def candidate_key(value: dict) -> tuple[str, str, float]:
    return value.get("operator", "stress"), value.get("integrator", ""), value.get("cfl", 0)


def matching_group(groups: list[dict], label: str, fixture: str,
                   selection: dict | None) -> dict | None:
    matches = [group for group in groups if group.get("label") == label
               and group.get("fixture") == fixture]
    if label in ("fast-turbo", "fast-turbo-live"):
        if selection is None:
            return None
        matches = [group for group in matches if candidate_key(group) == candidate_key(selection)]
    return matches[0] if len(matches) == 1 else None


def rate_cell(group: dict | None, expected_runs: int) -> str:
    if group is None:
        return "—"
    result = fmt(group.get("median_simtime_per_s"))
    if group.get("runs", 0) != expected_runs:
        result += f" ({group.get('runs', 0)} runs)"
    return result


def step_statistics(rows: list[dict], label: str, fixture: str,
                    selection: dict | None) -> tuple[float | None, float | None]:
    throughputs, timesteps = [], []
    for row in rows:
        if (row.get("phase") != "timing" or row.get("status") == "error"
                or row.get("label") != label or row.get("fixture") != fixture):
            continue
        if label in ("fast-turbo", "fast-turbo-live") and (
                selection is None or candidate_key(row) != candidate_key(selection)):
            continue
        steps, seconds, physical_time = row.get("steps"), row.get("seconds"), row.get("simulation_time")
        if (not finite(steps) or not finite(seconds) or not finite(physical_time)
                or steps <= 0 or seconds <= 0 or physical_time < 0):
            continue
        throughputs.append(steps/seconds)
        timesteps.append(physical_time/steps)
    return ((statistics.median(throughputs), statistics.median(timesteps))
            if throughputs else (None, None))


def timing_accuracy(rows: list[dict], label: str, fixture: str | None = None,
                    selection: dict | None = None) -> tuple[int, int, int]:
    """Count observed passes/failures/unknowns without treating missing runs as passes."""
    matches = [row for row in rows if row.get("phase") == "timing"
               and row.get("label") == label and row.get("status") != "error"
               and (fixture is None or row.get("fixture") == fixture)]
    if label in ("fast-turbo", "fast-turbo-live"):
        matches = [row for row in matches if selection is not None
                   and candidate_key(row) == candidate_key(selection)]
    passed = sum(row.get("accuracy_passed") is True for row in matches)
    failed = sum(row.get("accuracy_passed") is False for row in matches)
    return passed, failed, len(matches)-passed-failed


def transform_description(selection: dict | None) -> str:
    if selection is None or selection.get("integrator") not in METHOD_NAMES:
        return ""
    operator_name = selection.get("operator", "stress")
    if operator_name not in ("stress", "advective"):
        return ""
    stages = 4 if selection["integrator"] == "etdrk4" else 3
    per_stage = 4 if operator_name == "stress" else 5
    composition = "two inverse plus two forward" if operator_name == "stress" else "four inverse plus one forward"
    return (f"The selected solver performs {stages} nonlinear stages, each with {composition} padded 2D "
            f"transforms: **{stages*per_stage} transforms per accepted step**. Metal's IMEX RK3 stress formulation "
            "uses 12; Astra's IF Heun RK3 advective formulation uses 15. These counts exclude initialization, "
            "display/diagnostic transforms, and any inactive work encoded after a clipped final step.")


def comparison_statistics(pairs: list[tuple[dict, dict]]) -> tuple[float | None, int]:
    ratios: list[float] = []
    passed = 0
    for candidate, baseline in pairs:
        a, b = candidate.get("median_simtime_per_s"), baseline.get("median_simtime_per_s")
        if not finite(a) or not finite(b) or a <= 0 or b <= 0:
            continue
        ratio = a/b
        ratios.append(ratio)
        a_mad, b_mad = candidate.get("relative_mad"), baseline.get("relative_mad")
        if finite(a_mad) and finite(b_mad):
            passed += ratio-1 > max(0.02, 3*math.hypot(a_mad, b_mad))
    return (statistics.geometric_mean(ratios) if ratios else None), passed


def range_text(values: list[float], unit: str = "") -> str:
    clean = [value for value in values if finite(value)]
    if not clean:
        return "not recorded"
    return f"{fmt(min(clean))}–{fmt(max(clean))}{unit}"


def report(directory: Path, output: Path) -> str:
    summary = read_json(directory / "summary.json")
    rows = read_json(directory / "runs.json")
    metadata = read_json(directory / "metadata.json", {})
    references = read_json(directory / "references.json", [])
    audit = read_json(directory / "audit.json", {})
    configuration = metadata.get("configuration", {})
    groups = summary.get("groups", [])
    selection = summary.get("selected_candidate")
    expected_runs = configuration.get("runs", 5)
    floor = configuration.get("reference_floor", 2e-6)
    fixture_names = sorted({row["fixture"] for row in rows if "fixture" in row})
    fixtures = {name: read_json(directory / name / "fixture.json", {"name": name})
                for name in fixture_names}

    def fixture_label(name: str) -> str:
        fixture = fixtures.get(name, {})
        if "resolution" not in fixture:
            return name
        return (f"N{fixture['resolution']}, k₀={fmt(fixture.get('k0'))}, "
                f"Re={fmt(fixture.get('reynolds'), 6)}, seed={fixture.get('seed', '—')}")

    paragraphs = ["# 2DFastTurbo comparison"]
    if selection:
        name = METHOD_NAMES.get(selection["integrator"], selection["integrator"])
        status = "Selected configuration" if summary.get("selection_has_complete_timing") else "Provisional screening result"
        paragraphs.append(f"**{status}: {name}, {selection.get('operator', 'stress')} operator, "
                          f"CFL {fmt(selection['cfl'])}.** {summary.get('selection_basis', '')}.")
    else:
        paragraphs.append("**No default was selected.** The recorded reference, accuracy, or complete-timing "
                          "requirements were not all satisfied. Results below remain diagnostic.")
    started = metadata.get("started_utc", "not recorded")
    hardware = metadata.get("hardware", {})
    if isinstance(hardware, str):
        try:
            hardware = json.loads(hardware)
        except ValueError:
            hardware = {}
    displays = hardware.get("SPDisplaysDataType", []) if isinstance(hardware, dict) else []
    gpu = displays[0].get("sppci_model") if displays else None
    machine = f"GPU: {gpu}. " if gpu else ""
    paragraphs.append(f"{machine}Run started {started}. All throughput values below are completed physical simulation "
                      f"time per wall-clock second; higher is faster. Medians use {expected_runs} interleaved "
                      "runs per fixture unless a cell states otherwise.")

    rates = []
    for fixture in fixture_names:
        rates.append([fixture_label(fixture)] + [rate_cell(matching_group(groups, label, fixture, selection), expected_runs)
                      for label in ("fast-turbo-live", "fast-turbo", "metal-reference", "mlx-reference")])
    if rates:
        paragraphs.append(table(["Fixture", "2DFastTurbo live", "2DFastTurbo headless",
                                 "Metal baseline headless", "Astra baseline headless"], rates))
    paragraphs.append("The live application targets 60 Hz with adaptive batching. Metal and Astra are captured "
                      "headless predecessor adapters: Astra retains its per-batch field snapshots and diagnostics. "
                      "2DFastTurbo and Metal use VkFFT; Astra uses MLX FFT. Integrators, timestep controllers, "
                      "nonlinear formulations, and presentation costs also differ, so these measurements do not "
                      "isolate an FFT-library speedup.")
    baseline_accuracy = []
    for label, title in (("metal-reference", "Metal"), ("mlx-reference", "Astra")):
        passed, failed, unknown = timing_accuracy(rows, label)
        total = passed+failed+unknown
        if not total:
            continue
        baseline_accuracy.append(f"{title}: {passed}/{total} completed timed endpoints passed"
                                 + (f", {failed} failed" if failed else "")
                                 + (f", {unknown} unverified" if unknown else ""))
    if baseline_accuracy:
        paragraphs.append("Predecessor accuracy against the same Metal-based error budget: "
                          + "; ".join(baseline_accuracy) + ".")
    astra_passed, astra_failed, astra_unknown = timing_accuracy(rows, "mlx-reference")
    if astra_failed or astra_unknown:
        paragraphs.append("**Astra throughput is a comparison at its original settings, not an "
                          "accuracy-matched speedup:** "
                          + ("at least one measured Astra endpoint exceeds the accepted error budget."
                             if astra_failed else "its measured accuracy coverage is incomplete.")
                          + " Its rates remain visible to explain the predecessor's observed behavior.")

    step_rows = []
    for fixture in fixture_names:
        values = [step_statistics(rows, label, fixture, selection)
                  for label in ("fast-turbo", "metal-reference", "mlx-reference")]
        if any(value[0] is not None for value in values):
            step_rows.append([fixture_label(fixture)] + [fmt(value) for pair in values for value in pair])
    if step_rows:
        paragraphs.append("Headless step throughput and actual mean physical timestep separate how quickly "
                          "steps execute from how much turbulence time each step advances. Mean Δt is computed "
                          "per run as completed simulation time divided by completed steps; the table gives "
                          "medians of those per-run values.")
        paragraphs.append(table(["Fixture", "2DFastTurbo steps/s", "Mean Δt", "Metal steps/s", "Mean Δt",
                                 "Astra steps/s", "Mean Δt"], step_rows))
        paragraphs.append("Within each run, physical throughput equals steps/s × mean Δt; separately computed "
                          "medians need not multiply exactly. Astra retains its 0.45 Courant rule on padded "
                          "spacing and its 0.0125 timestep cap; the Metal-based solvers use a different "
                          "retained-grid Courant definition. Astra's recorded requested_cfl is the common "
                          "CLI argument; its actual controller remains 0.45. Whole-step throughput still includes nonlinear "
                          "kernels, synchronization, and snapshots, so its ratios are not isolated FFT speedups.")
    transforms = transform_description(selection)
    if transforms:
        paragraphs.append(transforms)

    ratios = []
    for candidate_label, title in (("fast-turbo-live", "Live"), ("fast-turbo", "Headless")):
        for baseline_label, baseline_title in (("metal-reference", "Metal"), ("mlx-reference", "Astra")):
            pairs = []
            accuracy_matched = 0
            for fixture in fixture_names:
                candidate = matching_group(groups, candidate_label, fixture, selection)
                baseline = matching_group(groups, baseline_label, fixture, selection)
                if candidate and baseline and candidate.get("runs", 0) >= expected_runs and baseline.get("runs", 0) >= expected_runs:
                    pairs.append((candidate, baseline))
                    counts = [timing_accuracy(rows, label, fixture, selection)
                              for label in (candidate_label, baseline_label)]
                    accuracy_matched += all(passed >= expected_runs and failed == unknown == 0
                                            for passed, failed, unknown in counts)
            ratio, gate_count = comparison_statistics(pairs)
            if pairs:
                ratios.append([f"{title} / {baseline_title}", f"{fmt(ratio)}×",
                               f"{len(pairs)}/{len(fixture_names)}", f"{accuracy_matched}/{len(pairs)}",
                               f"{gate_count}/{len(pairs)}"])
    if ratios:
        paragraphs.append(table(["Comparison", "Geometric mean throughput ratio", "Complete same-input fixtures",
                                 "Both solvers pass accuracy", "Gain passes noise gate"], ratios))
        paragraphs.append("The per-fixture gain gate requires improvement above both 2% and three combined "
                          "relative median absolute deviations, using the recorded timing rule. Ratios involving "
                          "the live app include its presentation cost; the predecessor adapters do not present frames. "
                          "The accuracy column counts fixtures where every timed run of both solvers passes the "
                          "same error budget; other ratios describe throughput only.")

    reference_lookup = {(item.get("fixture"), item.get("endpoint")): item for item in references}
    selected_rows = [row for row in rows if selection and row.get("label") in ("fast-turbo", "fast-turbo-live")
                     and candidate_key(row) == candidate_key(selection) and isinstance(row.get("error"), dict)]
    accuracy_rows = []
    for metric in METRICS:
        observations = []
        for row in selected_rows:
            reference = reference_lookup.get((row.get("fixture"), row.get("endpoint")))
            if not reference or not finite(row["error"].get(metric)):
                continue
            uncertainty = reference.get("uncertainty_estimate", reference.get("uncertainty", {})).get(metric)
            baseline_error = reference.get("baseline_error", {}).get(metric)
            if not finite(uncertainty) or not finite(baseline_error):
                continue
            limit = baseline_error + 2*uncertainty + floor
            observations.append((row["error"][metric], limit, row))
        if observations:
            error, limit, worst = max(observations, key=lambda value: value[0])
            fraction = max(value[0]/value[1] for value in observations if value[1] > 0)
            case = f"{fixture_label(worst['fixture'])}; T={fmt(worst['endpoint'])}"
            accuracy_rows.append([metric.capitalize(), fmt(error), fmt(limit), f"{100*fraction:.3g}%", case])
    if accuracy_rows:
        paragraphs.append("Selected-configuration errors include every available screening checkpoint and timed "
                          "headless/GUI endpoint. All errors are relative; each allowed limit is the original "
                          f"Metal error plus twice the recorded reference estimate plus {fmt(floor)}.")
        paragraphs.append(table(["Metric", "Maximum error", "Limit at that case", "Maximum budget used", "Maximum-error case"], accuracy_rows))
    maximum_uncertainty = {}
    for metric in METRICS:
        values = [item.get("uncertainty_estimate", item.get("uncertainty", {})).get(metric) for item in references]
        values = [value for value in values if finite(value)]
        maximum_uncertainty[metric] = max(values) if values else None
    converged = sum(item.get("converged") is True for item in references)
    calibration = summary.get("reference_calibration_passed")
    paragraphs.append(f"Reference gates passed {converged}/{len(references)} checkpoints; long analytic "
                      f"calibration {'passed' if calibration is True else 'failed' if calibration is False else 'was not recorded'}. "
                      "Maximum reference estimates: " + ", ".join(
                          f"{metric} {fmt(maximum_uncertainty[metric])}" for metric in METRICS)
                      + ". These are empirical estimates from refinement and cross-method checks, not rigorous error bounds.")

    live_rows = [row for row in selected_rows if row.get("phase") == "timing" and row.get("label") == "fast-turbo-live"]
    if live_rows:
        fps = [row.get("presented_frames_per_second") for row in live_rows]
        p95 = [row.get("p95_frame_interval_ms") for row in live_rows]
        longest = [row.get("longest_frame_interval_ms") for row in live_rows]
        paragraphs.append("Across selected live runs, presentation rate ranged " + range_text(fps, " fps")
                          + "; per-run p95 frame intervals ranged " + range_text(p95, " ms")
                          + "; longest observed frame intervals ranged " + range_text(longest, " ms")
                          + ". These statistics measure presentation cadence; simulation progress is reported separately.")
        cadence_rows = []
        for fixture in fixture_names:
            values = [row for row in live_rows if row.get("fixture") == fixture]
            fixture_fps = [row.get("presented_frames_per_second") for row in values
                           if finite(row.get("presented_frames_per_second"))]
            fixture_p95 = [row.get("p95_frame_interval_ms") for row in values
                           if finite(row.get("p95_frame_interval_ms"))]
            if fixture_fps or fixture_p95:
                cadence_rows.append([fixture_label(fixture),
                                     fmt(statistics.median(fixture_fps)) if fixture_fps else "—",
                                     range_text(fixture_fps),
                                     fmt(statistics.median(fixture_p95)) if fixture_p95 else "—",
                                     range_text(fixture_p95)])
        if cadence_rows:
            paragraphs.append(table(["Selected live fixture", "Median fps", "Run fps range",
                                     "Median p95 interval (ms)", "Run p95 range (ms)"], cadence_rows))

    limitations = list(dict.fromkeys(metadata.get("limitations", []) + summary.get("limitations", [])))
    if not summary.get("full_matrix"):
        limitations.append("This is a partial matrix; it does not establish the default across the full planned workload.")
    if not summary.get("selection_has_complete_timing"):
        limitations.append("Complete finalist timings are unavailable; screening throughput cannot establish a live-app winner.")
    if converged < len(references) or calibration is False:
        limitations.append("At least one reference requirement failed; a new numerical default is not validated by this run.")
    failures = summary.get("failed_timing_jobs", 0)
    if failures:
        limitations.append(f"{failures} timing jobs failed. Their rows and error logs remain in the raw artifacts.")
    if any(row.get("accuracy_passed") is False for row in selected_rows):
        limitations.append("The selected configuration has a failed accuracy row; review the selection before accepting it.")
    if audit:
        paragraphs.append(f"Configuration audit: {audit.get('passed', 0)}/{audit.get('completed_results', 0)} "
                          f"completed reports passed; {len(audit.get('commands_without_result', []))} commands lack results. "
                          f"Input SHA256 hashes were verified in {audit.get('initial_hashes_checked', 0)} reports.")
        if audit.get("all_passed") is not True:
            limitations.append("The configuration audit did not pass; review its mismatches before accepting the selection.")
        if audit.get("initial_hash_not_reported", 0):
            limitations.append(f"{audit['initial_hash_not_reported']} reports omit the loaded-field SHA256. "
                               "Their input commands are retained, but actual loaded-field identity is not certified "
                               "by those reports.")
    if summary.get("pruning_enabled"):
        paragraphs.append(f"Pruning skipped {summary.get('pruned_screen_trajectories', 0)} later candidate trajectories "
                          "after documented accuracy/execution failures. Reference and baseline checkpoints were retained.")
    if limitations:
        paragraphs.append("Limitations:\n\n" + "\n".join(f"- {item}" for item in dict.fromkeys(limitations)))

    evidence = [("summary", "summary.json"), ("raw runs", "runs.json"), ("CSV", "runs.csv"),
                ("reference gates", "references.json"), ("calibration", "reference-calibration.json"),
                ("configuration audit", "audit.json"),
                ("hardware, hashes, and options", "metadata.json")]
    paragraphs.append("Evidence: " + ", ".join(link(label, directory / name, output)
                                               for label, name in evidence if (directory / name).exists()) + ". "
                      "Each fixture folder retains its input/hash, commands, solver JSON, final spectra, and timing logs.")
    return "\n\n".join(paragraphs) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Completed compare.py output directory")
    parser.add_argument("--output", type=Path, help="Defaults to DIRECTORY/report.md")
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()
    output = args.output.expanduser().resolve() if args.output else directory / "report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report(directory, output))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
