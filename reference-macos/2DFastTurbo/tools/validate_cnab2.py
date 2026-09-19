#!/usr/bin/env python3
"""Validate native CNAB2 fields against the independent Python MLX solver (optional)."""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

from compare_sim import compare, read_log

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-repo", type=Path, default=HERE,
                        help="directory containing turbo_simulator.py or the palinstrophy package")
    parser.add_argument("--full", action="store_true", help="also run the N=512, 1001-step baseline case")
    parser.add_argument("--output", type=Path, help="retain logs, native full-precision JSON, and error norms")
    args = parser.parse_args()
    source = args.python_repo.resolve()
    sys.path.insert(0, str(source))
    with contextlib.redirect_stdout(io.StringIO()):
        if (source / "turbo_simulator.py").is_file():
            import turbo_simulator as dns
        else:
            from palinstrophy import turbo_simulator as dns
    if dns._mx is None:
        parser.error("MLX is required for the numerical comparison")
    # Cover startup, every-step updates, intermittent/final updates, and the
    # native 128-step command-buffer boundary between diagnostic checkpoints.
    cases = [(32, 1, 100, 1), (32, 5, 1, 5010), (64, 7, 3, 149), (32, 260, 1000, 1)]
    if args.full:
        cases.append((512, 1001, 100, 1))
    summary, failures = [], []
    with tempfile.TemporaryDirectory(prefix="validate-cnab2-") as directory:
        root = args.output.resolve() if args.output else Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        for n, steps, update, seed in cases:
            case = root / f"n{n}-steps{steps}-update{update}-seed{seed}"
            case.mkdir(exist_ok=True)
            final = case / "state"
            native_log = case / "swift.txt"
            with native_log.open("w") as stream:
                subprocess.run([str(HERE / "sim"), str(n), "10000", "10", str(steps), ".25", "metal",
                                str(update), "PAO", "CNAB2", "--json", str(case / "swift.json"),
                                "--state-out", str(final)], check=True, stdout=stream,
                               env={**os.environ, "SCIPYTURBO_SEED": str(seed)})
            os.environ["SCIPYTURBO_SEED"] = str(seed)
            python_log = case / "python.txt"
            with python_log.open("w") as stream, contextlib.redirect_stdout(stream):
                # Native initialization uses the same historical RNG and spectrum,
                # with small differences from each platform's Float32 math library.
                initial = dns.create_dns_state(N=n, Re=10000, K0=10, CFL=.25, backend="mlx",
                                               seed=seed, start_spectrum="PAO", populate_compact_ur=False)
                expected = np.asarray(initial.om2).ravel().astype(np.complex128)
                actual = np.fromfile(final / "initial-omega.c64", dtype="<c8").astype(np.complex128)
                initial_error = float(np.linalg.norm(actual - expected) / np.linalg.norm(expected))
                assert initial_error < 2e-6, f"Native initialization mismatch: {initial_error}"
                del initial
                # Truncate initialization-only chatter: this log is one full run.
                stream.seek(0); stream.truncate()
                state = dns.run_dns(N=n, Re=10000, K0=10, STEPS=steps, CFL=.25, backend="mlx",
                                    start_spectrum="PAO", UPDATE=update, method="CNAB2")
            with contextlib.redirect_stdout(io.StringIO()):
                assert compare(python_log, native_log), f"Diagnostic mismatch in {case.name}"
            norms = {}
            # The unchanged MLX reference uses omega - omega*ARG, reciprocal
            # multiplication and a Double host controller. The corrected native
            # path uses omega*(1-ARG), division and Float32 controller arithmetic.
            # Allow their accumulated Float32 drift here; independent one-step
            # tests remain tighter and app/command parity remains bitwise.
            for name, array, tolerance in [("omega", state.om2, 3e-5), ("history", state.fnm1, 3e-4),
                                            ("velocity", state.ur_full[:2], 3e-5)]:
                dtype, suffix = ("<f4", ".f32") if name == "velocity" else ("<c8", ".c64")
                ref = np.asarray(array).ravel().astype(np.complex128)
                candidate = np.fromfile(final / (name + suffix), dtype=dtype).astype(np.complex128)
                assert candidate.shape == ref.shape and np.all(np.isfinite(candidate))
                diff = candidate - ref
                l2 = float(np.linalg.norm(diff) / max(np.linalg.norm(ref), 1e-30))
                linf = float(np.max(np.abs(diff)) / max(np.max(np.abs(ref)), 1e-30))
                if max(l2, linf) > tolerance:
                    failures.append(f"{case.name} {name}: L2={l2}, Linf={linf} > {tolerance}")
                norms[name] = {"relative_l2": l2, "relative_linf": linf, "tolerance": tolerance}
            summary.append({"case": case.name, "backend": "mlx",
                            "initial_relative_l2": initial_error, "fields": norms})
            (case / "errors.json").write_text(json.dumps(summary[-1], indent=2) + "\n")
            passed = all(max(v["relative_l2"], v["relative_linf"]) <= v["tolerance"] for v in norms.values())
            print(f"{'PASS' if passed else 'FAIL'} {case.name}: " + ", ".join(f"{k} L2={v['relative_l2']:.3g}" for k, v in norms.items()), flush=True)
            del state
        # A truncated run must fail even if its surviving values match exactly.
        broken = Path(directory) / "truncated.txt"
        broken.write_text("\n".join(line for line in native_log.read_text().splitlines()
                                    if not line.lstrip().startswith("ITERATION")))
        try:
            read_log(broken)
        except ValueError:
            pass
        else:
            raise AssertionError("The log comparator accepted missing checkpoints")
        # A numerical mismatch must fail independently of structural validation.
        broken.write_text(native_log.read_text().replace("TAU_L=1.", "TAU_L=2."))
        with contextlib.redirect_stdout(io.StringIO()):
            assert not compare(native_log, broken), "The log comparator accepted changed diagnostics"
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print("PASS log rejection checks")
        if failures:
            raise AssertionError("\n".join(failures))


if __name__ == "__main__":
    main()
