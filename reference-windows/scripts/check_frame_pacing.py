"""Measure presented simulation images independently of solver steps/second."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, default=root / "reference-windows/dist/2DFastTurbo.exe")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolutions", type=int, nargs="+", default=[1024, 2048])
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--min-field-fps", type=float, default=0,
                        help="Optional hardware-specific minimum for new simulation images/s")
    args = parser.parse_args()
    if args.seconds <= 0 or args.min_field_fps < 0:
        parser.error("Duration must be positive and minimum FPS nonnegative")
    executable = args.executable.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for n in args.resolutions:
        report = output / f"n{n}.json"
        command = [str(executable), "--gui-benchmark", "--resolution", str(n),
                   "--k0", "10", "--reynolds", "50000", "--wall-seconds", str(args.seconds),
                   "--gui-warmup-seconds", "2", "--output", str(report)]
        # Run sequentially: simultaneous CUDA workloads invalidate frame timing.
        with report.with_suffix(".log").open("w") as log:
            subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=max(120, args.seconds + 60))
        run = json.loads(report.read_text())
        frames = run["rendering"]["distinct_field_frames"]
        presentations = run["rendering"]["presentations"]
        field_fps = (frames - 1) * run["presented_fps"] / (presentations - 1) if presentations > 1 else 0
        passed = bool(run["passed"] and run["stats"]["is_finite"] and frames > 1
                      and field_fps >= args.min_field_fps)
        result = {"resolution": n, "steps_per_s": run["steps_per_s"],
                  "presented_fps": run["presented_fps"], "distinct_field_fps": field_fps,
                  "distinct_field_frames": frames, "passed": passed}
        results.append(result)
        print(json.dumps(result), flush=True)
    summary = {"executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
               "seconds_per_run": args.seconds, "minimum_field_fps": args.min_field_fps,
               "runs": results, "passed": all(row["passed"] for row in results)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if not summary["passed"]:
        raise SystemExit("Frame pacing check failed; see the reports in " + str(output))


if __name__ == "__main__":
    main()
