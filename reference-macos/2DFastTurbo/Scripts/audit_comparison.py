#!/usr/bin/env python3
"""Audit completed benchmark configuration against its recorded CLI request.

This reads JSON and hashes shared initial fields once per file version. It
does not run solvers, inspect large spectral outputs, or change benchmark data.
Missing initial hashes are reported as coverage gaps, not invented matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Any


SWITCHES = {"--benchmark", "--gui-benchmark", "--live-check", "--help"}


def options_from(command: list[str]) -> dict[str, str]:
    if not isinstance(command, list) or not command or not all(isinstance(value, str) for value in command):
        raise ValueError("command.json must contain a nonempty argument-string array")
    result: dict[str, str] = {}
    index = 1
    while index < len(command):
        key = command[index]
        if key in SWITCHES:
            index += 1
            continue
        if not key.startswith("--") or index + 1 >= len(command):
            raise ValueError(f"Unpaired command option: {key!r}")
        if key in result:
            raise ValueError(f"Repeated command option: {key}")
        result[key] = command[index + 1]
        index += 2
    return result


def lookup(report: dict, key: str, fallback: str | None = None) -> Any:
    configuration = report.get("config", {})
    if isinstance(configuration, dict) and key in configuration:
        return configuration[key]
    if key in report:
        return report[key]
    if fallback:
        return lookup(report, fallback)
    return None


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def float32_match(requested: str, actual: Any) -> tuple[bool, float | None, float | None]:
    expected, observed = numeric(requested), numeric(actual)
    if expected is None or observed is None:
        return False, expected, None
    try:
        rounded = struct.unpack("<f", struct.pack("<f", expected))[0]
    except (OverflowError, struct.error):
        return False, expected, None
    if not math.isfinite(rounded):
        return False, rounded, None
    bits = struct.unpack("<I", struct.pack("<f", abs(rounded)))[0]
    if bits < 0x7F7FFFFF:
        ulp = struct.unpack("<f", struct.pack("<I", bits + 1))[0] - abs(rounded)
    else:
        ulp = abs(rounded) - struct.unpack("<f", struct.pack("<I", bits - 1))[0]
    tolerance = 2*ulp
    return abs(observed-rounded) <= tolerance, rounded, tolerance


class Auditor:
    def __init__(self):
        self.hashes: dict[tuple[str, int, int], str] = {}

    def hash_file(self, path: Path) -> str:
        stat = path.stat()
        key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        if key not in self.hashes:
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024*1024), b""):
                    digest.update(block)
            self.hashes[key] = digest.hexdigest()
        return self.hashes[key]

    def audit_run(self, result_file: Path, root: Path) -> dict[str, Any]:
        record: dict[str, Any] = {"path": str(result_file.relative_to(root)), "passed": False,
                                  "checks": [], "mismatches": [], "initial_hash_checked": False}
        command_file = result_file.with_name("command.json")
        try:
            report = json.loads(result_file.read_text())
            if not isinstance(report, dict):
                raise ValueError("result.json must contain a JSON object")
            command = json.loads(command_file.read_text())
            options = options_from(command)
            record["executable"] = command[0]
        except (OSError, ValueError, TypeError) as error:
            record["mismatches"].append({"field": "artifacts", "reason": str(error)})
            return record

        def check(field: str, requested: Any, actual: Any, passed: bool, **details) -> None:
            item = {"field": field, "requested": requested, "reported": actual, "passed": passed, **details}
            record["checks"].append(item)
            if not passed:
                record["mismatches"].append(item)

        if "--resolution" in options:
            try:
                n = int(options["--resolution"])
            except ValueError:
                n = None
            actual_n = numeric(lookup(report, "resolution", "grid"))
            actual_m = numeric(lookup(report, "padded_resolution", "padded_grid"))
            check("resolution", options["--resolution"], actual_n, n is not None and actual_n == n)
            expected_m = 3*n//2 if n is not None else None
            check("padded_resolution", expected_m, actual_m,
                  expected_m is not None and actual_m == expected_m)

        for option, field in (("--integrator", "integrator"), ("--operator", "operator")):
            if option in options:
                actual = lookup(report, field, "nonlinear_formulation" if field == "operator" else None)
                check(field, options[option], actual, actual == options[option])

        if "--reynolds" in options:
            expected = numeric(options["--reynolds"])
            actual = numeric(lookup(report, "reynolds"))
            passed = expected is not None and actual is not None and math.isclose(expected, actual, rel_tol=1e-12, abs_tol=0)
            check("reynolds", options["--reynolds"], actual, passed)

        for option, field in (("--cfl", "cfl"), ("--fixed-dt", "fixed_dt")):
            if option not in options:
                continue
            # The Astra adapter deliberately keeps its original 0.45 padded CFL
            # and records the common-interface argument separately.
            actual_field = ("requested_cfl" if field == "cfl" and lookup(report, "requested_cfl") is not None
                            else field)
            actual = lookup(report, actual_field)
            passed, rounded, tolerance = float32_match(options[option], actual)
            check(field, options[option], actual, passed, reported_field=actual_field,
                  float32_requested=rounded, absolute_tolerance=tolerance)

        if "--initial" in options:
            reported_hash = report.get("initial_sha256")
            if reported_hash is None:
                record["initial_hash_coverage"] = "not reported; no hash match claimed"
            else:
                initial = Path(options["--initial"]).expanduser()
                if not initial.is_absolute():
                    # Compare.py records absolute paths. Relative standalone
                    # fixtures are resolvable only when colocated with the run.
                    initial = command_file.parent / initial
                try:
                    actual_hash = self.hash_file(initial)
                    record["initial_hash_checked"] = True
                    check("initial_sha256", actual_hash, reported_hash,
                          isinstance(reported_hash, str) and reported_hash.lower() == actual_hash,
                          initial_path=str(initial.resolve()))
                except OSError as error:
                    check("initial_sha256", options["--initial"], reported_hash, False, reason=str(error))
        record["passed"] = not record["mismatches"]
        return record


def audit(root: Path) -> dict[str, Any]:
    auditor = Auditor()
    result_files = sorted(root.rglob("result.json"))
    records = [auditor.audit_run(path, root) for path in result_files]
    incomplete = [str(path.relative_to(root)) for path in sorted(root.rglob("command.json"))
                  if not path.with_name("result.json").exists()]
    passed = sum(record["passed"] for record in records)
    failed = len(records)-passed
    return {"schema_version": 1, "directory": str(root), "all_passed": bool(records) and failed == 0,
            "completed_results": len(records), "passed": passed, "failed": failed,
            "failed_paths": [record["path"] for record in records if not record["passed"]],
            "initial_hashes_checked": sum(record["initial_hash_checked"] for record in records),
            "initial_hash_not_reported": sum("initial_hash_coverage" in record for record in records),
            "unique_initial_files_hashed": len(auditor.hashes),
            "commands_without_result": incomplete,
            "scope": "Recorded configuration/input consistency only; numerical accuracy and timing gates are audited separately.",
            "float32_rule": "CFL and fixed_dt must match the requested value rounded to Float32 within 2 Float32 ULPs.",
            "runs": records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Completed comparison output directory")
    parser.add_argument("--output", type=Path, help="Defaults to DIRECTORY/audit.json")
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Directory does not exist: {root}")
    output = args.output.expanduser().resolve() if args.output else root / "audit.json"
    result = audit(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({key: result[key] for key in ("completed_results", "passed", "failed", "failed_paths")}, indent=2))
    print(output)
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
