#!/usr/bin/env python3
"""Independent FP64 reference and black-box GPU acceptance tests.

Install NumPy for this developer tool; the app has no Python dependency.
Arrays are [y, x]. Spectra are normalized Fourier-series coefficients:
omega(x,y) = sum omega_hat[ky,kx] exp(i (kx*x + ky*y)).
The reference evaluates -u*omega_x-v*omega_y, independently of the app's
stress-form implementation. A separate direct convolution checks padding.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import functools
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

import numpy as np


METHODS = ("imexRK3", "ifHeunRK3", "etdrk4")
OPERATORS = ("stress", "advective")


class FourierGrid:
    def __init__(self, n: int):
        if n < 8 or n % 2:
            raise ValueError("N must be even and at least 8")
        self.n, self.m = n, 3 * n // 2
        self.k = np.rint(np.fft.fftfreq(n) * n).astype(int)
        self.kx, self.ky = np.meshgrid(self.k, self.k)
        self.k2 = self.kx**2 + self.ky**2
        self.inverse_k2 = np.zeros((n, n))
        np.divide(1.0, self.k2, out=self.inverse_k2, where=self.k2 != 0)
        self.mask = (np.abs(self.kx) < n // 2) & (np.abs(self.ky) < n // 2)
        self.mask[0, 0] = False
        self.padded_indices = np.ix_(self.k % self.m, self.k % self.m)

    def project(self, spectrum: np.ndarray) -> np.ndarray:
        return np.asarray(spectrum, dtype=np.complex128) * self.mask

    def transform(self, field: np.ndarray) -> np.ndarray:
        return self.project(np.fft.fft2(field) / self.n**2)

    def physical(self, spectrum: np.ndarray) -> np.ndarray:
        return (np.fft.ifft2(spectrum) * self.n**2).real

    def padded_physical(self, spectrum: np.ndarray) -> np.ndarray:
        padded = np.zeros((self.m, self.m), dtype=np.complex128)
        padded[self.padded_indices] = self.project(spectrum)
        return (np.fft.ifft2(padded) * self.m**2).real

    def truncate(self, field: np.ndarray) -> np.ndarray:
        return self.project((np.fft.fft2(field) / self.m**2)[self.padded_indices])

    def velocity_spectra(self, spectrum: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # omega = dx(v) - dy(u), div(u,v) = 0.
        return (1j * self.ky * self.inverse_k2 * spectrum,
                -1j * self.kx * self.inverse_k2 * spectrum)

    def nonlinear(self, spectrum: np.ndarray) -> np.ndarray:
        u_hat, v_hat = self.velocity_spectra(spectrum)
        u, v = self.padded_physical(u_hat), self.padded_physical(v_hat)
        omega_x = self.padded_physical(1j * self.kx * spectrum)
        omega_y = self.padded_physical(1j * self.ky * spectrum)
        return self.truncate(-u * omega_x - v * omega_y)

    def stress_nonlinear(self, spectrum: np.ndarray) -> np.ndarray:
        u_hat, v_hat = self.velocity_spectra(spectrum)
        u, v = self.padded_physical(u_hat), self.padded_physical(v_hat)
        return self.project((self.kx**2 - self.ky**2) * self.truncate(u * v)
                            - self.kx * self.ky * self.truncate(u*u - v*v))

    def direct_nonlinear(self, spectrum: np.ndarray, threshold: float = 1e-13) -> np.ndarray:
        """Exact non-wrapping convolution, with no FFT or padded grid."""
        spectrum = self.project(spectrum)
        active = np.argwhere(np.abs(spectrum) > threshold)
        result = np.zeros_like(spectrum)
        for py, px in active:
            p_x, p_y = int(self.k[px]), int(self.k[py])
            p2 = p_x*p_x + p_y*p_y
            if not p2:
                continue
            for qy, qx in active:
                q_x, q_y = int(self.k[qx]), int(self.k[qy])
                x, y = p_x + q_x, p_y + q_y
                if abs(x) >= self.n // 2 or abs(y) >= self.n // 2:
                    continue
                result[y % self.n, x % self.n] += (
                    (p_y*q_x - p_x*q_y) / p2 * spectrum[py, px] * spectrum[qy, qx])
        return self.project(result)

    def rhs(self, spectrum: np.ndarray, viscosity: float) -> np.ndarray:
        return self.nonlinear(spectrum) - viscosity * self.k2 * spectrum

    def integrate(self, spectrum: np.ndarray, until: float, dt: float,
                  viscosity: float = 0) -> np.ndarray:
        """Classical RK4 in complex128, independent of all candidate integrators."""
        if not (dt > 0 and until >= 0 and math.isfinite(dt) and math.isfinite(until)):
            raise ValueError("Reference dt must be positive and endpoint nonnegative")
        state = self.project(spectrum)
        steps = math.ceil(until / dt)
        for index in range(steps):
            h = min(dt, until - index * dt)
            a = self.rhs(state, viscosity)
            b = self.rhs(state + 0.5*h*a, viscosity)
            c = self.rhs(state + 0.5*h*b, viscosity)
            d = self.rhs(state + h*c, viscosity)
            state = self.project(state + (h/6) * (a + 2*b + 2*c + d))
        return state

    def invariants(self, spectrum: np.ndarray) -> dict[str, float]:
        magnitude = np.abs(spectrum)**2
        energy = float(0.5 * np.sum(magnitude * self.inverse_k2))
        enstrophy = float(0.5 * np.sum(magnitude))
        return {"energy": energy, "enstrophy": enstrophy,
                "rms_vorticity": math.sqrt(2*enstrophy),
                "initial_turnover_time": (2*math.pi / math.sqrt(2*enstrophy)
                                          if enstrophy else math.inf)}

    def error(self, actual: np.ndarray, expected: np.ndarray) -> dict[str, float]:
        a, b = self.invariants(actual), self.invariants(expected)
        return {"vorticity": float(np.linalg.norm(actual - expected)
                                   / max(np.linalg.norm(expected), 1e-30)),
                "energy": abs(a["energy"] - b["energy"]) / max(b["energy"], 1e-30),
                "enstrophy": abs(a["enstrophy"] - b["enstrophy"]) / max(b["enstrophy"], 1e-30)}


def cosine_fixture(n: int, high: bool = False) -> np.ndarray:
    x, y = np.meshgrid(np.arange(n) * 2*math.pi/n, np.arange(n) * 2*math.pi/n)
    if high:
        return np.cos((n//2-2)*x + 3*y) + 0.75*np.cos((n//2-5)*x - 2*y)
    return np.cos(2*x + 3*y) + 0.6*np.cos(4*x-y) + 0.2*np.sin(x+5*y)


def pao_fixture(n: int, k0: float, seed: int) -> np.ndarray:
    """Shared deterministic isotropic fixture, normalized to unit rms speed.

    Random real-space noise gives Hermitian phases. The shell energy envelope
    is proportional to k^4 exp[-2(k/k0)^2]. The fixture is serialized once, so
    predecessor RNG and vorticity-sign conventions cannot affect comparison.
    """
    grid = FourierGrid(n)
    rng = np.random.Generator(np.random.PCG64(seed))
    noise = grid.transform(rng.standard_normal((n, n)))
    phase = noise / np.maximum(np.abs(noise), 1e-30)
    radius = np.sqrt(grid.k2)
    state = grid.project(phase * radius**2.5 * np.exp(-grid.k2 / k0**2))
    state /= math.sqrt(2 * grid.invariants(state)["energy"])
    return grid.physical(state).astype("<f4")


def executable_path(path: str | Path) -> Path:
    path = Path(path).expanduser().resolve()
    if path.suffix == ".app":
        binaries = list((path / "Contents/MacOS").iterdir())
        named = path / "Contents/MacOS" / path.stem
        path = named if named.exists() else binaries[0]
    if not path.is_file():
        raise ValueError(f"Executable does not exist: {path}")
    return path


def nested(data: dict, *paths: str, default: Any = None) -> Any:
    for path in paths:
        current: Any = data
        for component in path.split("."):
            if not isinstance(current, dict) or component not in current:
                break
            current = current[component]
        else:
            return current
    return default


@dataclass
class SolverResult:
    spectrum: np.ndarray
    report: dict[str, Any]
    command: list[str]
    reused: bool = False

    @property
    def simulation_time(self) -> float:
        value = nested(self.report, "stats.simulation_time", "simulation_time", "stats.time")
        if value is None:
            raise ValueError("Benchmark JSON is missing completed simulation_time")
        return float(value)

    @property
    def steps(self) -> int:
        value = nested(self.report, "stats.steps", "steps", "stats.iteration")
        if value is None:
            raise ValueError("Benchmark JSON is missing completed steps")
        return int(value)

    @property
    def seconds(self) -> float:
        value = nested(self.report, "seconds", "elapsed_seconds", "stats.seconds")
        if value is None or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError("Benchmark JSON is missing finite positive elapsed seconds")
        return float(value)


@functools.lru_cache(maxsize=64)
def _file_hash(path: str, size: int, modified_ns: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_hash(path: Path) -> str:
    stat = path.stat()
    return _file_hash(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def run_solver(app: Path, field: np.ndarray, directory: Path, *, method: str = "imexRK3",
               reynolds: float = 50000, cfl: float = 1.5, batch: int = 8,
               dt: float | None = None, steps: int | None = None,
               until: float | None = None, timeout: float = 300,
               extra: list[str] | None = None, gui: bool = False,
               shared_initial: Path | None = None, operator_name: str | None = None,
               resume: bool = False) -> SolverResult:
    directory.mkdir(parents=True, exist_ok=True)
    n = field.shape[0]
    if field.shape != (n, n):
        raise ValueError("Input must be a square physical vorticity array")
    initial = shared_initial or directory / "initial.f32"
    spectrum_file = directory / "spectrum.c64"
    output = directory / "result.json"
    if shared_initial is None:
        field.astype("<f4").tofile(initial)
    command = [str(app), "--gui-benchmark" if gui else "--benchmark",
               "--resolution", str(n), "--reynolds", str(reynolds),
               "--integrator", method, "--cfl", str(cfl),
               "--initial", str(initial), "--output", str(output), "--spectrum", str(spectrum_file)]
    if not gui:
        command += ["--batch", str(batch)]
    if operator_name is not None:
        command += ["--operator", operator_name]
    if dt is not None:
        command += ["--fixed-dt", str(dt)]
    if (steps is None) == (until is None):
        raise ValueError("Specify exactly one of steps or until")
    command += ["--steps", str(steps)] if steps is not None else ["--until", str(until)]
    command += extra or []
    (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    fingerprint = {"executable_sha256": file_hash(app), "initial_sha256": file_hash(initial),
                   "command_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest()}
    marker = directory / "cache.json"
    reused = False
    if resume and marker.exists() and output.exists() and spectrum_file.exists():
        try:
            cache = json.loads(marker.read_text())
            reused = (cache.get("fingerprint") == fingerprint
                      and cache.get("result_sha256") == file_hash(output)
                      and cache.get("spectrum_sha256") == file_hash(spectrum_file))
        except (OSError, ValueError):
            pass
    if reused:
        report = json.loads(output.read_text())
    else:
        output.unlink(missing_ok=True)
        spectrum_file.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            def decoded(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            (directory / "stdout.txt").write_text(decoded(error.stdout))
            (directory / "stderr.txt").write_text(decoded(error.stderr) + f"\nTimed out after {timeout} seconds.\n")
            raise
        (directory / "stdout.txt").write_text(completed.stdout)
        (directory / "stderr.txt").write_text(completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"Solver exited {completed.returncode}: {completed.stderr[-1500:]}")
        report = json.loads(output.read_text()) if output.exists() else json.loads(completed.stdout)
    raw = np.fromfile(spectrum_file, dtype="<c8")
    if raw.size != n*n:
        raise ValueError(f"Expected {n*n} spectral nodes; got {raw.size}")
    spectrum = raw.reshape(n, n).astype(np.complex128)
    normalization = nested(report, "spectrum_normalization", "config.spectrum_normalization",
                           default="fourier-series")
    if normalization in ("dft", "unnormalized", "fft"):
        spectrum /= n*n
    elif normalization not in ("fourier-series", "normalized"):
        raise ValueError(f"Unknown spectrum normalization: {normalization}")
    if not np.all(np.isfinite(spectrum)):
        raise ValueError("Solver spectrum contains nonfinite values")
    padded = nested(report, "config.padded_resolution", "padded_resolution")
    if padded is not None and int(padded) != 3*n//2:
        raise ValueError(f"Unexpected padded grid {padded}; expected {3*n//2}")
    if operator_name is not None:
        actual_operator = nested(report, "config.operator", "config.nonlinear_formulation", "operator")
        if actual_operator != operator_name:
            raise ValueError(f"Requested {operator_name} operator but solver reported {actual_operator!r}")
    if not reused and output.exists():
        marker.write_text(json.dumps({"schema_version": 1, "fingerprint": fingerprint,
            "result_sha256": file_hash(output), "spectrum_sha256": file_hash(spectrum_file)}, indent=2)+"\n")
    return SolverResult(spectrum, report, command, reused=reused)


def long_diffusion_check(app: Path, directory: Path, method: str, *, n: int = 32,
                         tolerance: float = 2e-6, operator_name: str | None = None,
                         resume: bool = False) -> dict[str, Any]:
    """Detect accumulated small-increment loss at reference-like step counts.

    Collinear x modes have exactly zero advection, but different decay rates.
    Their exp(-nu*k²*dt) factors round to 1 in Float32 at this timestep;
    an uncompensated state update may freeze instead of resolving the decay.
    """
    grid = FourierGrid(n)
    x = np.arange(n) * 2*math.pi/n
    field = np.broadcast_to(2*np.cos(x) + 0.75*np.sin(2*x), (n, n)).astype(np.float32).copy()
    run = run_solver(app, field, directory, method=method, reynolds=50000,
                     dt=0.0001, steps=3500, batch=32, operator_name=operator_name, resume=resume)
    expected = grid.transform(field) * np.exp(-2e-5 * grid.k2 * run.simulation_time)
    error = grid.error(run.spectrum, expected)
    if run.steps != 3500 or abs(run.simulation_time-0.35) > 2e-7:
        raise AssertionError(f"Long fixed-step clock/count mismatch: {run.steps}, {run.simulation_time}")
    if max(error.values()) > tolerance:
        raise AssertionError(f"Long low-viscosity analytic decay error {error} exceeds {tolerance}")
    return {"error": error, "tolerance": tolerance, "steps": run.steps,
            "simulation_time": run.simulation_time, "dt": 0.0001, "viscosity": 2e-5,
            "modes": [[1, 0], [2, 0]],
            "operator": operator_name, "reused_trajectory": run.reused,
            "reference": "exact independent exponential decay of collinear Fourier modes"}


def validate_gpu(app: Path, output: Path, n: int = 32,
                 methods: tuple[str, ...] = METHODS,
                 operator_name: str | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    grid = FourierGrid(n)
    results: list[dict[str, Any]] = []
    execute = functools.partial(run_solver, operator_name=operator_name)

    def record(name: str, body) -> None:
        started = time.monotonic()
        try:
            details = body(output / name)
            results.append({"name": name, "passed": True, "details": details,
                            "wall_seconds": time.monotonic()-started})
            print(f"PASS {name}", flush=True)
        except Exception as error:
            results.append({"name": name, "passed": False, "error": str(error),
                            "wall_seconds": time.monotonic()-started})
            print(f"FAIL {name}: {error}", flush=True)

    def require(value: bool, message: str) -> None:
        if not value:
            raise AssertionError(message)

    def projection(directory: Path) -> dict:
        x, y = np.meshgrid(np.arange(n)*2*math.pi/n, np.arange(n)*2*math.pi/n)
        field = cosine_fixture(n, high=True) + 3 + 0.25*np.cos(n//2*x+2*y)
        field += 0.125*np.cos(x+n//2*y)
        fields_file = directory / "fields.f32"
        run = execute(app, field, directory, steps=0, extra=["--fields", str(fields_file)])
        expected = grid.transform(field.astype(np.float32))
        error = grid.error(run.spectrum, expected)
        require(error["vorticity"] < 3e-6, f"Projection error {error}")
        require(run.steps == 0 and run.simulation_time == 0, "Snapshot advanced simulation")
        require(float(np.max(np.abs(run.spectrum[~grid.mask]))) < 1e-7,
                "Mean or Nyquist axes were retained")
        fields = np.fromfile(fields_file, dtype="<f4").reshape(3, n, n)
        u, v = np.fft.fft2(fields[1:]) / n**2
        divergence = 1j*(grid.kx*u + grid.ky*v)
        curl = 1j*(grid.kx*v - grid.ky*u)
        residuals = {"divergence": float(np.linalg.norm(divergence)/np.linalg.norm(expected)),
                     "curl": float(np.linalg.norm(curl-expected)/np.linalg.norm(expected)),
                     "snapshot_vorticity": float(np.linalg.norm(grid.transform(fields[0])-expected)
                                                  / np.linalg.norm(expected))}
        require(max(residuals.values()) < 1e-5, f"Snapshot field/curl mismatch {residuals}")
        return {"spectrum": error, "snapshot": residuals}

    record("initial_projection", projection)
    field = cosine_fixture(n).astype(np.float32)
    state = grid.transform(field)
    reference = grid.integrate(state, until=0.2, dt=0.0005, viscosity=1/50000)
    refined = grid.integrate(state, until=0.2, dt=0.00025, viscosity=1/50000)
    reference_uncertainty = grid.error(reference, refined)
    require(max(reference_uncertainty.values()) < 1e-10,
            f"Independent reference has not converged: {reference_uncertainty}")

    for method in methods:
        def long_diffusion(directory: Path, method=method) -> dict:
            return long_diffusion_check(app, directory, method, n=n, operator_name=operator_name)

        def diffusion(directory: Path, method=method) -> dict:
            x, y = np.meshgrid(np.arange(n)*2*math.pi/n, np.arange(n)*2*math.pi/n)
            field = (2*np.cos(2*x+3*y)).astype(np.float32)
            run = execute(app, field, directory, method=method, reynolds=12.5,
                             dt=0.002, steps=50)
            expected = grid.transform(field) * np.exp(-0.08*grid.k2*run.simulation_time)
            error = grid.error(run.spectrum, expected)
            require(max(error.values()) < 1e-4, f"Analytic diffusion error {error}")
            require(run.steps == 50 and abs(run.simulation_time-0.1) < 2e-7,
                    "Completed time/step accounting differs from fixed-step request")
            return error

        def nonlinear(directory: Path, method=method) -> dict:
            fields_file = directory / "fields.f32"
            run = execute(app, field, directory, method=method, dt=0.002, until=0.2,
                             extra=["--fields", str(fields_file)])
            error = grid.error(run.spectrum, refined)
            require(max(error.values()) < 2e-4, f"Independent FP64 RK4 mismatch {error}")
            require(abs(run.simulation_time-0.2) < 2e-7, "Failed exact physical endpoint")
            require(np.linalg.norm(run.spectrum-state) > 0.005*np.linalg.norm(state),
                    "Test did not exercise nontrivial advection")
            fields = np.fromfile(fields_file, dtype="<f4").reshape(3, n, n)
            u, v = np.fft.fft2(fields[1:]) / n**2
            curl = 1j*(grid.kx*v-grid.ky*u)
            snapshot_error = grid.error(grid.transform(fields[0]), run.spectrum)
            curl_error = float(np.linalg.norm(curl-run.spectrum)/np.linalg.norm(run.spectrum))
            require(max(snapshot_error.values()) < 3e-6 and curl_error < 1e-5,
                    f"Snapshot fields describe different completed states: {snapshot_error}, curl={curl_error}")
            return {"error": error, "reference_uncertainty": reference_uncertainty,
                    "snapshot_error": snapshot_error, "snapshot_curl_error": curl_error}

        def high_modes(directory: Path, method=method) -> dict:
            high_field = cosine_fixture(n, high=True).astype(np.float32)
            high_state = grid.transform(high_field)
            # A direct, non-wrapping first derivative catches alias leakage,
            # while RK4 checks all generated modes over a finite interval.
            derivative_error = np.max(np.abs(grid.nonlinear(high_state)
                                             - grid.direct_nonlinear(high_state, 1e-7)))
            require(derivative_error < 3e-8, "Padded and direct references disagree")
            reference = grid.integrate(high_state, 0.04, 0.0005, 1/50000)
            run = execute(app, high_field, directory, method=method, dt=0.002, until=0.04)
            error = grid.error(run.spectrum, reference)
            require(max(error.values()) < 1e-4, f"High-mode advection mismatch {error}")
            return error

        def conservation(directory: Path, method=method) -> dict:
            run = execute(app, field, directory, method=method, reynolds=1e30,
                             dt=0.002, steps=100)
            before, after = grid.invariants(state), grid.invariants(run.spectrum)
            drift = {key: abs(after[key]/before[key]-1) for key in ("energy", "enstrophy")}
            require(max(drift.values()) < 2e-4, f"Inviscid invariant drift {drift}")
            require(np.linalg.norm(run.spectrum-state) > 0.005*np.linalg.norm(state),
                    "Conservation test did not evolve")
            return drift

        def batching(directory: Path, method=method) -> dict:
            singles = execute(app, field, directory / "batch1", method=method,
                                 batch=1, steps=32)
            batched = execute(app, field, directory / "batch8", method=method,
                                 batch=8, steps=32)
            error = grid.error(batched.spectrum, singles.spectrum)
            require(max(error.values()) < 3e-6, f"Batch-dependent trajectory {error}")
            require(singles.steps == batched.steps == 32, "Batch overstepped requested count")
            require(abs(singles.simulation_time-batched.simulation_time) < 2e-7,
                    "Batch-dependent physical clock")
            endpoint = execute(app, field, directory / "endpoint", method=method,
                                  batch=8, dt=0.007, until=0.12345)
            require(abs(endpoint.simulation_time-0.12345) < 2e-7,
                    f"Endpoint was overshot: {endpoint.simulation_time}")
            require(endpoint.steps == 18, f"Expected 18 accepted steps, got {endpoint.steps}")
            return {"batch_error": error, "endpoint": endpoint.simulation_time}

        for name, test in (("analytic_diffusion", diffusion),
                           ("long_low_viscosity_diffusion", long_diffusion),
                           ("nonlinear_reference", nonlinear),
                           ("retained_high_modes", high_modes), ("inviscid_conservation", conservation),
                           ("batch_and_endpoint", batching)):
            record(f"{method}_{name}", test)

    report = {"schema_version": 1, "executable": str(app), "resolution": n,
              "operator": operator_name,
              "padded_resolution": grid.m, "reference": "independent NumPy FP64 advective RK4",
              "all_passed": all(row["passed"] for row in results), "tests": results}
    (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=executable_path, required=True)
    parser.add_argument("--output", type=Path, default=Path("Benchmarks/validation"))
    parser.add_argument("--resolution", type=int, default=32)
    parser.add_argument("--integrators", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--operator", choices=OPERATORS)
    args = parser.parse_args()
    report = validate_gpu(args.app, args.output.resolve(), args.resolution, tuple(args.integrators), args.operator)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
