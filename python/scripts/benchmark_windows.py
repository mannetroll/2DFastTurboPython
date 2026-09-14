"""Interleaved Release native/Python measurements; no concurrent GPU workloads."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

from fastturbo.engine import native_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--resolutions", type=int, nargs="+", default=[1024, 2048])
    parser.add_argument("--modes", nargs="+", choices=["headless", "gui"], default=["headless", "gui"])
    parser.add_argument("--output", type=Path, default=Path("results/windows-performance"))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    native_library()
    binary = project / "build/windows/reference/2DFastTurbo.exe"
    environment = {**os.environ, "PATH": str(project / "src/fastturbo/_native") + os.pathsep + os.environ["PATH"]}
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in args.resolutions:
        for mode in args.modes:
            for repeat in range(args.runs):
                for host in (["native", "python"] if repeat % 2 == 0 else ["python", "native"]):
                    stem = args.output / f"n{n}-{mode}-{repeat}-{host}"
                    cmd = [str(binary)] if host == "native" else [sys.executable, "-m", "fastturbo"]
                    cmd += ["--benchmark" if mode == "headless" else "--gui-benchmark",
                        "--resolution", str(n), "--reynolds", "50000", "--k0", "4", "--seed", "2", "--cfl", "3",
                        "--integrator", "etdrk4", "--operator", "stress", "--wall-seconds", str(args.seconds),
                        "--output", str(stem.with_suffix(".json"))]
                    cmd += ["--warmup", "8", "--batch", "8"] if mode == "headless" else ["--gui-warmup-seconds", "2", "--canvas-size", "640"]
                    print(f"N={n} {mode} {repeat+1}/{args.runs} {host}", flush=True)
                    with stem.with_suffix(".log").open("w") as output:
                        subprocess.run(cmd, env=environment, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=240)
                    report = json.loads(stem.with_suffix(".json").read_text())
                    assert report.get("passed", True)
                    assert report["config"]["resolution"] == n and report["config"]["padded_resolution"] == 3*n//2
                    s = report["stats"]
                    assert s["is_finite"] and s["steps"] > 0
                    seconds = report["seconds"]
                    rows.append({"n": n, "mode": mode, "repeat": repeat, "host": host,
                        "steps_per_s": s["steps"]/seconds, "R_turbo": s["simulation_time"]/seconds,
                        "ms_per_step": seconds*1000/s["steps"], "seconds": seconds,
                        "python_poll_percent": report.get("python_ui_poll_wall_percent"), "command": cmd})
                    (args.output / "runs.json").write_text(json.dumps(rows, indent=2)+"\n")
    summary = {"hardware": native_library().info, "runs": args.runs, "seconds": args.seconds,
        "native_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "dll_sha256": hashlib.sha256((project / "src/fastturbo/_native/fastturbo.dll").read_bytes()).hexdigest(),
        "measurements": []}
    for n in args.resolutions:
        for mode in args.modes:
            result = {"n": n, "mode": mode}
            for host in ["native", "python"]:
                group = [r for r in rows if (r["n"], r["mode"], r["host"]) == (n, mode, host)]
                result[host] = {key: statistics.median(r[key] for r in group) for key in ["steps_per_s", "R_turbo", "ms_per_step"]}
                result[host]["R_turbo_range"] = [min(r["R_turbo"] for r in group), max(r["R_turbo"] for r in group)]
            result["python_time_overhead_percent"] = 100*(result["python"]["ms_per_step"]/result["native"]["ms_per_step"]-1)
            result["python_throughput_loss_percent"] = 100*(1-result["python"]["R_turbo"]/result["native"]["R_turbo"])
            summary["measurements"].append(result)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    with (args.output / "runs.csv").open("w", newline="") as f:
        columns = [k for k in rows[0] if k != "command"]
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
