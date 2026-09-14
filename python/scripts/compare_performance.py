"""Interleave matched Swift/Python headless and visible native-GPU timings."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import numpy as np

p = argparse.ArgumentParser()
p.add_argument("--swift", type=Path, default=Path("/private/tmp/FastTurboPythonReference/Build/Products/Release/2DFastTurbo.app/Contents/MacOS/2DFastTurbo"))
p.add_argument("--runs", type=int, default=5)
p.add_argument("--until", type=float, default=3.0)
p.add_argument("--output", type=Path, default=Path("results/performance"))
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
rows = []
common = ["--resolution", "1024", "--reynolds", "50000", "--k0", "4", "--seed", "2",
          "--cfl", "3", "--integrator", "etdrk4", "--operator", "stress", "--until", str(a.until)]
for mode in ("headless", "gui"):
    for repeat in range(a.runs):
        pair = []
        for host in (["swift", "python"] if repeat % 2 == 0 else ["python", "swift"]):
            stem = a.output / f"{mode}-{repeat}-{host}"
            cmd = [str(a.swift)] if host == "swift" else [sys.executable, "-m", "fastturbo"]
            cmd += ["--benchmark" if mode == "headless" else "--gui-benchmark", *common,
                    "--output", str(stem.with_suffix(".json")), "--spectrum", str(stem.with_suffix(".c64"))]
            if mode == "headless":
                cmd += ["--batch", "8", "--warmup", "16"]
            else:
                cmd += ["--canvas-size", "640", "--gui-warmup-seconds", "2", "--display", "vorticity", "--palette", "ember"]
            print(f"{mode} {repeat+1}/{a.runs} {host}", flush=True)
            with stem.with_suffix(".log").open("w") as log:
                subprocess.run(cmd, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            report = json.loads(stem.with_suffix(".json").read_text())
            if report.get("passed") is False:
                raise RuntimeError(report)
            config = report["config"]
            assert config["resolution"] == 1024 and config["padded_resolution"] == 1536
            stats = report["stats"]
            assert abs(stats["simulation_time"] - a.until) < 1e-12
            data = np.fromfile(stem.with_suffix(".c64"), dtype="<c8")
            assert data.size == 1024**2 and np.isfinite(data).all()
            if mode == "gui":
                canvas = report["canvas"]
                assert abs(canvas["width_points"] - 640) <= 1 and abs(canvas["height_points"] - 640) <= 1, canvas
                assert report["presentation"]["presented_frames_per_second"] > 0
            row = {"mode": mode, "repeat": repeat, "host": host, "command": cmd,
                   "rate": report.get("simtime_per_s", report.get("simulation_time_per_second")),
                   "seconds": report["seconds"], "steps": stats["steps"],
                   "energy": stats["energy"], "enstrophy": stats["enstrophy"],
                   "spectrum_sha256": hashlib.sha256(stem.with_suffix(".c64").read_bytes()).hexdigest(),
                   "gpu_ms_per_step": report.get("gpu_seconds", 0) * 1000 / stats["steps"] if mode=="headless" else None,
                   "fps": report.get("presentation", {}).get("presented_frames_per_second"),
                   "python_poll_wall_percent": report.get("python_ui_poll_wall_percent")}
            rows.append(row)
            pair.append(data)
            (a.output / "runs.json").write_text(json.dumps(rows, indent=2)+"\n")
        error = float(np.linalg.norm(pair[0]-pair[1])/np.linalg.norm(pair[0]))
        assert error < 3e-6, error
        rows[-1]["paired_relative_spectrum_error"] = error
        (a.output / "runs.json").write_text(json.dumps(rows, indent=2)+"\n")
summary = {"runs": a.runs, "until": a.until, "config": common, "measurements": {}}
for mode in ("headless", "gui"):
    values = {}
    for host in ("swift", "python"):
        group = [r for r in rows if r["mode"] == mode and r["host"] == host]
        rates = [r["rate"] for r in group]
        values[host] = {"median_R_turbo": statistics.median(rates), "min": min(rates), "max": max(rates),
                        "median_gpu_ms_per_step": statistics.median(r["gpu_ms_per_step"] for r in group) if mode=="headless" else None,
                        "median_fps": statistics.median(r["fps"] for r in group) if mode=="gui" else None}
    values["python_overhead_percent"] = 100*(values["swift"]["median_R_turbo"]-values["python"]["median_R_turbo"])/values["swift"]["median_R_turbo"]
    summary["measurements"][mode] = values
summary["all_paired_spectra_bitwise_equal"] = all(rows[i]["spectrum_sha256"] == rows[i+1]["spectrum_sha256"] for i in range(0,len(rows),2))
(a.output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
print(json.dumps(summary, indent=2))
