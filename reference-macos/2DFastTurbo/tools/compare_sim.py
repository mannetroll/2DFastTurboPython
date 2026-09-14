#!/usr/bin/env python3
"""Compare Python/Swift sim logs, including parameters and every checkpoint."""

import argparse
from collections import defaultdict
from decimal import Decimal
import math
from pathlib import Path
import re
import sys

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
VALUES = re.compile(r"\b(T|DT|CN|CFLM|TAU_L)\s*=\s*(" + NUMBER + r")")
FIELDS = ("T", "DT", "CN", "CFLM", "TAU_L")


def read_log(path):
    """Retain decimal strings so tolerance accounts for printed precision."""
    config, rows = {}, {}
    initial = final = elapsed = fps = None
    for line in path.read_text().splitlines():
        setting = re.fullmatch(r"\s*(N|Re|K0|Steps|CFL|Update|Start spectrum|Method|PAO seed in)\s*=\s*(\S+)\s*", line)
        if setting:
            key, value = setting.groups()
            if key not in ("Start spectrum", "Method"):
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError(f"{path}: nonfinite {key}")
            if key in config and config[key] != value:
                raise ValueError(f"{path}: conflicting {key}")
            config[key] = value
        values = dict(VALUES.findall(line))
        iteration = re.match(r"\s*ITERATION\s+(\d+)\b", line)
        if iteration:
            i = int(iteration[1])
            if i in rows or set(values) != set(FIELDS):
                raise ValueError(f"{path}: duplicate or invalid iteration {i}")
            rows[i] = values
        elif "[NEXTDT INIT]" in line:
            if initial is not None or set(values) != {"CFLM", "DT", "CN"}:
                raise ValueError(f"{path}: duplicate or invalid initial diagnostics")
            initial = values
        elif re.match(r"\s*Final T=", line):
            if final is not None or set(values) != {"T", "DT", "CN"}:
                raise ValueError(f"{path}: duplicate or invalid final diagnostics")
            final = values
        match = re.fullmatch(r"\s*Elapsed CPU time for (\d+) steps \(s\) =\s*(" + NUMBER + r")\s*", line)
        if match:
            if elapsed is not None or int(match[1]) != config.get("Steps"):
                raise ValueError(f"{path}: inconsistent elapsed-time record")
            elapsed = float(match[2])
        match = re.fullmatch(r"\s*FPS =\s*(" + NUMBER + r")\s*", line)
        if match:
            fps = float(match[1])
    required = {"N", "Re", "K0", "Steps", "CFL", "Update", "Start spectrum", "Method", "PAO seed in"}
    if set(config) != required or initial is None or final is None or elapsed is None or fps is None:
        raise ValueError(f"{path}: incomplete sim log")
    steps, update = int(config["Steps"]), int(config["Update"])
    if steps < 0 or update <= 0:
        raise ValueError(f"{path}: invalid step/update count")
    expected = {i for i in range(1, steps + 1) if i == 1 or i == steps or i % update == 0}
    if set(rows) != expected:
        raise ValueError(f"{path}: missing or unexpected checkpoints")
    if elapsed <= 0 or not math.isfinite(elapsed) or fps < 0 or not math.isfinite(fps):
        raise ValueError(f"{path}: invalid elapsed time or FPS")
    return config, {"initial": initial, **rows, "final": final}, elapsed, fps


def rounding_radius(number):
    return 0.5 * float(Decimal(1).scaleb(Decimal(number).as_tuple().exponent))


def compare(reference, candidate, rtol=1e-5, atol=5e-10):
    ref_config, ref_rows, ref_time, ref_fps = read_log(reference)
    config, rows, elapsed, fps = read_log(candidate)
    if config != ref_config:
        changed = [f"{k}: {ref_config[k]} != {config[k]}" for k in config if config[k] != ref_config[k]]
        raise ValueError("Case parameters differ: " + "; ".join(changed))
    errors, failures = defaultdict(list), []
    for checkpoint, ref in ref_rows.items():
        for field, number in ref.items():
            other = rows[checkpoint][field]
            a, b = float(number), float(other)
            if not math.isfinite(a) or not math.isfinite(b):
                raise ValueError(f"Nonfinite {field} at {checkpoint}")
            delta = abs(a - b)
            relative = delta / abs(a) if a else (0.0 if delta == 0 else math.inf)
            tolerance = atol + rtol * abs(a) + rounding_radius(number) + rounding_radius(other)
            errors[field].append((delta, relative))
            if delta > tolerance:
                failures.append(f"{checkpoint} {field}: {number} -> {other} (|Δ|={delta:.6g})")
    print(f"Matched case: N={int(config['N'])}, {int(config['Steps'])} steps, {config['Method']}, seed={int(config['PAO seed in'])}")
    print(f"Tolerance: atol={atol:g} + rtol={rtol:g} × |reference| + both printed rounding radii")
    print(f"{'Diagnostic':<12} {'Max |Δ|':>14} {'Max relative Δ':>16}")
    for field in FIELDS:
        if errors[field]:
            print(f"{field:<12} {max(e[0] for e in errors[field]):14.6g} {max(e[1] for e in errors[field]):16.6g}")
    print(f"Wall seconds: reference={ref_time:.8g}, candidate={elapsed:.8g}")
    print(f"FPS:          reference={ref_fps:.8g}, candidate={fps:.8g}")
    print(f"T_R=T/wall:   reference={float(ref_rows['final']['T'])/ref_time:.8g}, candidate={float(rows['final']['T'])/elapsed:.8g}")
    print("Timing values describe these runs; use repeated measurements for performance conclusions.")
    for failure in failures:
        print("FAIL " + failure)
    print("FAIL" if failures else "PASS: all initial, checkpoint, and final diagnostics agree within tolerance.")
    return not failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--rtol", type=float, default=1e-5)
    parser.add_argument("--atol", type=float, default=5e-10)
    args = parser.parse_args()
    if any(not math.isfinite(x) or x < 0 for x in [args.rtol, args.atol]):
        parser.error("tolerances must be finite and nonnegative")
    try:
        return 0 if compare(args.reference, args.candidate, args.rtol, args.atol) else 1
    except (OSError, ValueError) as error:
        print(f"compare_sim: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
