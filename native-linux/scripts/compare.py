#!/usr/bin/env python3
"""Orchestrate existing native benchmark/controller modes; no solver/timing loop."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--sizes", type=int, nargs="+", default=[2048, 4096])
    parser.add_argument("--build", type=Path, default=ROOT / "build-linux")
    parser.add_argument("--output", type=Path, default=ROOT / "native-linux/evidence/performance")
    args = parser.parse_args()
    if args.seconds <= 0 or args.runs < 1:
        parser.error("seconds and runs must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    records, commands = [], []
    modes = ("headless", "disabled", "normal")
    for n in args.sizes:
        for run in range(args.runs):
            # Rotate order to reduce consistent warmup/thermal bias.
            for mode in modes[run % 3:] + modes[:run % 3]:
                env = dict(os.environ)
                if mode == "headless":
                    for name in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"):
                        env.pop(name, None)
                    command = [str(args.build / "2dbench"), "--sizes", str(n), "--seconds", str(args.seconds), "--json"]
                else:
                    command = [str(args.build / "fastturbo-linux"), "--resolution", str(n),
                               "--benchmark-seconds", str(args.seconds), "--interval-ms", "100"]
                    if mode == "disabled":
                        command.append("--no-visualization")
                    elif run == 0:
                        command += ["--capture", str(args.output / f"n{n}-native.png")]
                print(f"N={n} run={run + 1} {mode}", flush=True)
                process = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True,
                                         timeout=args.seconds + 150)
                stem = f"n{n}-{mode}-{run + 1}"
                (args.output / f"{stem}.json").write_text(process.stdout)
                (args.output / f"{stem}.txt").write_text(process.stderr)
                commands.append(dict(argv=command, cwd=str(ROOT), returncode=process.returncode,
                                     display=env.get("DISPLAY"), timestamp=datetime.now(timezone.utc).isoformat()))
                (args.output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
                process.check_returncode()
                report = json.loads(process.stdout)
                if mode == "headless":
                    case = report["results"][0]
                    if case["status"] != "ok":
                        raise RuntimeError(case)
                    ms, rate = case["ms_per_step"], case["steps_per_second"]
                else:
                    if report["stats"]["steps"] <= 0 or report["hardware"]["vkfft_internal_bytes"] <= 0:
                        raise RuntimeError("No production CUDA/VkFFT work")
                    if mode == "normal" and report["rendering"]["device_copy_bytes"] <= 0:
                        raise RuntimeError("No CUDA/OpenGL uploads")
                    if mode == "disabled" and (report["rendering"]["uploads"] or report["snapshots"]):
                        raise RuntimeError("Disabled visualization still extracts fields")
                    ms, rate = report["milliseconds_per_step"], report["steps_per_s"]
                records.append(dict(n=n, run=run + 1, mode=mode, ms_per_step=ms, steps_per_s=rate))
                print(f"  {ms:.4f} ms/step; {rate:.2f} steps/s", flush=True)
    summary = []
    for n in args.sizes:
        baseline = statistics.median(r["ms_per_step"] for r in records if r["n"] == n and r["mode"] == "headless")
        for mode in modes:
            cases = [r for r in records if r["n"] == n and r["mode"] == mode]
            ms = statistics.median(r["ms_per_step"] for r in cases)
            summary.append(dict(n=n, mode=mode, ms_per_step=ms, steps_per_s=1000 / ms,
                                overhead_percent=100 * (ms / baseline - 1),
                                min_ms=min(r["ms_per_step"] for r in cases), max_ms=max(r["ms_per_step"] for r in cases)))
    result = dict(summary=summary, runs=records,
                  overhead_definition="100 * (mode wall ms/step / headless wall ms/step - 1)",
                  note="Same production solver, ETDRK4/stress, PAO, seed 2, k0 10, Re 27898.36614974335, CFL 3, batch 8. "
                       "Headless uses 8 warmup steps/reset; GUI uses existing controller's 2-second warmup/reset, periodic inspection. "
                       "GUI wall time includes field extraction, unlike GPU event time. Rendering FPS is separate. "
                       "Remote presentation is not a local-display rendering benchmark.")
    (args.output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
