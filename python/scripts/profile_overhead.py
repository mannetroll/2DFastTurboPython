"""Measure native batch, diagnostic, snapshot and no-op ABI costs separately."""
import json
from pathlib import Path
import statistics
import time
from fastturbo import Configuration, Engine

out = Path(__file__).resolve().parents[1] / "results/profile.json"
out.parent.mkdir(parents=True, exist_ok=True)
with Engine(Configuration(resolution=1024, reynolds=50000)) as engine:
    engine.advance(16)
    engine.export(snapshot=True)
    engine.reset()
    def measure(call, count):
        samples = []
        for _ in range(count):
            started = time.perf_counter_ns()
            call()
            samples.append((time.perf_counter_ns()-started)/1e6)
        return {"count": count, "median_ms": statistics.median(samples), "mean_ms": statistics.mean(samples), "min_ms": min(samples)}
    report = {}
    report["batch8"] = measure(lambda: engine.advance(8), 100)
    report["inspect"] = measure(engine.inspect, 100)
    report["vorticity_snapshot"] = measure(lambda: engine.export(snapshot=True, include_velocity=False), 100)
    report["velocity_snapshot"] = measure(lambda: engine.export(snapshot=True, include_velocity=True), 100)
    report["noop_abi_upper_bound"] = measure(lambda: engine.advance(0), 2000)
    report["notes"] = ["No-op includes JSON encode/parse, ctypes, native dispatch, clock/statistics read and response allocation.",
                       "Inspection and snapshots include existing native GPU synchronization.",
                       "Timings exclude initialization and first FFT-plan creation."]
    out.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))
