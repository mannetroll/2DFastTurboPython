"""Matched Linux CUDA measurements against the stored Windows configuration."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--resolutions", type=int, nargs="+", default=[1024, 2048])
    parser.add_argument("--mode", choices=["headless", "gui"], default="headless")
    parser.add_argument("--output", type=Path, default=Path("evidence/linux"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in args.resolutions:
        for repeat in range(args.runs):
            output = args.output / f"n{n}-{args.mode}-{repeat}.json"
            command = [sys.executable, "-m", "fastturbo", "--headless" if args.mode == "headless" else "--gui-benchmark",
                "--resolution", str(n), "--reynolds", "50000", "--k0", "4", "--seed", "2", "--cfl", "3",
                "--integrator", "etdrk4", "--operator", "stress", "--wall-seconds", str(args.seconds),
                "--output", str(output)]
            command += ["--warmup", "8", "--batch", "8"] if args.mode == "headless" else ["--gui-warmup-seconds", "2", "--canvas-size", "640"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=240)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            report = json.loads(output.read_text())
            stats, seconds = report["stats"], report["seconds"]
            assert stats["is_finite"] and stats["steps"] > 0
            rows.append({"n": n, "repeat": repeat, "mode": args.mode, "seconds": seconds,
                "steps": stats["steps"], "simulated_time": stats["simulation_time"],
                "R_turbo": stats["simulation_time"] / seconds, "ms_per_step": 1000 * seconds / stats["steps"],
                "steps_per_s": stats["steps"] / seconds, "command": command})
            print(json.dumps(rows[-1]), flush=True)
    summary = {"mode": args.mode, "runs": rows, "medians": []}
    for n in args.resolutions:
        group = [r for r in rows if r["n"] == n]
        summary["medians"].append({"n": n, **{key: statistics.median(r[key] for r in group)
            for key in ("R_turbo", "ms_per_step", "steps_per_s")}})
    (args.output / f"performance-{args.mode}.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
