"""Compact architecture comparison using the existing headless benchmark harness.

Each case has its own process so native allocations are released even after a
GPU error. This module never implements a timestep, initializer, or timing loop.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile

from .build import NATIVE, ROOT
from .engine import Configuration, get_backend_info
from .main import parser as production_parser, run_benchmark


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("seconds must be finite and positive")
    return number


def size(value):
    n = int(value)
    if not 16 <= n <= 16384 or n % 2:
        raise argparse.ArgumentTypeError("sizes must be even, between 16 and 16384")
    return n


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def warmup_steps(value):
    number = nonnegative(value)
    if number > 4096:
        raise argparse.ArgumentTypeError("warmup must be at most 4096 steps")
    return number


def parser():
    p = argparse.ArgumentParser(prog="2dbench", description="FastTurbo native GPU architecture benchmark (no GUI)")
    p.add_argument("--seconds", type=positive_seconds, default=20.0, help="timed seconds per size (default: 20)")
    p.add_argument("--sizes", type=size, nargs="+", default=[2048, 4096], help="retained sizes (default: 2048 4096)")
    p.add_argument("--device", type=nonnegative, default=0, help="CUDA visible-device / Metal device index (default: 0)")
    p.add_argument("--warmup", type=warmup_steps, default=8, help="warmup steps, followed by reset (default: 8)")
    p.add_argument("--json", action="store_true", help="emit one JSON document; build diagnostics go to stderr")
    p.add_argument("--_case-file", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--_metadata-only", action="store_true", help=argparse.SUPPRESS)
    return p


def cuda_version(value):
    return f"{value // 1000}.{value % 1000 // 10}" if value is not None else None


def platform_metadata(hardware):
    system = platform.system()
    os_name = platform.platform()
    if system == "Linux":
        try:
            os_name = platform.freedesktop_os_release().get("PRETTY_NAME", os_name)
        except OSError:
            pass
    elif system == "Darwin":
        os_name = "macOS " + platform.mac_ver()[0]
    commit, dirty = None, None
    try:
        commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=normal"], text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    try:
        build = json.loads((NATIVE / "build-manifest.json").read_text())
    except (OSError, ValueError):
        build = {}
    return {
        "hostname": socket.gethostname(), "os": os_name, "arch": platform.machine(),
        "gpu": hardware.get("gpu"), "device_index": hardware.get("device_index"),
        "backend": hardware.get("backend", "unknown").replace(" / ", " + "),
        "compute_capability": hardware.get("compute_capability"),
        "gpu_memory_bytes": hardware.get("gpu_memory_bytes"),
        "physical_memory_bytes": hardware.get("physical_memory_bytes"),
        "unified_memory": hardware.get("unified_memory"),
        "recommended_working_set_bytes": hardware.get("recommended_working_set_bytes"),
        "cuda_runtime": cuda_version(hardware.get("cuda_runtime_version")),
        "cuda_driver": cuda_version(hardware.get("cuda_driver_version")),
        "vkfft_version": build.get("vkfft_version"),
        "cuda_build_architectures": build.get("architectures"),
        "native_directory": str(NATIVE.resolve()),
        "native_sha256": (hashlib.sha256((NATIVE / "fastturbo.dll").read_bytes()).hexdigest()
                          if (NATIVE / "fastturbo.dll").is_file() else None),
        "precision": hardware.get("precision", "float32"),
        "git_commit": commit, "git_dirty": dirty,
    }


def memory_reason(n, hardware):
    # Lower bound only, shared by the existing ETDRK4/stress CUDA and Metal cores:
    # nine retained complex half-spectra, six real coefficient arrays, three
    # 3-field snapshot slots, and two padded real fields. Ignores row alignment,
    # small reductions, VkFFT plans/scratch and driver allocations. Do not claim
    # this predicts peak usage or reject a case on a guessed safety margin.
    padded = Configuration(resolution=n).padded_resolution
    minimum = 84 * n * n + 8 * padded * padded
    available = hardware.get("available_memory_bytes")
    if available is None:
        # Metal's recommended working set is advisory, not a free-memory limit.
        # Only reject an impossible case relative to total unified memory.
        if hardware.get("unified_memory"):
            available = hardware.get("physical_memory_bytes")
    if available is not None and minimum > available:
        return (f"insufficient device memory: at least {minimum / 2**30:.2f} GiB "
                f"of solver buffers required; {available / 2**30:.2f} GiB available")
    return None


def failure(n, message):
    memory = any(term in message.lower() for term in (
        "out of memory", "out_of_memory", "memory allocation", "cannot allocate",
        "insufficient device memory", "std::bad_alloc", "failed_to_allocate",
    )) or bool(re.search(r"VkFFT.*(?:failed:|result)\s*(?:4001|4025)\b", message, re.I))
    return {"n": n, "padded_n": Configuration(resolution=n).padded_resolution,
            "status": "skipped" if memory else "failed",
            "reason": ("insufficient device memory: " if memory and "insufficient device memory" not in message else "") + message}


def run_case(args):
    """Runs only inside the isolated worker, before native initialization."""
    os.environ["FASTTURBO_DEVICE"] = str(args.device)
    n = args.sizes[0]
    hardware = {}
    try:
        hardware = get_backend_info()
        if args._metadata_only:
            return {"hardware": hardware}
        if reason := memory_reason(n, hardware):
            result = failure(n, reason)
        else:
            # Reuse the exact production CLI configuration and harness. These
            # fixed settings are the same on every device, including batch size.
            options = production_parser().parse_args([
                "--benchmark", "--resolution", str(n), "--wall-seconds", str(args.seconds),
                "--warmup", str(args.warmup), "--batch", "8",
                "--integrator", "etdrk4", "--operator", "stress", "--k0", "10",
                "--seed", "2", "--cfl", "3", "--reynolds", str(Configuration().reynolds),
            ])
            report = run_benchmark(options)
            if not report["stats"]["is_finite"]:
                raise RuntimeError("Solver finished with non-finite diagnostics")
            result = {"n": n, "padded_n": report["config"]["padded_resolution"], "status": "ok",
                      "steps": report["stats"]["steps"] - report["initial_stats"]["steps"],
                      "seconds": report["seconds"], "ms_per_step": report["milliseconds_per_step"],
                      "steps_per_second": report["steps_per_s"],
                      "initialization_seconds": report["initialization_seconds"],
                      "native_report": report}
    except Exception as error:
        result = failure(n, str(error) or type(error).__name__)
    return {"hardware": hardware, "result": result}


def launch_case(args, n, *, metadata_only=False):
    with tempfile.TemporaryDirectory(prefix="fastturbo-bench-") as folder:
        output = Path(folder) / "case.json"
        command = [sys.executable, "-m", "fastturbo.bench", "--sizes", str(n),
                   "--seconds", str(args.seconds), "--warmup", str(args.warmup),
                   "--device", str(args.device), "--_case-file", str(output)]
        if metadata_only:
            command.append("--_metadata-only")
        # A file carries results so compiler/native stdout cannot corrupt JSON.
        process = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr)
        if process.returncode or not output.is_file():
            return {"hardware": {}, "result": failure(n, f"native worker exited with code {process.returncode}; see stderr")}
        return json.loads(output.read_text(encoding="utf-8"))


def scaling(results):
    times = {r["n"]: r["ms_per_step"] for r in results if r["status"] == "ok"}
    if 2048 in times and 4096 in times:
        return times[4096] / times[2048]
    return None


def format_header(report):
    lines = ["FastTurbo 2D Benchmark"]
    fields = [("Host", "hostname"), ("OS", "os"), ("Arch", "arch"), ("GPU", "gpu"),
              ("Device", "device_index"), ("Compute", "compute_capability"), ("Backend", "backend")]
    for label, key in fields:
        if report.get(key) is not None:
            lines.append(f"{label + ':':<12}{report[key]}")
    if report.get("gpu_memory_bytes"):
        lines.append(f"VRAM:       {report['gpu_memory_bytes'] / 2**30:.1f} GiB")
    elif report.get("physical_memory_bytes"):
        label = "unified" if report.get("unified_memory") else "system"
        lines.append(f"Memory:     {report['physical_memory_bytes'] / 2**30:.1f} GiB {label}")
    if report.get("cuda_runtime"):
        lines.append(f"CUDA:       {report['cuda_runtime']} runtime / {report.get('cuda_driver', 'unknown')} driver API")
        lines.append(f"GPU targets: {report.get('cuda_build_architectures') or 'unknown'}")
        lines.append(f"Native DLL: {report.get('native_directory') or 'unknown'}")
        lines.append(f"DLL SHA256: {report.get('native_sha256') or 'unknown'}")
    lines.append(f"VkFFT:      {report.get('vkfft_version') or 'unknown'}")
    lines.append(f"Precision:  {report['precision']}")
    lines.append(f"Git:        {(report.get('git_commit') or 'unknown')[:12]}" + (" (dirty)" if report.get("git_dirty") else ""))
    lines.append(f"Workload:   ETDRK4 / stress, k0=10, seed=2, CFL=3, Re={Configuration().reynolds:.8f}")
    lines.append(f"Timing:     synchronized wall time, batch=8, warmup={report['warmup_steps']} steps")
    lines.append(f"Target:     {report['target_seconds']:.1f} s per case")
    lines.extend([" Retained      Padded      Steps    Seconds    ms/step    steps/s", "-" * 64])
    return "\n".join(lines)


def format_result(result):
    prefix = f" {str(result['n']) + '²':<13}{str(result['padded_n']) + '²':<12}"
    if result["status"] == "ok":
        return prefix + (f"{result['steps']:>7} {result['seconds']:>10.2f} "
                         f"{result['ms_per_step']:>10.3f} {result['steps_per_second']:>10.1f}")
    return prefix + f"{result['status'].upper()}: {result['reason']}"


def main(argv=None):
    args = parser().parse_args(argv)
    if args._case_file:
        args._case_file.write_text(json.dumps(run_case(args), allow_nan=False), encoding="utf-8")
        return 0
    if not args.json:
        # Release the probe's GPU context before starting any timed worker.
        probe = launch_case(args, args.sizes[0], metadata_only=True)
        header = {**platform_metadata(probe["hardware"]),
                  "target_seconds": args.seconds, "warmup_steps": args.warmup}
        print(format_header(header), flush=True)
    cases = []
    for n in args.sizes:
        case = launch_case(args, n)
        cases.append(case)
        if not args.json:
            print(format_result(case["result"]), flush=True)
    hardware = next((case["hardware"] for case in cases if case["hardware"]), {})
    results = [case["result"] for case in cases]
    report = {"schema_version": 1, **platform_metadata(hardware),
              "timestamp": datetime.now(timezone.utc).isoformat(),
              "target_seconds": args.seconds, "warmup_steps": args.warmup,
              "timing": "synchronized wall time, including batch submission and final inspection; initialization/warmup/reset excluded",
              "batch_steps": 8, "results": results, "timestep_ratio_4096_2048": scaling(results)}
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
    elif report["timestep_ratio_4096_2048"] is not None:
        print(f"4096/2048 timestep ratio: {report['timestep_ratio_4096_2048']:.2f}x", flush=True)
    return 0 if all(r["status"] == "ok" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
