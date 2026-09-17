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
    parser.add_argument("--mode", choices=["headless", "gui", "both"], default="headless")
    parser.add_argument("--output", type=Path, default=Path("evidence/linux"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    modes = ["headless", "gui"] if args.mode == "both" else [args.mode]
    for n in args.resolutions:
        for repeat in range(args.runs):
            for mode in modes:
                output = args.output / f"n{n}-{mode}-{repeat}.json"
                command = [sys.executable, "-m", "fastturbo", "--headless" if mode == "headless" else "--gui-benchmark",
                    "--resolution", str(n), "--reynolds", "50000", "--k0", "4", "--seed", "2", "--cfl", "3",
                    "--integrator", "etdrk4", "--operator", "stress", "--wall-seconds", str(args.seconds),
                    "--output", str(output)]
                command += ["--warmup", "8", "--batch", "8"] if mode == "headless" else ["--gui-warmup-seconds", "2", "--canvas-size", "640"]
                result = subprocess.run(command, capture_output=True, text=True, timeout=240)
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
                report = json.loads(output.read_text())
                stats, seconds = report["stats"], report["seconds"]
                assert stats["is_finite"] and stats["steps"] > 0
                rows.append({"n": n, "repeat": repeat, "mode": mode, "seconds": seconds,
                    "steps": stats["steps"], "simulated_time": stats["simulation_time"],
                    "R_turbo": stats["simulation_time"] / seconds, "ms_per_step": 1000 * seconds / stats["steps"],
                    "steps_per_s": stats["steps"] / seconds, "command": command})
                print(json.dumps(rows[-1]), flush=True)
    summaries = {}
    for mode in modes:
        summary = {"mode": mode, "runs": [r for r in rows if r["mode"] == mode], "medians": []}
        for n in args.resolutions:
            group = [r for r in summary["runs"] if r["n"] == n]
            summary["medians"].append({"n": n, **{key: statistics.median(r[key] for r in group)
                for key in ("R_turbo", "ms_per_step", "steps_per_s")}})
        (args.output / f"performance-{mode}.json").write_text(json.dumps(summary, indent=2) + "\n")
        summaries[mode] = summary
    if args.mode == "both":
        comparison = []
        for headless, gui in zip(summaries["headless"]["medians"], summaries["gui"]["medians"]):
            comparison.append({"n": headless["n"], "headless": headless, "interactive": gui,
                "gui_overhead_percent": 100 * (headless["R_turbo"] - gui["R_turbo"]) / headless["R_turbo"]})
        (args.output / "performance-comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")



if __name__ == "__main__":
    main()
