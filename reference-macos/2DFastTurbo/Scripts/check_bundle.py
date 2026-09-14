#!/usr/bin/env python3
"""Sequential post-release replay and real-window acceptance of a packaged app.

Run only after the full comparison and native factory release checks finish.
This performs four GPU jobs, never in parallel, and makes no timing claims.
All commands, process logs, results and comparisons are retained under --output.
The script itself uses only the Python standard library.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import plistlib
import subprocess
import time
from typing import Any, Callable

from audit_comparison import float32_match, options_from


METHODS = {"imexRK3", "ifHeunRK3", "etdrk4"}
OPERATORS = {"stress", "advective"}
COMMON_CONTROLS = ("window_visible_on_first_launch", "first_frame_presented",
                   "pause_stable", "reset_at_zero", "reopen_visible")
ACTIVE_CONTROLS = ("reset_while_running", "reconfigure_preserved_pause", "reconfigure_resume")


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def executable(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.suffix == ".app" and path.is_dir():
        with (path/"Contents/Info.plist").open("rb") as source:
            name = plistlib.load(source)["CFBundleExecutable"]
        path = path/"Contents/MacOS"/name
    require(path.is_file(), f"Executable is missing: {path}")
    return path


def finite(value: Any, *, positive: bool = False) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (not positive or value > 0))


def configuration(report: dict, selected: dict, n: int = 1024) -> None:
    config = report.get("config", {})
    expected = {"resolution": n, "padded_resolution": 3*n//2, "reynolds": 50000,
                "k0": 4, "seed": 2, "operator": selected["operator"],
                "integrator": selected["integrator"], "fixed_dt": None}
    require(all(config.get(key) == value for key, value in expected.items()),
            f"Configuration differs from the promoted factory defaults: {config}")
    require(float32_match(str(selected["cfl"]), config.get("cfl"))[0],
            f"CFL differs from selected {selected['cfl']}: {config.get('cfl')}")


def input_identity(report: dict, initial: Path | None) -> None:
    require("initial_file" in report and "initial_sha256" in report,
            "Result omits captured initial-field identity")
    if initial is None:
        require(report["initial_file"] is None and report["initial_sha256"] is None,
                "Native GUI run unexpectedly imported an initial field")
    else:
        require(report["initial_sha256"] == sha256(initial),
                "Reported initial hash differs from the actual imported samples")
        require(isinstance(report["initial_file"], str)
                and Path(report["initial_file"]).resolve() == initial.resolve(),
                "Reported initial path differs from the requested field")


def controls(report: dict, *, active: bool = False) -> None:
    checks = report.get("controls", {})
    required = COMMON_CONTROLS + (ACTIVE_CONTROLS if active else ())
    require(all(checks.get(key) is True for key in required),
            f"GUI lifecycle checks are incomplete or failed: {checks}")
    require(checks.get("single_step_delta") == 1, "Single Step did not advance exactly once")
    require(finite(checks.get("pause_ms")) and checks["pause_ms"] >= 0,
            "GUI pause latency was not recorded")
    require(report.get("passed") is True, "GUI diagnostic did not report success")


def identical_trajectory(report: dict, spectrum: Path, archived: dict,
                         archived_spectrum: Path) -> dict:
    stats, expected = report.get("stats", {}), archived.get("stats", {})
    require(stats.get("is_finite", stats.get("finite")) is True,
            "Replay did not report a finite state")
    for key in ("steps", "simulation_time"):
        require(finite(stats.get(key), positive=True) and stats[key] == expected.get(key),
                f"Trajectory {key} differs: {stats.get(key)} versus {expected.get(key)}")
    expected_bytes = 1024*1024*8
    require(spectrum.stat().st_size == archived_spectrum.stat().st_size == expected_bytes,
            "Spectrum size differs from the full N1024 complex Float32 layout")
    # Comparing streamed bytes gives an exact result without a large allocation.
    with spectrum.open("rb") as actual, archived_spectrum.open("rb") as reference:
        while block := actual.read(1024*1024):
            require(block == reference.read(len(block)), "Final spectra are not byte-identical")
    return {"byte_identical_spectrum": True, "spectrum_sha256": sha256(spectrum),
            "steps": stats["steps"], "simulation_time": stats["simulation_time"],
            "reference_spectrum": str(archived_spectrum)}


def run(command: list[str], directory: Path, timeout: float) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    save(directory/"command.json", command)
    for name in ("result.json", "spectrum.c64", "native.png"):
        (directory/name).unlink(missing_ok=True)
    def decoded(value: Any) -> str:
        return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        (directory/"stdout.txt").write_text(decoded(error.stdout))
        (directory/"stderr.txt").write_text(decoded(error.stderr)+f"\nTimeout after {timeout} seconds.\n")
        raise RuntimeError(f"Process timed out after {timeout} seconds") from error
    except OSError as error:
        (directory/"stdout.txt").write_text("")
        (directory/"stderr.txt").write_text(str(error)+"\n")
        raise
    (directory/"stdout.txt").write_text(completed.stdout)
    (directory/"stderr.txt").write_text(completed.stderr)
    require(completed.returncode == 0,
            f"Process exited {completed.returncode}: {completed.stderr[-1500:]}")
    result = read(directory/"result.json")
    require(isinstance(result, dict), "Process result is not a JSON object")
    return result


def validate_inputs(args: argparse.Namespace) -> tuple[dict, dict, Path, list[str], Path]:
    summary, factory = read(args.summary), read(args.factory)
    selected = summary.get("selected_candidate")
    require(isinstance(selected, dict) and selected.get("operator") in OPERATORS
            and selected.get("integrator") in METHODS and finite(selected.get("cfl"), positive=True),
            "Summary has no valid selected configuration")
    for flag in ("full_matrix", "selection_has_complete_timing", "all_references_converged",
                 "reference_calibration_passed", "original_metal_accuracy_comparison"):
        require(summary.get(flag) is True, f"Full comparison prerequisite failed: {flag}")
    require(summary.get("selection_mode") == "live"
            and selected in summary.get("validated_candidates", []),
            "Selection is not a validated live GUI result")
    require(factory.get("status") == "complete" and factory.get("all_required_checks_passed") is True
            and factory.get("accuracy_requested") is True,
            "Factory release checks, including native accuracy, have not all passed")
    require(factory.get("selected_candidate") == selected
            and factory.get("selection_summary_sha256") == sha256(args.summary),
            "Factory release was not validated against this exact selection summary")
    tests = factory.get("tests", [])
    require(len(tests) == factory.get("expected_check_count") == 8
            and all(test.get("passed") is True for test in tests)
            and any(test.get("name") == "factory-n1024-2turnovers-accuracy" for test in tests),
            "Factory release lacks all eight successful acceptance checks")
    matrix = args.summary.parent
    rows = read(matrix/"runs.json")
    matches = [row for row in rows if row.get("phase") == "timing" and row.get("label") == "fast-turbo"
               and row.get("repeat") == 0 and all(row.get(key) == value for key, value in selected.items())]
    require(bool(matches) and matches[0].get("accuracy_passed") is True,
            "First selected repeat-zero headless timing is missing or failed")
    first = matches[0]
    archive = matrix/first["fixture"]/"timing"/(
        f"0-fast-turbo-{selected['operator']}-{selected['integrator']}-cfl{selected['cfl']:g}")
    command = read(archive/"command.json")
    options = options_from(command)
    require(options.get("--resolution") == "1024" and options.get("--reynolds") == "50000",
            "First matrix fixture does not match the N1024/Re50000 factory configuration")
    initial = Path(options["--initial"]).resolve()
    require(initial.is_file(), f"Archived PCG64 initial field is missing: {initial}")
    return selected, factory, archive, command, initial


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--factory", type=Path, required=True, help="Passed native factory release.json")
    parser.add_argument("--bench", type=Path, required=True, help="Final standalone benchmark executable")
    parser.add_argument("--app", type=Path, required=True, help="Final packaged .app or its executable")
    parser.add_argument("--output", type=Path, required=True, help="Directory for bundle.json and four checks")
    parser.add_argument("--timeout", type=float, default=900, help="Per process, in seconds")
    args = parser.parse_args()
    if not finite(args.timeout, positive=True):
        parser.error("Timeout must be finite and positive")
    args.summary, args.factory, args.output = (p.expanduser().resolve()
                                             for p in (args.summary, args.factory, args.output))
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output/"bundle.json"
    report: dict[str, Any] = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
                              "status": "checking-prerequisites", "expected_check_count": 4,
                              "all_required_checks_passed": False, "tests": [],
                              "per_process_timeout_seconds": args.timeout,
                              "scope": "Post-release exact trajectory, promoted defaults, input identity and GUI lifecycle checks; not throughput timing."}
    try:
        selected, factory, archive, archived_command, initial = validate_inputs(args)
        bench, app = executable(args.bench), executable(args.app)
        report.update(selected_candidate=selected, selection_summary=str(args.summary),
                      selection_summary_sha256=sha256(args.summary), factory_release=str(args.factory),
                      factory_release_sha256=sha256(args.factory), archived_timing=str(archive),
                      executables={"bench": {"path": str(bench), "sha256": sha256(bench)},
                                   "app": {"path": str(app), "sha256": sha256(app)}}, status="running")
        bundle = args.app.expanduser().resolve()
        if bundle.is_dir() and bundle.suffix == ".app":
            report["packaged_metallibs"] = {str(p.relative_to(bundle)): sha256(p)
                                           for p in sorted(bundle.rglob("*.metallib"))}
        archived = read(archive/"result.json")
        original_options = options_from(archived_command)
        archived_spectrum = Path(original_options["--spectrum"]).resolve()
        native_directory = args.factory.parent/"accuracy/selected-native"
        native = read(native_directory/"result.json")
        native_options = options_from(read(native_directory/"command.json"))
        native_spectrum = Path(native_options["--spectrum"]).resolve()
        native_endpoint = read(args.factory.parent/"accuracy/accuracy.json")["endpoint"]
        require(finite(native_endpoint, positive=True), "Factory native endpoint is invalid")
    except Exception as error:
        report.update(status="prerequisite-failed", error=str(error))
        save(destination, report)
        print(f"FAIL prerequisites: {error}", flush=True)
        return 2
    save(destination, report)

    def record(name: str, body: Callable[[Path], dict]) -> None:
        started = time.monotonic()
        directory = args.output/name
        directory.mkdir(parents=True, exist_ok=True)
        try:
            item = {"name": name, "passed": True, "details": body(directory)}
        except Exception as error:
            item = {"name": name, "passed": False, "error": str(error)}
        item.update(wall_seconds=time.monotonic()-started, artifact_directory=str(directory))
        report["tests"].append(item)
        report["all_required_checks_passed"] = (len(report["tests"]) == 4
                                                and all(test["passed"] for test in report["tests"]))
        save(directory/"check.json", item)
        save(destination, report)
        print(f"{'PASS' if item['passed'] else 'FAIL'} {name}", flush=True)

    def headless(directory: Path) -> dict:
        command = list(archived_command)
        command[0] = str(bench)
        for option, value in (("--output", directory/"result.json"), ("--spectrum", directory/"spectrum.c64")):
            command[command.index(option)+1] = str(value)
        actual = run(command, directory, args.timeout)
        configuration(actual, selected)
        input_identity(actual, initial)
        return identical_trajectory(actual, directory/"spectrum.c64", archived, archived_spectrum)

    def gui(directory: Path, *, imported: bool) -> dict:
        endpoint = original_options["--until"] if imported else str(native_endpoint)
        command = [str(app), "--gui-benchmark", "--until", endpoint,
                   "--output", str(directory/"result.json"), "--spectrum", str(directory/"spectrum.c64")]
        if imported:
            command += ["--initial", str(initial)]
        else:
            command += ["--export", str(directory/"native.png")]
        actual = run(command, directory, args.timeout)
        configuration(actual, selected)
        input_identity(actual, initial if imported else None)
        controls(actual)
        require(actual.get("mode") == "gui-benchmark" and actual.get("batch_policy") == "adaptive-8ms-1to32",
                "GUI did not use its normal adaptive benchmark path")
        presentation = actual.get("presentation", {})
        require(finite(presentation.get("presented_frames_per_second"), positive=True),
                "No positive GUI presentation cadence was recorded")
        if imported:
            replay = args.output/"headless-replay"
            reference, reference_spectrum = read(replay/"result.json"), replay/"spectrum.c64"
        else:
            reference, reference_spectrum = native, native_spectrum
            png = directory/"native.png"
            with png.open("rb") as image:
                require(image.read(8) == b"\x89PNG\r\n\x1a\n", "Native GUI export is not a PNG")
        details = identical_trajectory(actual, directory/"spectrum.c64", reference, reference_spectrum)
        return {**details, "unmodified_numerical_defaults_verified": True,
                "initial_sha256": actual["initial_sha256"], "controls": actual["controls"],
                "presentation": presentation}

    def live(directory: Path) -> dict:
        command = [str(app), "--live-check", "--resolution", "128", "--gui-warmup-seconds", "0.2",
                   "--output", str(directory/"result.json")]
        actual = run(command, directory, args.timeout)
        require(actual.get("mode") == "live-check", "GUI did not execute the active lifecycle path")
        configuration(actual, selected, n=128)
        input_identity(actual, None)
        controls(actual, active=True)
        return {"configuration": actual["config"], "controls": actual["controls"]}

    record("headless-replay", headless)
    record("native-gui", lambda directory: gui(directory, imported=False))
    record("imported-gui", lambda directory: gui(directory, imported=True))
    record("active-lifecycle", live)
    report.update(status="complete", completed_utc=datetime.now(timezone.utc).isoformat())
    save(destination, report)
    return 0 if report["all_required_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
