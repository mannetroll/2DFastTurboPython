"""
turbo_simulator.py — 2D Homogeneous Turbulence DNS with selectable PAO start spectrum (SciPy / CuPy / MLX port)

This is a structural port of dns_all.cu to Python.

Key ideas kept from the CUDA version:
  • DnsState structure mirrors DnsDeviceState (Nbase, NX, NZ, NK, NX_full, NZ_full, NK_full)
  • UR (compact)  : shape (NZ, NX, 3)   — AoS: [z, x, comp]
  • UC (compact)  : shape (NZ, NK, 3)   — spectral, [z, kx, comp]
  • UR_full (3/2) : shape (3, NZ_full, NX_full)   — SoA: [comp, z, x]
  • UC_full (3/2) : shape (3, NZ_full, NK_full)   — spectral, SoA
  • om2, fnm1     : shape (NZ, NX_half) — spectral vorticity & non-linear term
  • alfa[NX_half], gamma[NZ]           — wave-number vectors
  • Time loop     : STEP2B → STEP3 → STEP2A → NEXTDT, like dns_all.cu

Backends:
  • CPU:  SciPy
  • GPU:  CuPy (if installed); same API used via the `xp` alias.
  • MLX:  Apple Silicon GPU via Metal (if installed); `xp` is `mlx.core`.

MLX differs from NumPy/CuPy in three ways that shape the port:
  • slices are copies, not views, so the in-place `out=` buffer reuse used by
    the SciPy/CuPy paths cannot work — the MLX paths are written functionally
  • there is no float64 on the Metal GPU, so float64 reductions run on the host
  • evaluation is lazy, so each step ends with an explicit `mx.eval` to keep the
    graph (and memory) bounded

This is now a faithful structural port of dns_all.cu:

  • dnsCudaPaoHostInit  → dns_pao_host_init
  • PAO random phases can keep the original k*exp(-(k/K0)^2) shell shape
    or be shell-rescaled to an initial k^-3 energy spectrum
  • dnsCudaCalcom       → dns_calcom_from_uc_full
  • dnsCudaStep2A/2B/3  → dns_step2a / dns_step2b / dns_step3
  • next_dt_gpu         → next_dt

The 3/2 de-aliasing, Crank–Nicolson update, and spectral vorticity
formulas follow the CUDA kernels line-by-line.
"""
from contextlib import nullcontext
import csv
from dataclasses import dataclass, field
import datetime as _dt
import io
import math
import os
import platform
import re
import subprocess
import sys
import time
from typing import Literal, cast

import numpy as _np

SCIPYTURBO_SEED_ENV = "SCIPYTURBO_SEED"
PAO_SEED_MIN = 1
PAO_SEED_MAX = 5010
TimeStepper = Literal["CNAB2", "LS_IMEX_RK3", "CHECK"]
Backend = Literal["cpu", "gpu", "mlx", "auto"]

# MLX's Metal FFT has a fast single-kernel path for transform lengths up to
# 4096; past that it falls back to a roughly 40x slower path. The solver
# transforms the 3/2 de-aliasing grid, so the usable limit is N <= 2*4096/3.
MLX_FFT_FAST_PATH_MAX = 4096


def echo_command(program: str, *fields) -> None:
    """
    Echo a fully-resolved command line, so a log tells you how to repeat the run
    even when it was started with defaults. The PAO seed is only part of the
    command when it was pinned; otherwise each run draws a fresh one (the chosen
    value is printed by the initialization banner as "PAO seed in").
    """
    seed = os.environ.get(SCIPYTURBO_SEED_ENV)
    prefix = f"{SCIPYTURBO_SEED_ENV}={seed} " if seed else ""
    args = " ".join(f"{f:.10g}" if isinstance(f, float) else str(f) for f in fields)
    print(f"\r\n$ {prefix}uv run {program} {args}")


def pao_seed_from_env(default: int | None = None) -> int | None:
    raw = os.environ.get(SCIPYTURBO_SEED_ENV)
    if raw is None or raw.strip() == "":
        return default

    try:
        seed = int(raw.strip(), 10)
    except ValueError as exc:
        raise ValueError(
            f"{SCIPYTURBO_SEED_ENV} must be an integer from "
            f"{PAO_SEED_MIN} to {PAO_SEED_MAX}; got {raw!r}"
        ) from exc

    if not PAO_SEED_MIN <= seed <= PAO_SEED_MAX:
        raise ValueError(
            f"{SCIPYTURBO_SEED_ENV} must be from "
            f"{PAO_SEED_MIN} to {PAO_SEED_MAX}; got {seed}"
        )

    return seed


def _format_cuda_version(version: int) -> str | None:
    if version <= 0:
        return None
    major = version // 1000
    minor = (version % 1000) // 10
    patch = version % 10
    if patch:
        return f"{major}.{minor}.{patch}"
    return f"{major}.{minor}"


def _nvidia_kernel_driver_version() -> str | None:
    try:
        with open("/proc/driver/nvidia/version", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None

    match = re.search(r"Kernel Module\s+([0-9][0-9.]+)", text)
    return match.group(1) if match else None


def _nvidia_smi_driver_version() -> str | None:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if proc.returncode != 0:
        return None
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return lines[0] if lines else None


def _cuda_driver_summary(cp_module) -> str:
    parts: list[str] = []

    nvidia_driver = _nvidia_kernel_driver_version() or _nvidia_smi_driver_version()
    if nvidia_driver is not None:
        parts.append(f"NVIDIA driver {nvidia_driver}")

    try:
        cuda_driver = _format_cuda_version(int(cp_module.cuda.runtime.driverGetVersion()))
    except Exception:
        cuda_driver = None
    if cuda_driver is not None:
        parts.append(f"CUDA driver {cuda_driver}")

    try:
        cuda_runtime = _format_cuda_version(int(cp_module.cuda.runtime.runtimeGetVersion()))
    except Exception:
        cuda_runtime = None
    if cuda_runtime is not None:
        parts.append(f"CUDA runtime {cuda_runtime}")

    return " | ".join(parts)


try:
    import cupy as _cp
    #_cp.show_config()
    dev = _cp.cuda.Device()
    props = _cp.cuda.runtime.getDeviceProperties(dev.id)
    name = props["name"].decode("utf-8") if isinstance(props["name"], (bytes, bytearray)) else str(props["name"])
    print(f"\r\nGPU: {name}")  # e.g. "NVIDIA GeForce RTX 3090"
    driver_summary = _cuda_driver_summary(_cp)
    if driver_summary:
        print(f"GPU driver: {driver_summary}")
    _cflm_max_abs_sum = None
    if _cp is not None:
        _cflm_max_abs_sum = _cp.ReductionKernel(
            in_params="float32 u, float32 w, float32 inv_dx",
            out_params="float32 out",
            map_expr="(fabsf(u) + fabsf(w)) * inv_dx",
            reduce_expr="max(a, b)",
            post_map_expr="out = a",
            identity="0.0f",
            name="cflm_max_abs_sum_inv_dx",
        )
except Exception:  # CuPy is optional
    _cp = None
    print("\r\nCPU: CuPy not installed")


def _mlx_device_info(mx_module) -> dict:
    """Metal device properties, or {} when Metal cannot report them."""
    try:
        return dict(mx_module.device_info())
    except Exception:
        try:
            return dict(mx_module.metal.device_info())
        except Exception:
            return {}


def _mlx_device_summary(mx_module) -> str:
    """Apple Silicon chip name + working-set size, if Metal can report them."""
    info = _mlx_device_info(mx_module)
    if not info:
        return ""

    name = str(info.get("device_name", "Apple GPU"))
    working_set = info.get("max_recommended_working_set_size")
    if working_set:
        return f"{name} ({float(working_set) / (1024 ** 3):.1f} GiB working set)"
    return name


def mlx_device_name() -> str:
    """Chip name of the active Metal device; '' when MLX is unavailable."""
    if _mx is None:
        return ""
    return str(_mlx_device_info(_mx).get("device_name", "Apple GPU"))


try:
    import mlx.core as _mx  # type: ignore

    # Importing MLX succeeds without a GPU; touching the device is what fails
    # (headless/sandboxed sessions). Force that check now so `auto` can fall
    # back to SciPy instead of dying on the first allocation.
    _mx.eval(_mx.zeros((1,), dtype=_mx.float32))
    _mlx_summary = _mlx_device_summary(_mx)
    print(f"MLX: {_mlx_summary}" if _mlx_summary else "MLX: available")
except Exception as _mlx_exc:  # MLX is optional (Apple Silicon only)
    _mx = None
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        print(f"MLX: unavailable ({type(_mlx_exc).__name__}: {str(_mlx_exc)[:80]})")

print(f"Python: {platform.python_version()} ({platform.python_implementation()}) | OS: {platform.platform()}")
import numpy as np  # in addition to your existing _np alias, this is fine

_TIME_SCALAR_INDEX = {"dt": 0, "cn": 1, "cnm1": 2}
SPECTRUM = Literal["PAO", "KM3"]


def _csv_comment_line(values) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="")
    writer.writerow(values)
    return "# " + buf.getvalue() + "\n"


# ===============================================================
# Optional Numba acceleration (CPU-only) for PAO initialization
#
# Pattern:
#   - one PAO kernel implementation (NumPy)
#   - one dispatcher name used by dns_pao_host_init
#   - if numba exists: dispatcher points to njit() version
#   - else: dispatcher points to pure-Python version
#
# IMPORTANT: no duplicate PAO kernel code.
# ===============================================================
try:
    import numba as _nb  # type: ignore
except Exception:
    _nb = None

def _pao_hash01_impl(x: int, z: int, seed: int, salt: int) -> float:
    """Deterministic decorrelation hash for PAO k^-3 mode phases/weights."""
    a = (
        (float(x) + 1.0) * 12.9898
        + (float(z) + 1.0) * 78.233
        + (float(seed) + 1.0) * 0.010001
        + (float(salt) + 1.0) * 37.719
    )
    s = math.sin(a) * 43758.5453123
    return s - math.floor(s)


if _nb is not None:
    _pao_hash01 = _nb.njit(cache=True)(_pao_hash01_impl)
else:
    _pao_hash01 = _pao_hash01_impl


def _spectrum_bincount_impl(
    om2_flat: _np.ndarray,
    bins_flat: _np.ndarray,
    factor: _np.ndarray,
    nbins: int,
) -> _np.ndarray:
    """Accumulate spectrum shells without full-size float64 temporaries."""
    esum = _np.zeros(nbins, dtype=_np.float64)
    for i in range(om2_flat.size):
        zr = _np.float64(om2_flat[i].real)
        zi = _np.float64(om2_flat[i].imag)
        esum[bins_flat[i]] += (zr * zr + zi * zi) * factor[i]
    return esum


if _nb is not None:
    _spectrum_bincount = _nb.njit(cache=True)(_spectrum_bincount_impl)
else:
    _spectrum_bincount = _spectrum_bincount_impl


def _flow_eddy_metrics_mlx_impl(
    om2_flat: _np.ndarray,
    k2_flat: _np.ndarray,
    rfft_weight: _np.ndarray,
    inv_k_flat: _np.ndarray,
) -> tuple[float, float, float]:
    """Float32-compatible MLX diagnostics without full-grid temporaries."""
    energy_sum = 0.0
    length_sum = 0.0
    nx_half = rfft_weight.size
    for i in range(1, om2_flat.size):
        k2 = k2_flat[i]
        if k2 <= _np.float32(0.0):
            continue
        om = om2_flat[i]
        power = _np.float32(om.real * om.real + om.imag * om.imag)
        power = _np.float32(power / k2)
        weighted = _np.float32(power * rfft_weight[i % nx_half])
        energy_sum += weighted
        length_sum += _np.float32(weighted * inv_k_flat[i])

    # Parseval: for the normalized spectral coefficients used by this solver,
    # sum(|omega_hat|^2/k^2) is mean(u^2+v^2).  Reuse the spectral reduction
    # instead of scanning both 3/2-grid physical velocity planes.
    U = math.sqrt(max(0.0, energy_sum))
    if energy_sum <= 0.0:
        return U, float("nan"), float("nan")
    L = 2.0 * math.pi * length_sum / energy_sum
    return U, L, L / U if U > 0.0 else float("nan")


if _nb is not None:
    _flow_eddy_metrics_mlx_reduction = _nb.njit(cache=True)(_flow_eddy_metrics_mlx_impl)
else:
    _flow_eddy_metrics_mlx_reduction = _flow_eddy_metrics_mlx_impl


def _spectrum_and_flow_metrics_mlx_impl(
    om2_flat: _np.ndarray,
    bins_flat: _np.ndarray,
    factor: _np.ndarray,
    nbins: int,
    k2_flat: _np.ndarray,
    rfft_weight: _np.ndarray,
    inv_k_flat: _np.ndarray,
) -> tuple[_np.ndarray, float, float, float]:
    """Accumulate exact spectrum bins and float32 flow metrics in one pass."""
    esum = _np.zeros(nbins, dtype=_np.float64)
    energy_sum = 0.0
    length_sum = 0.0
    nx_half = rfft_weight.size
    for i in range(om2_flat.size):
        om = om2_flat[i]
        zr = _np.float64(om.real)
        zi = _np.float64(om.imag)
        esum[bins_flat[i]] += (zr * zr + zi * zi) * factor[i]

        k2 = k2_flat[i]
        if i == 0 or k2 <= _np.float32(0.0):
            continue
        power = _np.float32(om.real * om.real + om.imag * om.imag)
        power = _np.float32(power / k2)
        weighted = _np.float32(power * rfft_weight[i % nx_half])
        energy_sum += weighted
        length_sum += _np.float32(weighted * inv_k_flat[i])

    U = math.sqrt(max(0.0, energy_sum))
    if energy_sum <= 0.0:
        return esum, U, float("nan"), float("nan")
    L = 2.0 * math.pi * length_sum / energy_sum
    return esum, U, L, L / U if U > 0.0 else float("nan")


if _nb is not None:
    _spectrum_and_flow_metrics_mlx = _nb.njit(cache=True)(_spectrum_and_flow_metrics_mlx_impl)
else:
    _spectrum_and_flow_metrics_mlx = _spectrum_and_flow_metrics_mlx_impl


def _pao_build_ur_and_stats_impl(
    N: int,
    NE: int,
    K0: np.float32,
    Re: np.float32,
    seed_init: int,
    alfa: np.ndarray,
    gamma: np.ndarray,
    use_km3_spectrum: bool,
):
    """
    Shared PAO core (single source of truth):

      - Generate isotropic random spectrum (Fortran DO 500/510 loops)
      - Hermitian symmetry in Z (Fortran DO 600)
      - Compute averages A(1..7), E110, Q2, W2, VISC (Fortran DO 800/810)
      - Reshuffle (Fortran DO 1000 block)

    IMPORTANT:
      - Keep SERIAL loop order to preserve deterministic RNG call sequence for a given seed.
      - No prints inside: must work for Numba and non-Numba.
    """
    ND2 = N // 2
    NED2 = NE // 2
    PI = np.float32(3.14159265358979)

    # ------------------------------------------------------------------
    # Fortran LCG used in PAO (same constants as frand()).
    # ------------------------------------------------------------------
    IMM = 420029
    IT = 2017
    ID = 5011

    seed = int(seed_init)

    # ------------------------------------------------------------------
    # Fortran random vector RANVEC(97)
    # ------------------------------------------------------------------
    RANVEC = np.zeros(97, dtype=np.float32)

    # "warm-up" 97 calls
    for _ in range(97):
        seed = (seed * IMM + IT) % ID

    # fill RANVEC
    for i in range(97):
        seed = (seed * IMM + IT) % ID
        RANVEC[i] = np.float32(seed) / np.float32(ID)

    NORM = PI * K0 * K0
    AMP2_FLOOR = np.float32(1.0e-40)

    # ------------------------------------------------------------------
    # Host spectral UR: complex field UR(kx,z,comp)
    # comp=0 → u1, comp=1 → u3 (Fortran components 1 and 2)
    #
    #   UR[x,z,c]  where  x ∈ [0..ND2-1], z ∈ [0..NE-1], c ∈ {0,1}
    # ------------------------------------------------------------------
    UR = np.zeros((ND2, NE, 2), dtype=np.complex64)

    # ------------------------------------------------------------------
    # Generate isotropic random spectrum (Fortran DO 500/510 loops)
    # ------------------------------------------------------------------
    for z in range(NE):
        gz = gamma[z]
        for x in range(NED2):
            # frand()
            seed = (seed * IMM + IT) % ID
            r = np.float32(seed) / np.float32(ID)

            # random_from_vec(r)
            idx = int(float(r) * 97.0)
            if idx < 0:
                idx = 0
            if idx > 96:
                idx = 96
            v = RANVEC[idx]
            RANVEC[idx] = r

            th = np.float32(2.0) * PI * v
            ARG = np.complex64(np.cos(th) + 1j * np.sin(th))

            ax = alfa[x]
            K2 = np.float32(ax * ax + gz * gz)
            K = np.float32(np.sqrt(K2)) if K2 > 0.0 else np.float32(0.0)

            if ax == 0.0:
                # ALFA(X) == 0: purely u1 mode
                UR[x, z, 1] = np.complex64(0.0 + 0.0j)

                ABSU2 = np.float32(np.exp(- (K / K0) * (K / K0)) / NORM)
                if use_km3_spectrum and ABSU2 < AMP2_FLOOR:
                    ABSU2 = AMP2_FLOOR
                amp = np.float32(np.sqrt(ABSU2))
                UR[x, z, 0] = np.complex64(amp) * ARG
            else:
                denom = np.float32(1.0) + (gz * gz) / (ax * ax)
                ABSW2 = np.float32(np.exp(- (K / K0) * (K / K0)) / (denom * NORM))
                if use_km3_spectrum and ABSW2 < AMP2_FLOOR:
                    ABSW2 = AMP2_FLOOR
                ampw = np.float32(np.sqrt(ABSW2))

                w = np.complex64(ampw) * ARG
                u = np.complex64(- (gz / ax)) * w  # -GAMMA/ALFA * UR(.,.,2)

                UR[x, z, 1] = w
                UR[x, z, 0] = u

    # Special zero modes (UR(1,1,1)=0, UR(1,1,2)=0 in 1-based Fortran)
    UR[0, 0, 0] = np.complex64(0.0 + 0.0j)
    UR[0, 0, 1] = np.complex64(0.0 + 0.0j)

    # ------------------------------------------------------------------
    # Hermitian symmetry in Z (Fortran DO 600)
    # ------------------------------------------------------------------
    for z in range(1, NED2):
        UR[0, NE - z, 0] = np.conj(UR[0, z, 0])
        UR[0, NE - z, 1] = np.conj(UR[0, z, 1])

    # Zero at Z=NED2+1 (index NED2 in 0-based)
    for x in range(ND2):
        UR[x, NED2, 0] = np.complex64(0.0 + 0.0j)
        UR[x, NED2, 1] = np.complex64(0.0 + 0.0j)

    if use_km3_spectrum:
        # --------------------------------------------------------------
        # PAO-k^-3 start spectrum:
        #   keep PAO random phases and divergence-free relation, but
        #   redistribute each integer k-shell toward E(k) ~ k^-3 through
        #   k/k_Nyquist <= 1. Total energy is normalized back to the
        #   original PAO energy.
        # --------------------------------------------------------------
        k_nyquist = float(ND2)
        kmax_shell = ND2
        shell_energy = np.zeros(kmax_shell + 1, dtype=np.float64)
        shell_weight = np.zeros(kmax_shell + 1, dtype=np.float64)
        target_shell = np.zeros(kmax_shell + 1, dtype=np.float64)

        for x in range(ND2):
            x1 = (x == 0)
            ax2 = float(alfa[x]) * float(alfa[x])
            m = 1.0 if x1 else 2.0

            for z in range(NE):
                U1 = UR[x, z, 0]
                U3 = UR[x, z, 1]
                u1u1 = float(U1.real) * float(U1.real) + float(U1.imag) * float(U1.imag)
                u3u3 = float(U3.real) * float(U3.real) + float(U3.imag) * float(U3.imag)
                gz2 = float(gamma[z]) * float(gamma[z])
                k = math.sqrt(ax2 + gz2)
                shell = int(k + 0.5)
                if k > 0.0 and k <= k_nyquist and shell <= kmax_shell:
                    shell_energy[shell] += m * (u1u1 + u3u3)
                    z_hash = z
                    if x == 0 and z > NED2:
                        z_hash = NE - z
                    mode_jitter = 0.75 + 0.50 * _pao_hash01(x, z_hash, seed_init, 17)
                    shell_weight[shell] += m * mode_jitter

        k0f = float(K0)
        if k0f < 1.0:
            k0f = 1.0

        for shell in range(1, kmax_shell + 1):
            k = float(shell)
            if k < k0f:
                low = k / k0f
                shape = (low * low * low * low) / (k0f * k0f * k0f)
            else:
                shape = 1.0 / (k * k * k)

            target_shell[shell] = shape

        original_energy = 0.0
        target_energy = 0.0
        for shell in range(1, kmax_shell + 1):
            original_energy += shell_energy[shell]
            target_energy += target_shell[shell]

        if original_energy > 0.0 and target_energy > 0.0:
            norm = original_energy / target_energy

            for x in range(ND2):
                ax = float(alfa[x])
                ax2 = float(alfa[x]) * float(alfa[x])
                for z in range(NE):
                    gz = float(gamma[z])
                    gz2 = float(gamma[z]) * float(gamma[z])
                    k = math.sqrt(ax2 + gz2)
                    shell = int(k + 0.5)
                    if k > 0.0 and k <= k_nyquist and shell <= kmax_shell and shell_weight[shell] > 0.0:
                        z_hash = z
                        phase_sign = 1.0
                        if x == 0 and z > NED2:
                            z_hash = NE - z
                            phase_sign = -1.0

                        mode_jitter = 0.75 + 0.50 * _pao_hash01(x, z_hash, seed_init, 17)
                        mode_energy = (norm * target_shell[shell]) * mode_jitter / shell_weight[shell]
                        th = phase_sign * 2.0 * math.pi * _pao_hash01(x, z_hash, seed_init, 53)
                        phase = np.complex64(math.cos(th) + 1j * math.sin(th))

                        if ax == 0.0:
                            UR[x, z, 0] = np.complex64(math.sqrt(mode_energy)) * phase
                            UR[x, z, 1] = np.complex64(0.0 + 0.0j)
                        else:
                            denom = 1.0 + gz2 / ax2
                            w = np.complex64(math.sqrt(mode_energy / denom)) * phase
                            u = np.complex64(-gz / ax) * w
                            UR[x, z, 0] = u
                            UR[x, z, 1] = w
                    else:
                        UR[x, z, 0] = np.complex64(0.0 + 0.0j)
                        UR[x, z, 1] = np.complex64(0.0 + 0.0j)

    # ------------------------------------------------------------------
    # Compute averages A(1..7), E110, Q2, W2, VISC (Fortran DO 800/810)
    # ------------------------------------------------------------------
    A1 = 0.0
    A2 = 0.0
    A3 = 0.0
    A4 = 0.0
    A5 = 0.0
    A6 = 0.0
    A7 = 0.0
    E110 = 0.0

    for x in range(ND2):
        x1 = (x == 0)
        ax2 = float(alfa[x]) * float(alfa[x])

        for z in range(NE):
            U1 = UR[x, z, 0]
            U3 = UR[x, z, 1]

            # Keep this explicit (Numba-friendly, avoids complex abs)
            u1u1 = float(U1.real) * float(U1.real) + float(U1.imag) * float(U1.imag)
            u3u3 = float(U3.real) * float(U3.real) + float(U3.imag) * float(U3.imag)

            gz2 = float(gamma[z]) * float(gamma[z])
            K2f = ax2 + gz2
            m = 1.0 if x1 else 2.0

            A1 += m * u1u1
            A2 += m * u3u3
            A3 += m * u1u1 * ax2
            A4 += m * u1u1 * gz2
            A5 += m * u3u3 * ax2
            A6 += m * u3u3 * gz2
            A7 += m * (u1u1 + u3u3) * K2f * K2f

            if x1:
                E110 += u1u1

    Q2 = A1 + A2
    W2 = A3 + A4 + A5 + A6
    #visc = np.sqrt((Q2 * Q2) / (float(Re) * W2))
    visc = 1.0 / float(Re)

    # ------------------------------------------------------------------
    # Reshuffle (Fortran DO 1000 block)
    # ------------------------------------------------------------------
    for comp in range(2):
        for z in range(NED2 - 1, -1, -1):
            for x in range(ND2):
                # UR(X,N-NED2+Z,I) = UR(X,Z+NED2,I)
                UR[x, N - NED2 + z, comp] = UR[x, z + NED2, comp]

                # IF(Z.LE.(N-NE)) UR(X,Z+NED2,I) = NOLL
                if z <= (N - NE - 1):
                    UR[x, z + NED2, comp] = np.complex64(0.0 + 0.0j)

    return UR, seed, np.float32(visc), Q2, W2, E110, A1, A2, A3, A4, A5, A6, A7


# Dispatcher used by dns_pao_host_init (Numba if available; else Python).
if _nb is not None:
    _pao_build_ur_and_stats = _nb.njit(cache=True)(_pao_build_ur_and_stats_impl)
else:
    _pao_build_ur_and_stats = _pao_build_ur_and_stats_impl

# ===============================================================
# ONLY FFT selection (CPU: scipy.fft, GPU: cupyx.scipy.fft)
# ===============================================================
try:
    import scipy.fft as _spfft  # type: ignore
except Exception:
    _spfft = None

try:
    import cupyx.scipy.fft as _cpfft  # type: ignore
except Exception:
    _cpfft = None


def _fft_mod_for_state(S: "DnsState"):
    """
    ONLY FFT selection:
      - CPU: scipy.fft
      - GPU: cupyx.scipy.fft (fallback to cupy.fft if cupyx.scipy.fft is unavailable)
      - MLX: precision-patched Metal FFT for supported grids, else mlx.core.fft
    """
    if S.backend == "gpu":
        if _cpfft is not None:
            return _cpfft
        return S.xp.fft
    if S.backend == "mlx":
        from palinstrophy._mlx_fft import fft_for_shape

        return fft_for_shape((S.NZ_full, S.NX_full))
    return _spfft


# ===============================================================
# MLX compatibility shims
#
# MLX is close enough to NumPy that most expressions port directly, but a few
# idioms this solver leans on do not exist there:
#   - `xp.complex64(0)` / `xp.float32(0)` — dtypes are not scalar constructors
#   - `arr.astype(dt, copy=False)`        — no `copy` keyword
#   - `arr[...] = value`                  — Ellipsis indexing is rejected
# These helpers keep the shared code paths readable instead of branching inline.
# ===============================================================

def _is_mlx(xp) -> bool:
    return _mx is not None and xp is _mx


def _zero_c(xp):
    """Complex zero usable as an assignment source on any backend."""
    return 0j if _is_mlx(xp) else xp.complex64(0.0 + 0.0j)


def _f32(xp, value):
    """float32 scalar constant; MLX dtypes are not scalar constructors."""
    return float(_np.float32(value)) if _is_mlx(xp) else xp.float32(value)


def _astype32(arr, dtype):
    """astype() without the `copy` keyword MLX does not accept."""
    if _mx is not None and isinstance(arr, _mx.array):
        return arr.astype(dtype)
    return arr.astype(dtype, copy=False)


def _div_by_real(xp, num, den_real):
    """
    Divide a complex array by a real one.

    MLX promotes the real divisor to complex and evaluates the full complex
    quotient (a+bi)/(c+0i) as ((ac+bd) + (bc-ad)i) / (c*c + d*d), which squares
    the denominator. A regulariser like 1e-30 then underflows float32 to zero
    and the result is NaN. Scaling by the reciprocal keeps c in range.
    NumPy and CuPy special-case a real divisor, so they divide directly.
    """
    if _is_mlx(xp):
        return num * (1.0 / den_real)
    return num / den_real

# ===============================================================
# Fortran-style random generator used in PAO (port of frand)
# ===============================================================
def frand(seed_list):
    """
    Port of the Fortran LCG used in PAO:

      IMM = 420029
      IT  = 2017
      ID  = 5011

      seed = (seed*IMM + IT) mod ID
      r    = seed / ID

    `seed_list` is a 1-element list to mimic Fortran SAVE/INTENT(INOUT).
    """
    IMM = 420029
    IT = 2017
    ID = 5011

    seed_list[0] = (seed_list[0] * IMM + IT) % ID
    return np.float32(seed_list[0] / ID)


# ---------------------------------------------------------------------------
# Backend selection: xp = np (CPU) or cp (GPU, if available)
# ---------------------------------------------------------------------------
def get_xp(backend: Backend = "auto"):
    """
    backend = "gpu"  → force CuPy cuFFT (error if not available)
    backend = "mlx"  → force MLX / Metal on Apple Silicon (error if not available)
    backend = "cpu"  → force SciPy FFT
    backend = "auto" → CuPy if available, else MLX if available, else SciPy
    """
    # Auto-select: CUDA first, then Apple Silicon, then CPU
    if backend == "auto":
        if _cp is not None:
            return _cp
        if _mx is not None:
            return _mx
        return _np

    # Explicit GPU / MLX / CPU selection
    if backend == "gpu":
        if _cp is None:
            raise RuntimeError("CuPy is not installed, but backend='gpu' was requested.")
        return _cp

    if backend == "mlx":
        if _mx is None:
            raise RuntimeError(
                "MLX is not available, but backend='mlx' was requested. "
                "MLX needs Apple Silicon plus a reachable Metal device."
            )
        return _mx

    # backend == "cpu"
    return _np


def effective_backend(backend: Backend = "auto") -> str:
    """Resolve "auto" to the backend get_xp() actually hands back."""
    if backend != "auto":
        return backend

    xp = get_xp("auto")
    if _cp is not None and xp is _cp:
        return "gpu"
    if _mx is not None and xp is _mx:
        return "mlx"
    return "cpu"


# ---------------------------------------------------------------------------
# Fortran-style random generator used in PAO, port of frand(seed)
# ---------------------------------------------------------------------------

class Frand:
    """
    Port of the tiny LCG from dns_all.cu:

      IMM = 420029
      IT  = 2017
      ID  = 5011

      seed = (seed*IMM + IT) % ID
      r    = seed / ID
    """
    IMM = 420029
    IT = 2017
    ID = 5011

    def __init__(self, seed: int = 1):
        self.seed = int(seed)

    def __call__(self) -> float:
        self.seed = (self.seed * self.IMM + self.IT) % self.ID
        return float(self.seed) / float(self.ID)


# ===============================================================
# Python equivalent of dnsCudaDumpUCFullCsv
# ===============================================================
def dump_uc_full_csv(S: "DnsState", UC_full, comp: int):
    """
    CSV dumper compatible with step2a_debug.py, but for SoA layout:

        UC_full: (3, NZ_full, NK_full)  # [comp, z, kx]

    We print NX_full rows, NZ_full columns:
      - For i < 2*ND2:
          kx       = i // 2
          imag_row = (i & 1) == 1
          value    = Re or Im of UC_full[comp, z, kx]
      - For i >= 2*ND2, we print 0.0 (as in the debug helper).
    """
    N = S.Nbase
    NX_full = S.NX_full
    NZ_full = S.NZ_full
    NK_full = S.NK_full
    ND2 = N // 2

    # Bring data to NumPy on CPU for printing
    if S.backend == "gpu":
        UC_local = _np.asarray(UC_full.get())
    else:
        UC_local = _np.asarray(UC_full)

    for i in range(NX_full):
        row_vals = []

        use_mode = (i < 2 * ND2)
        if use_mode:
            kx = i // 2           # 0..ND2-1
            imag_row = (i & 1) == 1
        else:
            kx = None
            imag_row = False  # unused

        for z in range(NZ_full):
            if use_mode and kx < NK_full:
                # SoA layout: [comp, z, kx]
                v = UC_local[comp, z, kx]
                val = float(v.imag if imag_row else v.real)
            else:
                val = 0.0

            row_vals.append(f"{val:10.5f}")

        print(",".join(row_vals))

    print(f"[CSV] Wrote UC_full, {NX_full}x{NZ_full}, comp={comp}")


# ---------------------------------------------------------------------------
# DNS state  (Python equivalent of DnsDeviceState)
# ---------------------------------------------------------------------------

@dataclass
class DnsState:
    xp: any                 # scipy or cupy module
    backend: str            # "cpu" or "gpu"

    Nbase: int              # Fortran NX=NZ
    NX: int
    NZ: int
    NK: int

    NX_full: int
    NZ_full: int
    NK_full: int

    Re: float
    K0: float
    visc: float             # viscosity
    cflnum: float           # CFL target
    start_spectrum: SPECTRUM = "KM3"
    seed_init: int = 1
    fft_workers: int = 1

    # Cached FFT module (scipy.fft or cupyx.scipy.fft)
    fft: any = None

    # Reusable cuFFT plans (GPU only)
    fft_plan_rfft2_ur_full: any = None
    fft_plan_irfft2_uc01: any = None

    # Precomputed grid constants for CFL computation (dx==dz==2*pi/N)
    inv_dx: float = 0.0

    # CFL scratch to avoid per-step allocations (full 3/2 grid)
    cfl_tmp: any = None
    cfl_absw: any = None

    # Time integration
    t: float = 0.0
    dt: float = 0.0
    cn: float = 1.0
    cnm1: float = 0.0
    time_scalars: any = None   # GPU only: float32 [dt, cn, cnm1]
    cnm1_needs_update: bool = True
    it: int = 0

    # Spectral wavenumber vectors
    alfa: any = None        # shape (NX_half,)
    gamma: any = None       # shape (NZ,)

    # Compact grid (AoS)
    ur: any = None          # shape (NZ, NX, 3), real
    uc: any = None          # shape (NZ, NK, 3), complex

    # Full 3/2 grid (SoA)
    ur_full: any = None     # shape (3, NZ_full, NX_full), real
    uc_full: any = None     # shape (3, NZ_full, NK_full), complex

    # Vorticity and non-linear history
    om2: any = None         # shape (NZ, NX_half), complex
    fnm1: any = None        # shape (NZ, NX_half), complex

    scratch1: any = None
    scratch2: any = None

    # Precomputed index grids for STEP3 (avoid per-step allocations)
    step3_z_indices: any = None
    step3_kx_indices: any = None
    step3_z_spec: any = None

    # STEP3 scratch buffers & constants (avoid per-step allocations)
    step3_uc1_th: any = None
    step3_uc2_th: any = None
    step3_uc3_th: any = None

    step3_K2: any = None          # float32 (NZ, NX_half)
    step3_GA: any = None          # float32 (NZ, NX_half)
    step3_G2mA2: any = None       # float32 (NZ, NX_half)
    step3_invK2_sub: any = None   # float32 (NZ, NX_half-1)
    step3_invK2_full: any = None  # float32 (NZ, NX_half), MLX only

    step3_ARG: any = None         # float32 (NZ, NX_half)
    step3_DEN: any = None         # float32 (NZ, NX_half)
    step3_NUM: any = None         # complex64 (NZ, NX_half)

    step3_mask_ix0: any = None    # bool (NZ,)
    step3_inv_gamma0: any = None  # float32 (NZ,)  precomputed 1/gamma for ix=0 branch (0 where invalid)
    step3_divxz: any = None       # float32 scalar
    populate_compact_ur: bool = True

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        idx = _TIME_SCALAR_INDEX.get(name)
        if idx is None:
            return
        if self.__dict__.get("backend") != "gpu":
            return
        time_scalars = self.__dict__.get("time_scalars")
        if time_scalars is not None:
            time_scalars[idx] = _np.float32(value)
        if name == "cn":
            object.__setattr__(self, "cnm1_needs_update", True)

    def sync(self):
        """Force synchronization at convenient checkpoints (CuPy / MLX)."""
        if self.backend == "gpu":
            self.xp.cuda.Stream.null.synchronize()  # type: ignore[attr-defined]
        elif self.backend == "mlx":
            self.eval_state()
            _mx.synchronize()

    def eval_state(self):
        """
        Realize the lazy MLX graph for the arrays that carry state across steps.

        MLX builds a graph instead of computing eagerly, so without this the
        solver would accumulate every step's operations until something forced
        evaluation — unbounded memory and meaningless per-step timings.
        """
        if self.backend != "mlx":
            return
        pending = [
            a for a in (self.om2, self.fnm1, self.uc_full, self.ur_full, self.uc, self.ur)
            if a is not None
        ]
        if pending:
            _mx.eval(*pending)

    @staticmethod
    def _scalar_item(x) -> float:
        return float(x.item()) if hasattr(x, "item") else float(x)

    def _flow_eddy_metrics_mlx(self) -> tuple[float, float, float]:
        """
        Same metrics as flow_eddy_metrics, but Metal has no float64: the float32
        fields are formed on-device and the float64 reductions run on the host.
        Unified memory makes that handoff a view rather than a copy, and the
        result matches the SciPy path exactly.
        """
        cached = getattr(self, "_flow_eddy_metrics_cache", None)
        if cached is not None and cached[0] == int(self.it):
            return cached[1]

        k2 = _np.asarray(self.step3_K2)
        nx_half = int(k2.shape[1])

        rfft_weight = getattr(self, "_eddy_rfft_w_np", None)
        if rfft_weight is None or rfft_weight.shape[0] != nx_half:
            rfft_weight = _np.full(nx_half, _np.float32(2.0), dtype=_np.float32)
            rfft_weight[0] = _np.float32(1.0)
            if nx_half > 1:
                rfft_weight[-1] = _np.float32(1.0)
            self._eddy_rfft_w_np = rfft_weight

        inv_k = getattr(self, "_eddy_inv_k_np", None)
        if inv_k is None or inv_k.shape != k2.shape:
            nonzero = k2 > _np.float32(0.0)
            inv_k = _np.where(
                nonzero,
                _np.float32(1.0) / _np.sqrt(_np.where(nonzero, k2, _np.float32(1.0))),
                _np.float32(0.0),
            )
            self._eddy_inv_k_np = inv_k
        return _flow_eddy_metrics_mlx_reduction(
            _np.asarray(self.om2).ravel(),
            k2.ravel(),
            rfft_weight,
            inv_k.ravel(),
        )

    def flow_eddy_metrics(self) -> tuple[float, float, float]:
        if self.backend == "mlx":
            return self._flow_eddy_metrics_mlx()

        xp = self.xp
        u = self.ur_full[0]
        v = self.ur_full[1]

        mean_speed2 = xp.mean(u * u + v * v, dtype=xp.float64)
        U = math.sqrt(max(0.0, self._scalar_item(mean_speed2)))

        k2 = self.step3_K2
        nx_half = int(k2.shape[1])

        rfft_weight = getattr(self, "_eddy_rfft_w", None)
        if rfft_weight is None or rfft_weight.shape[0] != nx_half:
            rfft_weight = xp.empty(nx_half, dtype=xp.float32)
            rfft_weight[:] = xp.float32(2.0)
            rfft_weight[0] = xp.float32(1.0)
            if nx_half > 1:
                rfft_weight[-1] = xp.float32(1.0)
            self._eddy_rfft_w = rfft_weight

        inv_k = getattr(self, "_eddy_inv_k", None)
        if inv_k is None or inv_k.shape != k2.shape:
            nonzero = k2 > xp.float32(0.0)
            inv_k = xp.where(
                nonzero,
                xp.float32(1.0) / xp.sqrt(xp.where(nonzero, k2, xp.float32(1.0))),
                xp.float32(0.0),
            )
            self._eddy_inv_k = inv_k

        # Use the compact vorticity spectrum, not uc_full. The GPU inverse FFT
        # path can clobber uc_full[0:2] while producing ur_full, but om2 remains
        # the authoritative compact spectral state after STEP3.
        om2_power = self.om2.real * self.om2.real + self.om2.imag * self.om2.imag
        safe_k2 = xp.where(k2 > xp.float32(0.0), k2, xp.float32(1.0))
        power = xp.where(k2 > xp.float32(0.0), om2_power / safe_k2, xp.float32(0.0))
        weighted_power = power * rfft_weight
        energy_sum = xp.sum(weighted_power, dtype=xp.float64) - weighted_power[0, 0]
        energy_sum_f = self._scalar_item(energy_sum)

        if energy_sum_f <= 0.0:
            return U, float("nan"), float("nan")

        length_sum = xp.sum(weighted_power * inv_k, dtype=xp.float64)
        L = 2.0 * math.pi * self._scalar_item(length_sum) / energy_sum_f
        tau_l = L / U if U > 0.0 else float("nan")
        return U, L, tau_l


@dataclass
class SpectrumSample:
    nbins: int = 0
    r_max: float = 0.0
    k_nyq: float = 0.0
    energy: _np.ndarray = field(default_factory=lambda: _np.zeros(0, dtype=_np.float64))
    count: _np.ndarray = field(default_factory=lambda: _np.zeros(0, dtype=_np.int32))


@dataclass
class SpectrumAverageState:
    initialized: bool = False
    has_previous: bool = False
    since_restart: bool = False
    Nbase: int = 0
    NX_full: int = 0
    NZ_full: int = 0
    NK_full: int = 0
    nbins: int = 0
    sample_count: int = 0
    first_step: int = 0
    last_step: int = 0
    r_max: float = 0.0
    k_nyq: float = 0.0
    total_dt: float = 0.0
    previous_time: float = 0.0
    first_time: float = 0.0
    last_time: float = 0.0
    previous_energy: _np.ndarray = field(default_factory=lambda: _np.zeros(0, dtype=_np.float64))
    integral_energy: _np.ndarray = field(default_factory=lambda: _np.zeros(0, dtype=_np.float64))
    count: _np.ndarray = field(default_factory=lambda: _np.zeros(0, dtype=_np.int32))


def sync_time_scalars_from_device(S: DnsState) -> None:
    """Refresh host dt/cn/cnm1 after GPU-side timestep updates."""
    if S.backend != "gpu" or S.time_scalars is None:
        return

    vals = _cp.asnumpy(S.time_scalars) if _cp is not None else _np.asarray(S.time_scalars)
    object.__setattr__(S, "dt", float(vals[0]))
    object.__setattr__(S, "cn", float(vals[1]))
    object.__setattr__(S, "cnm1", float(vals[2]))


def _copy_cn_to_cnm1_device(S: DnsState) -> None:
    global _STEP3_COPY_CN_KERNEL
    if S.backend != "gpu" or S.time_scalars is None:
        return
    if not S.cnm1_needs_update:
        return
    if _STEP3_COPY_CN_KERNEL is None:
        _STEP3_COPY_CN_KERNEL = _cp.RawKernel(r'''
        extern "C" __global__
        void turbo_copy_cn_to_cnm1(float* time_scalars) {
            time_scalars[2] = time_scalars[1];
        }
        ''', "turbo_copy_cn_to_cnm1")
    _STEP3_COPY_CN_KERNEL((1,), (1,), (S.time_scalars,))
    S.cnm1_needs_update = False


# ---------------------------------------------------------------------------
# Helper to create a DnsState (dnsCudaInit equivalent)
# ---------------------------------------------------------------------------

def create_dns_state(
    N: int = 8,
    Re: float = 1e5,
    K0: float = 100.0,
    CFL: float = 0.75,
    backend: Backend = "auto",
    seed: int = 1,
    skip_pao: bool = False,
    populate_compact_ur: bool = True,
    start_spectrum: SPECTRUM = "KM3",
) -> DnsState:
    xp = get_xp(backend)
    resolved_backend = effective_backend(backend)

    Nbase = N
    NX = N
    NZ = N

    # Your CUDA code uses 3*N/2 (full 3/2 grid)
    NX_full = 3 * NX // 2
    NZ_full = 3 * NZ // 2
    NK_full = NX_full // 2 + 1

    # Compact spectral NK:
    # For the original PAO/Calcom you used NK = 3*N/4 + 1; we keep that here.
    NK = 3 * NX // 4 + 1

    NX_half = NX // 2
    visc = 0

    fft_workers = min(8, os.cpu_count() or 1)

    state = DnsState(
        xp=xp,
        backend=resolved_backend,
        Nbase=Nbase,
        NX=NX,
        NZ=NZ,
        NK=NK,
        NX_full=NX_full,
        NZ_full=NZ_full,
        NK_full=NK_full,
        Re=Re,
        K0=K0,
        visc=visc,
        cflnum=CFL,
        start_spectrum=start_spectrum,
        seed_init=int(seed),
        fft_workers=fft_workers,
        populate_compact_ur=populate_compact_ur,
    )

    # Cache FFT module for the chosen backend (avoid per-call selection)
    state.fft = _fft_mod_for_state(state)
    if state.backend == "cpu" and state.fft is None:
        raise RuntimeError("scipy.fft import failed; CPU backend requires SciPy.")

    # Precompute inverse grid spacing (dx==dz==2*pi/N)
    state.inv_dx = float(state.Nbase) / (2.0 * math.pi)

    # Allocate arrays
    state.ur = xp.zeros((NZ, NX, 3), dtype=xp.float32)
    state.uc = xp.zeros((NZ, NK, 3), dtype=xp.complex64)

    state.ur_full = xp.zeros((3, NZ_full, NX_full), dtype=xp.float32)
    state.uc_full = xp.zeros((3, NZ_full, NK_full), dtype=xp.complex64)

    # CFL scratch buffers (full 3/2 grid) to avoid per-step temporaries.
    # MLX reduces functionally and never touches these, so skip the allocation —
    # at large N it is a meaningful slice of the unified memory budget.
    if state.backend != "mlx":
        state.cfl_tmp = xp.empty((NZ_full, NX_full), dtype=xp.float32)
        state.cfl_absw = xp.empty((NZ_full, NX_full), dtype=xp.float32)

    if state.backend == "gpu":
        state.time_scalars = xp.asarray((state.dt, state.cn, state.cnm1), dtype=xp.float32)

    state.om2 = xp.zeros((NZ, NX_half), dtype=xp.complex64)
    state.fnm1 = xp.zeros((NZ, NX_half), dtype=xp.complex64)

    state.alfa = xp.zeros((NX_half,), dtype=xp.float32)
    state.gamma = xp.zeros((NZ,), dtype=xp.float32)

    # Reusable cuFFT plans (GPU only)
    if state.backend == "gpu":
        plan_mod = None
        if _cpfft is not None and hasattr(_cpfft, "get_fft_plan"):
            plan_mod = _cpfft

        if plan_mod is not None:
            # Forward: rfft2 on real UR_full over (z,x) axes
            state.fft_plan_rfft2_ur_full = plan_mod.get_fft_plan(
                state.ur_full, axes=(1, 2), value_type="R2C"
            )
            # Inverse: irfft2 on UC_full[0:2] over (z,x) axes back to real
            state.fft_plan_irfft2_uc01 = plan_mod.get_fft_plan(
                state.uc_full[0:2],
                shape=(state.NZ_full, state.NX_full),
                axes=(1, 2),
                value_type="C2R",
            )

        if plan_mod is None:
            print("FFT plan_mod: None")
        else:
            print(f"FFT plan_mod: {plan_mod.__name__}")
    elif state.backend == "mlx":
        fft_label = getattr(state.fft, "label", "mlx.core.fft")
        print(f"FFT: {fft_label} on {_mlx_device_summary(_mx) or 'Apple GPU'}")
        if max(state.NX_full, state.NZ_full) > MLX_FFT_FAST_PATH_MAX:
            print(
                f" WARNING: 3/2 grid is {state.NZ_full}x{state.NX_full}; MLX's fast Metal FFT"
                f" only covers transforms up to {MLX_FFT_FAST_PATH_MAX}."
            )
            print(
                f"          Beyond that it drops to a ~40x slower path — expect the CPU"
                f" backend to win. Use N <= {2 * MLX_FFT_FAST_PATH_MAX // 3} for MLX."
            )
    else:
        print(f"FFT workers (CPU): {state.fft_workers}")

    # PAO-style initialization (dnsCudaPaoHostInit)
    dns_pao_host_init(state, skip_pao=skip_pao)

    # DT and CN will be initialized in run_dns via CFL (like CUDA)
    state.dt = 0.0
    state.cn = 1.0
    state.cnm1 = 0.0

    if state.backend != "mlx":
        state.scratch1 = xp.zeros((NZ, NX_half), dtype=xp.complex64)
        state.scratch2 = xp.zeros((NZ, NX_half), dtype=xp.complex64)

    # Precompute index grids used in STEP3 (avoid per-step allocations)
    NZ = state.NZ
    NX_half = state.NX // 2
    state.step3_z_indices = xp.arange(NZ, dtype=xp.int32)
    state.step3_kx_indices = xp.arange(NX_half, dtype=xp.int32)
    NZ_half = NZ // 2
    zi = state.step3_z_indices
    state.step3_z_spec = xp.where(
        zi <= (NZ_half - 1),
        zi,
        zi + NZ_half,
    )

    # STEP3: preallocate gather buffers for UC low-k band (avoid advanced-index allocs).
    # The MLX path gathers functionally and has no use for them.
    if state.backend != "mlx":
        state.step3_uc1_th = xp.empty((NZ, NX_half), dtype=xp.complex64)
        state.step3_uc2_th = xp.empty((NZ, NX_half), dtype=xp.complex64)
        state.step3_uc3_th = xp.empty((NZ, NX_half), dtype=xp.complex64)

    # STEP3: precompute constant spectral grids (float32) used each step
    ax = state.alfa[None, :]          # (1, NX_half)
    gz = state.gamma[:, None]         # (NZ, 1)
    ax2 = ax * ax
    gz2 = gz * gz

    state.step3_K2 = _astype32(ax2 + gz2, xp.float32)
    state.step3_GA = _astype32(gz * ax, xp.float32)
    state.step3_G2mA2 = _astype32(gz2 - ax2, xp.float32)

    one_f32 = _f32(xp, 1.0)
    tiny_f32 = _f32(xp, 1.0e-30)

    if state.backend == "mlx":
        # MLX reconstructs the full width in one shot (no views to write into a
        # sub-slice), so it needs 1/K2 across all kx rather than just kx >= 1.
        state.step3_invK2_full = _astype32(one_f32 / (state.step3_K2 + tiny_f32), xp.float32)
    elif NX_half > 1:
        state.step3_invK2_sub = _astype32(
            one_f32 / (state.step3_K2[:, 1:] + tiny_f32), xp.float32
        )
    else:
        state.step3_invK2_sub = xp.zeros((NZ, 0), dtype=xp.float32)

    # STEP3: per-step float/complex scratch (avoid allocating ARG/DEN/NUM each step)
    if state.backend != "mlx":
        state.step3_ARG = xp.empty((NZ, NX_half), dtype=xp.float32)
        state.step3_DEN = xp.empty((NZ, NX_half), dtype=xp.float32)
        state.step3_NUM = xp.empty((NZ, NX_half), dtype=xp.complex64)

    # ix=0 branch mask (Z>=1 and GAMMA!=0), constant
    state.step3_mask_ix0 = (state.step3_z_indices >= 1) & (xp.abs(state.gamma) > 0.0)

    # Precompute safe inv_gamma for ix=0 (avoid xp.divide(where=...) which CuPy rejects here)
    mask0 = xp.asarray(state.step3_mask_ix0)  # stays on-device for CuPy/MLX
    safe_gamma = xp.where(mask0, state.gamma, one_f32)  # no zeros in denominator
    inv_gamma0 = _astype32(one_f32 / safe_gamma, xp.float32)
    inv_gamma0 = inv_gamma0 * _astype32(mask0, xp.float32)  # zero out invalid lanes

    state.step3_mask_ix0 = mask0
    state.step3_inv_gamma0 = inv_gamma0

    # DIVXZ = 1/(3NX/2 * 3NZ/2), constant for fixed N.
    # Evaluated in float32 regardless of backend so every backend gets the
    # identical constant (MLX would otherwise fold a float64 Python scalar).
    NX32 = _np.float32(1.5) * _np.float32(state.Nbase)
    NZ32 = _np.float32(1.5) * _np.float32(state.Nbase)
    divxz = _np.float32(1.0) / (NX32 * NZ32)
    state.step3_divxz = float(divxz) if _is_mlx(xp) else xp.float32(divxz)

    return state

# ===============================================================
# Python/Numpy/Scipy port of dnsCudaPaoHostInit, wired into DnsState
# ===============================================================
def dns_pao_host_init(S: DnsState, skip_pao: bool = False):
    xp = S.xp
    N = S.NX
    NE = S.NZ
    ND2 = N // 2
    NED2 = NE // 2
    PI = np.float32(3.14159265358979)

    DXZ = np.float32(2.0) * PI / np.float32(N)
    K0 = np.float32(S.K0)
    use_km3_spectrum = S.start_spectrum == "KM3"
    spectrum_label = "KM3 k^-3" if use_km3_spectrum else "PAO k*exp(-(k/K0)^2)"

    backend_label = {"gpu": "CuPy", "mlx": "MLX"}.get(S.backend, "SciPy")
    print(f"--- INITIALIZING {backend_label} ---", _dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    print(f" N={N}, K0={int(K0)}, Re={S.Re:,.1f}")
    print(f" Start spec. = {spectrum_label}")

    # ------------------------------------------------------------------
    # Build ALFA(N/2) and GAMMA(N)  (Fortran DALFA, DGAMMA, E1, E3)
    # ------------------------------------------------------------------
    alfa = np.zeros(ND2, dtype=np.float32)
    gamma = np.zeros(NE, dtype=np.float32)

    E1 = np.float32(1.0)
    E3 = np.float32(1.0) / E1

    DALFA = np.float32(1.0) / E1
    DGAMMA = np.float32(1.0) / E3

    for x in range(NED2):
        alfa[x] = np.float32(x) * DALFA

    gamma[0] = np.float32(0.0)
    for z in range(1, NED2 + 1):
        gamma[z] = np.float32(z) * DGAMMA
        gamma[NE - z] = -gamma[z]

    # When loading a saved case we only need alfa/gamma; skip the
    # expensive random-spectrum generation whose results will be
    # overwritten by the restart data anyway.
    if skip_pao:
        S.alfa = xp.asarray(alfa, dtype=xp.float32)
        S.gamma = xp.asarray(gamma, dtype=xp.float32)
        print(" PAO spectrum skipped (loading saved case)")
        return

    # ------------------------------------------------------------------
    # Host spectral UR: complex field UR(kx,z,comp)
    # comp=0 → u1, comp=1 → u3 (Fortran components 1 and 2)
    #
    #   UR[x,z,c]  where  x ∈ [0..ND2-1], z ∈ [0..NE-1], c ∈ {0,1}
    # ------------------------------------------------------------------
    UR = np.zeros((ND2, NE, 2), dtype=np.complex64)

    # ------------------------------------------------------------------
    # Fortran random vector RANVEC(97)
    # ------------------------------------------------------------------
    seed_in = int(S.seed_init)
    seed = [seed_in]  # mimics ISEED SAVE

    # ------------------------------------------------------------------
    # Generate isotropic random spectrum (Fortran DO 500/510 loops)
    # ------------------------------------------------------------------
    if use_km3_spectrum:
        print(" Generate PAO random spectrum and shell-rescale to k^-3... " + ("(Numba)" if (_nb is not None) else "(Python)"))
    else:
        print(" Generate PAO random spectrum... " + ("(Numba)" if (_nb is not None) else "(Python)"))

    UR, seed_out, visc_f32, Q2, W2, E110, A1, A2, A3, A4, A5, A6, A7 = _pao_build_ur_and_stats(
        N=N,
        NE=NE,
        K0=np.float32(S.K0),
        Re=np.float32(S.Re),
        seed_init=int(S.seed_init),
        alfa=alfa,
        gamma=gamma,
        use_km3_spectrum=use_km3_spectrum,
    )

    seed[0] = int(seed_out)
    S.visc = np.float32(visc_f32)

    # ------------------------------------------------------------------
    # Extra diagnostics (Fortran WRITE block)
    # ------------------------------------------------------------------
    visc = float(S.visc)
    EP = visc * W2
    De = 2.0 * visc * visc * A7
    KOL = (visc * visc * visc / EP) ** 0.25
    NLAM = 0.0
    if E110 != 0.0:
        NLAM = 2.0 * A1 / E110

    a11 = 2.0 * A1 / Q2 - 1.0
    e11 = 2.0 * (A3 + A4) / W2 - 1.0
    tscale = 0.5 * Q2 / EP
    dxKol = float(DXZ) / KOL
    Lux = 2.0 * math.pi / math.sqrt(2.0 * A1 / A3)
    Luz = 2.0 * math.pi / math.sqrt(2.0 * A1 / A4)
    Lwx = 2.0 * math.pi / math.sqrt(2.0 * A2 / A5)
    Lwz = 2.0 * math.pi / math.sqrt(2.0 * A2 / A6)
    Ceps2 = 0.5 * Q2 * De / (EP * EP)

    # Print diagnostics exactly like the CUDA/Fortran version
    print(f" N           = {N:.8g}")
    print(f" Reynolds n. = {float(S.Re):.8g}")
    print(f" K0          = {K0:.8g}")
    print(f" Energy      = {Q2:.8g}")
    print(f" WiWi        = {W2:.8g}")
    #print(f" Epsilon     = {EP:.8g}")
    #print(f" a11         = {a11:.8g}")
    #print(f" e11         = {e11:.8g}")
    print(f" Time scale  = {tscale:.8g}")
    print(f" Kolmogorov  = {KOL:.8g}")
    print(f" Viscosity   = {visc:.8g}")
    print(f" dx/Kol.     = {dxKol:.8g}")
    #print(f" 2Pi/Nlamda  = {NLAM:.8g}")
    #print(f" 2Pi/Lux     = {Lux:.8g}")
    #print(f" 2Pi/Luz     = {Luz:.8g}")
    #print(f" 2Pi/Lwx     = {Lwx:.8g}")
    #print(f" 2Pi/Lwz     = {Lwz:.8g}")
    #print(f" Deps.       = {De:.8g}")
    #print(f" Ceps2       = {Ceps2:.8g}")
    #print(f" E1          = {float(E1):.8g}")
    #print(f" E3          = {float(E3):.8g}")
    print(f" PAO seed in = {seed_in:.8g}")
    print(f" PAO seed out= {seed[0]:.8g}")

    # ------------------------------------------------------------------
    # Scatter spectral UR → compact UC(kx,z,comp) buffer (current grid)
    #   UC: (NK, NE, 3) on host, but DnsState.uc is (NZ, NK, 3) in xp
    # ------------------------------------------------------------------
    NK = S.NK
    #print(f" UC_host = np.zeros(({NK}, {NE}, 3), dtype=np.complex64)")
    UC_host = np.zeros((NK, NE, 3), dtype=np.complex64)  # only comp 0,1 used

    for z in range(NE):
        for x in range(ND2):
            for c in range(2):
                UC_host[x, z, c] = UR[x, z, c]

    # ALSO build full 3/2-grid UC_full (Fortran-like layout)
    NK_full = S.NK_full
    NZ_full = S.NZ_full

    #print(f" UC_full_host = np.zeros(({NK_full}, {NZ_full}, 3), dtype=np.complex64)")
    UC_full_host = np.zeros((NK_full, NZ_full, 3), dtype=np.complex64)
    for z in range(NE):
        for x in range(ND2):
            for c in range(2):
                UC_full_host[x, z, c] = UR[x, z, c]

    print(f" {S.start_spectrum} INITIALIZATION OK. VISC={float(S.visc):.8g}")

    # ------------------------------------------------------------------
    # Move alfa/gamma/UC/UC_full into DnsState (xp backend, SoA layout)
    # ------------------------------------------------------------------
    S.alfa = xp.asarray(alfa, dtype=xp.float32)
    S.gamma = xp.asarray(gamma, dtype=xp.float32)

    # compact UC: host (NK, NE, 3) → xp (NZ, NK, 3) with axes swap
    # (`[:]` rather than `[...]`: MLX rejects Ellipsis indexing.)
    UC_xp = xp.asarray(UC_host)
    S.uc[:] = xp.transpose(UC_xp, (1, 0, 2))  # (NE,NK,3) == (NZ,NK,3)

    # full UC_full: host (NK_full, NZ_full, 3) → xp (3, NZ_full, NK_full)
    UC_full_xp = xp.asarray(UC_full_host)
    S.uc_full[:] = xp.transpose(UC_full_xp, (2, 1, 0))  # (3,NZ_full,NK_full)

    # ------------------------------------------------------------------
    # Build initial om2 from UC_full (for the rest of the solver)
    # ------------------------------------------------------------------
    # Spectral vorticity from pristine UC_full. Callers always run dns_step2a
    # immediately after create_dns_state, which dealiases UC_full[0:2] and
    # runs the inverse FFT — so there is no need to populate UR_full here.
    # (Avoiding the extra inverse FFT also keeps UC_full[0:2] pristine: the
    # GPU inverse path now writes directly into ur_full[0:2] via plan.fft,
    # which lets cuFFT clobber its input buffer.)
    dns_calcom_from_uc_full(S)

    # No history yet
    S.fnm1[:] = xp.zeros_like(S.om2)


# ---------------------------------------------------------------------------
# FFT helpers (vfft_full_* equivalents)
# ---------------------------------------------------------------------------

def vfft_full_inverse_uc_full_to_ur_full(S: DnsState) -> None:
    UC = S.uc_full
    fft = S.fft

    UC01 = UC[0:2, :, :]

    # norm='forward' skips the default 1/N irfft2 scaling so we get the
    # unnormalized result directly — saves one full pass over ur_full.
    if S.backend == "cpu":
        ur01 = fft.irfft2(
            UC01,
            axes=(1, 2),
            overwrite_x=True,
            workers=S.fft_workers,
            norm='forward',
        )
        S.ur_full[0:2, :, :] = ur01
    elif S.backend == "mlx":
        # norm='forward' returns the unnormalized inverse, with the same
        # convention as the SciPy and cuFFT paths. STEP2B
        # rebuilds all three nonlinear-product planes, while visualization now
        # consumes its scratch field directly, so no third plane is needed
        # between DNS steps.
        S.ur_full = fft.irfft2(
            UC01, s=(S.NZ_full, S.NX_full), axes=(1, 2), norm='forward'
        )
    else:
        plan = S.fft_plan_irfft2_uc01
        if plan is not None:
            # Execute the C2R plan directly into ur_full[0:2]. This bypasses
            # cupyx.scipy.fft.irfft2's internal allocation + copy of the FFT
            # output into our pre-allocated ur_full slice — the dominant cost
            # at large N. Raw cuFFT applies no scaling, matching norm='forward'.
            plan.fft(UC01, S.ur_full[0:2], _cp.cuda.cufft.CUFFT_INVERSE)
        else:
            ur01 = fft.irfft2(UC01, s=(S.NZ_full, S.NX_full), axes=(1, 2), norm='forward')
            S.ur_full[0:2, :, :] = ur01


def vfft_full_forward_ur_full_to_uc_full(S: DnsState) -> None:
    """
    UR_full (3, NZ_full, NX_full) → UC_full (3, NZ_full, NK_full)

    Correct forward:
      1) real FFT along x      (real → complex)
      2) FFT along z           (complex → complex)

    ONLY CHANGE: use rfft2 on (z,x) axes.
    """
    # S.ur_full is already float32
    UR = S.ur_full
    fft = S.fft

    if S.backend == "cpu":
        # overwrite_x is safe here (UR_full is overwritten later by STEP2A anyway)
        UC = fft.rfft2(
            UR,
            axes=(1, 2),
            overwrite_x=True,
            workers=S.fft_workers,
        )
        S.uc_full[...] = UC
    elif S.backend == "mlx":
        # Default norm='backward' applies no forward scaling, matching SciPy.
        S.uc_full[:] = fft.rfft2(UR, s=(S.NZ_full, S.NX_full), axes=(1, 2))
    else:
        plan = S.fft_plan_rfft2_ur_full
        if plan is not None:
            # Execute the R2C plan directly into uc_full. Skips the internal
            # allocation + copy that cupyx.scipy.fft.rfft2 would do.
            plan.fft(UR, S.uc_full, _cp.cuda.cufft.CUFFT_FORWARD)
        else:
            UC = fft.rfft2(UR, s=(S.NZ_full, S.NX_full), axes=(1, 2), overwrite_x=True)
            S.uc_full[...] = UC


# ---------------------------------------------------------------------------
# CALCOM — spectral vorticity from UC_full (dnsCudaCalcom)
# ---------------------------------------------------------------------------

def dns_calcom_from_uc_full(S: DnsState) -> None:
    """
    Python/xp port of dnsCudaCalcom:

      OM2(ix,iz) = i * [ GAMMA(iz)*UC1(ix,iz) - ALFA(ix)*UC2(ix,iz) ]

    Uses:
      S.uc_full : (3, NZ_full, NK_full)  [comp,z,kx]
      S.alfa    : (NX_half,)
      S.gamma   : (NZ,)
    Writes:
      S.om2     : (NZ, NX_half)
    """
    xp = S.xp

    Nbase = int(S.Nbase)
    NX_full = int(S.NX_full)
    NZ_full = int(S.NZ_full)
    NK_full = int(S.NK_full)

    NX_half = Nbase // 2
    NZ = Nbase

    alfa_1d = _astype32(S.alfa, xp.float32)   # (NX_half,)
    gamma_1d = _astype32(S.gamma, xp.float32)  # (NZ,)

    # UC_full layout: [comp, z, kx]
    uc1_full = S.uc_full[0]                   # (NZ_full, NK_full)
    uc2_full = S.uc_full[1]                   # (NZ_full, NK_full)

    # We only use the first NZ rows and NX_half kx-modes
    uc1 = uc1_full[:NZ, :NX_half]             # (NZ, NX_half)
    uc2 = uc2_full[:NZ, :NX_half]             # (NZ, NX_half)

    ax = alfa_1d[None, :]                     # (1, NX_half)
    gz = gamma_1d[:, None]                    # (NZ, 1)

    # diff = GAMMA*UC1 - ALFA*UC2
    diff = gz * uc1 - ax * uc2                # (NZ, NX_half), complex

    # om = i * diff = (-Im(diff), Re(diff))
    diff_r = diff.real
    diff_i = diff.imag

    om_r = -diff_i
    om_i = diff_r

    S.om2[:] = xp.asarray(om_r + 1j * om_i, dtype=xp.complex64)


# ---------------------------------------------------------------------------
# STEP2B — build uiuj and forward FFT (dnsCudaStep2B)
# ---------------------------------------------------------------------------
_STEP2B_MUL3_KERNEL = None  # fallback, created lazily on first GPU call
_STEP2B_BUILD_UIUJ_KERNEL = None  # created lazily on first GPU call
_STEP2B_ZERO_MIDDLE_KERNEL = None  # created lazily on first GPU call
_STEP3_FUSED_KERNEL = None  # created lazily on first GPU call
_LS_IMEX_RK3_STAGE_KERNEL = None  # created lazily on first GPU LS-IMEX-RK3 call
_STEP2A_PREPARE_KERNEL = None  # created lazily on first GPU call
_STEP2A_CROP_KERNEL = None  # created lazily on first GPU call
_STEP3_COPY_CN_KERNEL = None  # created lazily on first GPU call
_NEXT_DT_UPDATE_KERNEL = None  # created lazily on first GPU call
_ENERGY_SPECTRUM_OMEGA_KERNEL = None  # created lazily on first GPU spectrum sample

def dns_step2b(S: DnsState) -> None:
    """
    Python/CuPy port of dnsCudaStep2B(DnsDeviceState *S).

    Mirrors Fortran STEP2B:

      1) Build uiuj in UR(x,z,1..3) on the full 3/2 grid
      2) Full-grid forward FFT: UR_full → UC_full (3 components)
         (VRFFTF + VCFFTF in Fortran)
      3) Zero UC(X,NZ+1,I) for X<=NX/2, I=1..3
    """
    xp = S.xp

    # Geometry on the full 3/2 grid
    N = S.Nbase          # NX = NZ = Nbase (Fortran NX,NZ)
    NX_full = S.NX_full        # 3*N/2
    NZ_full = S.NZ_full        # 3*N/2
    NK_full = S.NK_full        # 3*N/4+1

    UR = S.ur_full
    UC = S.uc_full

    u = UR[0]   # (NZ_full, NX_full)
    w = UR[1]   # (NZ_full, NX_full)

    # Use the same vectorized pair kernel as the native CUDA hot path when the
    # full-row width is even. It halves the number of global-memory transactions
    # versus scalar elementwise code for the common power-of-two DNS sizes.
    if S.backend == "gpu":
        global _STEP2B_BUILD_UIUJ_KERNEL, _STEP2B_MUL3_KERNEL
        if (NX_full % 2) == 0:
            if _STEP2B_BUILD_UIUJ_KERNEL is None:
                _STEP2B_BUILD_UIUJ_KERNEL = _cp.RawKernel(r'''
                extern "C" __global__
                void turbo_step2b_build_uiuj_vec2(float* ur_full,
                                                  int NX_full,
                                                  int NZ_full)
                {
                    int x2 = (int)(blockIdx.x * blockDim.x + threadIdx.x);
                    int z = (int)(blockIdx.y * blockDim.y + threadIdx.y);

                    int NX_pairs = NX_full / 2;
                    if (x2 >= NX_pairs || z >= NZ_full) return;

                    size_t plane = (size_t)NX_full * (size_t)NZ_full;
                    size_t row = (size_t)z * (size_t)NX_full;

                    float2* u_row  = reinterpret_cast<float2*>(ur_full + row);
                    float2* w_row  = reinterpret_cast<float2*>(ur_full + plane + row);
                    float2* uw_row = reinterpret_cast<float2*>(ur_full + 2 * plane + row);

                    float2 u = u_row[x2];
                    float2 w = w_row[x2];

                    float2 uw;
                    uw.x = u.x * w.x;
                    uw.y = u.y * w.y;

                    float2 uu;
                    uu.x = u.x * u.x;
                    uu.y = u.y * u.y;

                    float2 ww;
                    ww.x = w.x * w.x;
                    ww.y = w.y * w.y;

                    uw_row[x2] = uw;
                    u_row[x2] = uu;
                    w_row[x2] = ww;
                }
                ''', "turbo_step2b_build_uiuj_vec2")

            block = (16, 16)
            grid = (((NX_full // 2) + block[0] - 1) // block[0],
                    (NZ_full + block[1] - 1) // block[1])
            _STEP2B_BUILD_UIUJ_KERNEL(
                grid,
                block,
                (UR, _np.int32(NX_full), _np.int32(NZ_full)),
            )
        else:
            if _STEP2B_MUL3_KERNEL is None:
                _STEP2B_MUL3_KERNEL = xp.ElementwiseKernel(
                    "T u, T w",
                    "T uw, T uu, T ww",
                    "uw = u * w; uu = u * u; ww = w * w;",
                    "turbo_step2b_mul3",
                )
            _STEP2B_MUL3_KERNEL(u, w, UR[2], UR[0], UR[1])
    elif S.backend == "mlx":
        # `u`/`w` are already independent copies (MLX slices never alias), so the
        # three products can be built and stacked in one shot. Rebinding beats
        # three scatter-writes into the existing buffer.
        S.ur_full = xp.stack([u * u, w * w, u * w], axis=0)
    else:
        # Use in-place multiplies to avoid temporaries
        xp.multiply(u, w, out=UR[2])  # u * w
        xp.multiply(u, u, out=UR[0])  # u^2
        xp.multiply(w, w, out=UR[1])  # w^2

    vfft_full_forward_ur_full_to_uc_full(S)

    NX_half = N // 2
    NZ = N
    z_mid = NZ

    kx_max = min(NX_half, NK_full)

    if z_mid < NZ_full and kx_max > 0:
        if S.backend == "gpu":
            global _STEP2B_ZERO_MIDDLE_KERNEL
            if _STEP2B_ZERO_MIDDLE_KERNEL is None:
                _STEP2B_ZERO_MIDDLE_KERNEL = _cp.RawKernel(r'''
                extern "C" __global__
                void turbo_step2b_zero_middle(float2* uc_full,
                                              int NX_half,
                                              int z_mid,
                                              int NK_full,
                                              int NZ_full)
                {
                    int kx = (int)(blockIdx.x * blockDim.x + threadIdx.x);
                    if (kx >= NX_half) return;

                    for (int c = 0; c < 3; ++c) {
                        size_t idx = (size_t)kx
                                   + (size_t)NK_full
                                   * ((size_t)z_mid + (size_t)NZ_full * (size_t)c);
                        uc_full[idx].x = 0.0f;
                        uc_full[idx].y = 0.0f;
                    }
                }
                ''', "turbo_step2b_zero_middle")
            threads = 256
            blocks = (kx_max + threads - 1) // threads
            _STEP2B_ZERO_MIDDLE_KERNEL(
                (blocks,),
                (threads,),
                (UC, _np.int32(kx_max), _np.int32(z_mid), _np.int32(NK_full), _np.int32(NZ_full)),
            )
        else:
            # Read through S rather than the local UC: the forward FFT above may
            # have rebound S.uc_full rather than writing into it.
            S.uc_full[0:3, z_mid, 0:kx_max] = _zero_c(xp)


# ---------------------------------------------------------------------------
# STEP3 — vorticity update using om2 & fnm1
# ---------------------------------------------------------------------------

def _compute_nonlinear_vorticity_term(S: DnsState, out) -> None:
    xp = S.xp
    Nbase = int(S.Nbase)
    NX_half = Nbase // 2
    NZ = Nbase

    uc_full = S.uc_full
    uc0_low = uc_full[0, :, :NX_half]
    uc1_low = uc_full[1, :, :NX_half]
    uc2_low = uc_full[2, :, :NX_half]

    uc1_th = S.step3_uc1_th
    uc2_th = S.step3_uc2_th
    uc3_th = S.step3_uc3_th
    if S.backend == "cpu":
        NZ_half = NZ // 2
        uc1_th[:NZ_half] = uc0_low[:NZ_half]
        uc1_th[NZ_half:] = uc0_low[NZ:NZ + NZ_half]
        uc2_th[:NZ_half] = uc1_low[:NZ_half]
        uc2_th[NZ_half:] = uc1_low[NZ:NZ + NZ_half]
        uc3_th[:NZ_half] = uc2_low[:NZ_half]
        uc3_th[NZ_half:] = uc2_low[NZ:NZ + NZ_half]
    else:
        xp.take(uc0_low, S.step3_z_spec, axis=0, out=uc1_th)
        xp.take(uc1_low, S.step3_z_spec, axis=0, out=uc2_th)
        xp.take(uc2_low, S.step3_z_spec, axis=0, out=uc3_th)

    tmp_c = S.scratch2
    xp.subtract(uc1_th, uc2_th, out=out)
    xp.multiply(out, S.step3_GA, out=out)
    xp.multiply(uc3_th, S.step3_G2mA2, out=tmp_c)
    xp.add(out, tmp_c, out=out)
    xp.multiply(out, S.step3_divxz, out=out)


def _compute_nonlinear_vorticity_term_mlx(S: DnsState):
    """
    MLX equivalent of _compute_nonlinear_vorticity_term, returning the term
    instead of writing it into a caller-supplied buffer (MLX has no `out=`).
    """
    xp = S.xp
    NX_half = int(S.Nbase) // 2

    uc_full = S.uc_full
    def band_term(start: int, weights):
        stop = start + NX_half
        fn_band = (
            uc_full[0, start:stop, :NX_half] - uc_full[1, start:stop, :NX_half]
        ) * weights[0]
        fn_band = fn_band + uc_full[2, start:stop, :NX_half] * weights[1]
        return fn_band * S.step3_divxz

    positive = band_term(0, (S.step3_GA[:NX_half], S.step3_G2mA2[:NX_half]))
    negative = band_term(
        S.Nbase, (S.step3_GA[NX_half:], S.step3_G2mA2[NX_half:])
    )
    return xp.concatenate((positive, negative), axis=0)


_MLX_RECONSTRUCT_VELOCITY = None


def _reconstruct_velocity_from_om2_mlx(S: DnsState) -> None:
    """
    MLX equivalent of _reconstruct_velocity_from_om2.

    The SciPy/CuPy version writes into the kx >= 1 sub-slice in place and then
    patches column 0. MLX slices are copies, so the full width is built in one
    expression and column 0 is overwritten afterwards. alfa[0] == 0 already
    zeroes out2's first column, but it is set explicitly to mirror the original.
    """
    xp = S.xp
    NZ = int(S.Nbase)
    NX_half = NZ // 2

    global _MLX_RECONSTRUCT_VELOCITY
    if _MLX_RECONSTRUCT_VELOCITY is None:
        def reconstruct(om2, inv_k2, gamma, alfa, inv_gamma0):
            base = om2 * inv_k2
            out1 = (base * gamma[:, None]) * xp.array(-1.0j, dtype=xp.complex64)
            out2 = (base * alfa[None, :]) * xp.array(1.0j, dtype=xp.complex64)
            out1[:, 0] = (xp.array(-1.0j, dtype=xp.complex64) * om2[:, 0]) * inv_gamma0
            out2[:, 0] = _zero_c(xp)
            return out1, out2
        _MLX_RECONSTRUCT_VELOCITY = xp.compile(reconstruct)

    out1, out2 = _MLX_RECONSTRUCT_VELOCITY(
        S.om2, S.step3_invK2_full, S.gamma, S.alfa, S.step3_inv_gamma0
    )

    S.uc_full[0, :NZ, :NX_half] = out1
    S.uc_full[1, :NZ, :NX_half] = out2


def _reconstruct_velocity_from_om2(S: DnsState) -> None:
    xp = S.xp
    Nbase = int(S.Nbase)
    NX_half = Nbase // 2
    NZ = Nbase

    om2 = S.om2
    out1 = S.scratch1
    out2 = S.scratch2

    if NX_half > 1:
        invK2_sub = S.step3_invK2_sub
        om2_sub = om2[:, 1:]
        out1_sub = out1[:, 1:]
        out2_sub = out2[:, 1:]

        out1_sub[...] = om2_sub
        xp.multiply(out1_sub, invK2_sub, out=out1_sub)
        xp.multiply(out1_sub, S.gamma[:, None], out=out1_sub)
        xp.multiply(out1_sub, xp.complex64(-1.0j), out=out1_sub)

        out2_sub[...] = om2_sub
        xp.multiply(out2_sub, invK2_sub, out=out2_sub)
        xp.multiply(out2_sub, S.alfa[1:][None, :], out=out2_sub)
        xp.multiply(out2_sub, xp.complex64(1.0j), out=out2_sub)

    out1[:, 0] = xp.complex64(-1.0j) * om2[:, 0] * S.step3_inv_gamma0
    out2[:, 0] = xp.complex64(0.0 + 0.0j)

    S.uc_full[0, :NZ, :NX_half] = out1
    S.uc_full[1, :NZ, :NX_half] = out2


def _dns_step3_mlx(S: DnsState) -> None:
    """
    CNAB2 vorticity update for MLX.

    Same formula as the SciPy path, written functionally: MLX fuses the chain
    into its own kernels, so the scratch-buffer reuse the other backends need
    would only get in the way.
    """
    # Match the CPU's float32 rounding at each scalar operation, then pass
    # the rounded values to MLX as Python scalars.
    visc = _np.float32(S.visc)
    dt = _np.float32(S.dt)
    cnm1 = _np.float32(S.cnm1)
    half = _np.float32(0.5)

    K2 = S.step3_K2
    om2 = S.om2

    fn = _compute_nonlinear_vorticity_term_mlx(S)

    ARG = K2 * (half * visc * dt).item()
    DEN = ARG + 1.0

    c2 = (half * dt * (_np.float32(2.0) + cnm1)).item()
    c3 = (-half * dt * cnm1).item()

    num = om2 - om2 * ARG
    num = num + fn * c2
    num = num + S.fnm1 * c3

    S.om2 = _div_by_real(S.xp, num, DEN)
    S.fnm1 = fn

    _reconstruct_velocity_from_om2_mlx(S)
    S.cnm1 = float(S.cn)


def dns_step3(S: DnsState, fuse: bool = True) -> None:
    xp = S.xp
    global _STEP3_FUSED_KERNEL

    if S.backend == "mlx":
        _dns_step3_mlx(S)
        return
    # Fast GPU path: mirror the native CUDA kernel by updating OM2/FNM1 and
    # reconstructing UC_full[0:2] from the new OM2 while it is still in a register.
    if S.backend == "gpu" and _cp is not None and fuse:

        # Compile once per process
        if _STEP3_FUSED_KERNEL is None:
            _STEP3_FUSED_KERNEL = _cp.RawKernel(r'''
            extern "C" __global__
            void turbo_step3_fused(float2* om2,
                                   float2* fnm1,
                                   float2* uc_full,
                                   const float* time_scalars,
                                   int NX_half,
                                   int NZ,
                                   int NZ_full,
                                   int NK_full,
                                   float divxz,
                                   float visc
            ) {
                int k = (int)(blockIdx.x * blockDim.x + threadIdx.x);
                int z = (int)(blockIdx.y * blockDim.y + threadIdx.y);
                if (k >= NX_half || z >= NZ) return;

                int idx = z * NX_half + k;
                float2 om_old = om2[idx];
                float2 fn_old = fnm1[idx];

                float ax = (float)k;
                float gz = (z < (NZ / 2)) ? (float)z : (float)(z - NZ);
                float A2 = ax * ax;
                float G2 = gz * gz;
                float K2 = A2 + G2;
                float GA = gz * ax;
                float G2_minus_A2 = G2 - A2;

                int z_spec = ((z + 1) <= (NZ / 2)) ? z : (z + NZ / 2);

                size_t idx_uc1_in = (size_t)k + (size_t)NK_full
                                   * ((size_t)z_spec + (size_t)NZ_full * 0u);
                size_t idx_uc2_in = (size_t)k + (size_t)NK_full
                                   * ((size_t)z_spec + (size_t)NZ_full * 1u);
                size_t idx_uc3_in = (size_t)k + (size_t)NK_full
                                   * ((size_t)z_spec + (size_t)NZ_full * 2u);

                float2 uc1 = uc_full[idx_uc1_in];
                float2 uc2 = uc_full[idx_uc2_in];
                float2 uc3 = uc_full[idx_uc3_in];

                float fnx = (GA * (uc1.x - uc2.x) + G2_minus_A2 * uc3.x) * divxz;
                float fny = (GA * (uc1.y - uc2.y) + G2_minus_A2 * uc3.y) * divxz;

                float dt = time_scalars[0];
                float cnm1 = time_scalars[2];
                float arg = K2 * (0.5f * visc * dt);
                float den = 1.0f + arg;
                float c1 = 1.0f - arg;
                float c2 = 0.5f * dt * (2.0f + cnm1);
                float c3 = -0.5f * dt * cnm1;

                float2 om_new;
                om_new.x = (c1 * om_old.x + c2 * fnx + c3 * fn_old.x) / den;
                om_new.y = (c1 * om_old.y + c2 * fny + c3 * fn_old.y) / den;

                om2[idx] = om_new;
                fnm1[idx].x = fnx;
                fnm1[idx].y = fny;

                float2 out1;
                float2 out2;
                out1.x = 0.0f;
                out1.y = 0.0f;
                out2.x = 0.0f;
                out2.y = 0.0f;

                if (k >= 1) {
                    float invK2 = 1.0f / (K2 + 1.0e-30f);
                    float vx = om_new.x * invK2;
                    float vy = om_new.y * invK2;

                    out1.x = gz * vy;
                    out1.y = -gz * vx;
                    out2.x = -ax * vy;
                    out2.y = ax * vx;
                } else {
                    float invG = ((z + 1) >= 2 && (gz > 0.0f || gz < 0.0f)) ? (1.0f / gz) : 0.0f;
                    out1.x = om_new.y * invG;
                    out1.y = -om_new.x * invG;
                }

                size_t idx_uc1_out = (size_t)k + (size_t)NK_full
                                    * ((size_t)z + (size_t)NZ_full * 0u);
                size_t idx_uc2_out = (size_t)k + (size_t)NK_full
                                    * ((size_t)z + (size_t)NZ_full * 1u);
                uc_full[idx_uc1_out] = out1;
                uc_full[idx_uc2_out] = out2;
            }
            ''', "turbo_step3_fused")

        # Geometry and constants
        Nbase = int(S.Nbase)
        NX_half = Nbase // 2
        NZ = Nbase

        NK_full = int(S.NK_full)
        NZ_full = int(S.NZ_full)

        block = (64, 4)
        grid = ((NX_half + block[0] - 1) // block[0],
                (NZ + block[1] - 1) // block[1])

        # IMPORTANT: RawKernel scalar args must match the C signature types.
        # On 64-bit Python, passing plain Python ints/floats will typically be int64/float64,
        # which corrupts the kernel argument packing (and silently breaks the physics).
        NK_full_i32 = _np.int32(NK_full)
        NX_half_i32 = _np.int32(NX_half)
        NZ_i32 = _np.int32(NZ)
        NZ_full_i32 = _np.int32(NZ_full)
        divxz_f32 = _np.float32(S.step3_divxz)
        visc_f32 = _np.float32(S.visc)

        _STEP3_FUSED_KERNEL(
            grid,
            block,
            (
                S.om2,
                S.fnm1,
                S.uc_full,
                S.time_scalars,
                NX_half_i32,
                NZ_i32,
                NZ_full_i32,
                NK_full_i32,
                divxz_f32,
                visc_f32,
            ),
        )

        _copy_cn_to_cnm1_device(S)
        return

    visc = xp.float32(S.visc)
    if S.backend == "gpu" and S.time_scalars is not None:
        dt = S.time_scalars[0]
        cnm1 = S.time_scalars[2]
    else:
        dt = xp.float32(S.dt)
        cnm1 = xp.float32(S.cnm1)

    K2 = S.step3_K2

    tmp_FN = S.scratch1
    tmp_c = S.scratch2
    _compute_nonlinear_vorticity_term(S, tmp_FN)

    VT = xp.float32(0.5) * visc * dt
    ARG = S.step3_ARG
    DEN = S.step3_DEN
    xp.multiply(K2, VT, out=ARG)
    xp.add(ARG, xp.float32(1.0), out=DEN)

    c2 = xp.float32(0.5) * dt * (xp.float32(2.0) + cnm1)
    c3 = -xp.float32(0.5) * dt * cnm1

    NUM = S.step3_NUM
    xp.multiply(S.om2, ARG, out=tmp_c)
    xp.subtract(S.om2, tmp_c, out=NUM)

    xp.multiply(tmp_FN, c2, out=tmp_c)
    xp.add(NUM, tmp_c, out=NUM)
    xp.multiply(S.fnm1, c3, out=tmp_c)
    xp.add(NUM, tmp_c, out=NUM)

    xp.divide(NUM, DEN, out=S.om2)

    S.fnm1[...] = tmp_FN
    _reconstruct_velocity_from_om2(S)

    if S.backend == "gpu" and S.time_scalars is not None:
        _copy_cn_to_cnm1_device(S)
    else:
        S.cnm1 = float(S.cn)


def _dns_step_ls_imex_rk3_stage_gpu(S: DnsState, alpha: _np.float32, beta: _np.float32) -> None:
    global _LS_IMEX_RK3_STAGE_KERNEL

    if _LS_IMEX_RK3_STAGE_KERNEL is None:
        _LS_IMEX_RK3_STAGE_KERNEL = _cp.RawKernel(r'''
        extern "C" __global__
        void turbo_ls_imex_rk3_stage(float2* om2,
                                     float2* fnm1,
                                     float2* uc_full,
                                     const float* time_scalars,
                                     int NX_half,
                                     int NZ,
                                     int NZ_full,
                                     int NK_full,
                                     float divxz,
                                     float visc,
                                     float alpha,
                                     float beta)
        {
            int k = (int)(blockIdx.x * blockDim.x + threadIdx.x);
            int z = (int)(blockIdx.y * blockDim.y + threadIdx.y);
            if (k >= NX_half || z >= NZ) return;

            int idx = z * NX_half + k;
            float2 om_old = om2[idx];
            float2 fn_old = fnm1[idx];

            float ax = (float)k;
            float gz = (z < (NZ / 2)) ? (float)z : (float)(z - NZ);
            float A2 = ax * ax;
            float G2 = gz * gz;
            float K2 = A2 + G2;
            float GA = gz * ax;
            float G2_minus_A2 = G2 - A2;

            int z_spec = ((z + 1) <= (NZ / 2)) ? z : (z + NZ / 2);

            size_t idx_uc1_in = (size_t)k + (size_t)NK_full
                               * ((size_t)z_spec + (size_t)NZ_full * 0u);
            size_t idx_uc2_in = (size_t)k + (size_t)NK_full
                               * ((size_t)z_spec + (size_t)NZ_full * 1u);
            size_t idx_uc3_in = (size_t)k + (size_t)NK_full
                               * ((size_t)z_spec + (size_t)NZ_full * 2u);

            float2 uc1 = uc_full[idx_uc1_in];
            float2 uc2 = uc_full[idx_uc2_in];
            float2 uc3 = uc_full[idx_uc3_in];

            float2 fn;
            fn.x = (GA * (uc1.x - uc2.x) + G2_minus_A2 * uc3.x) * divxz;
            fn.y = (GA * (uc1.y - uc2.y) + G2_minus_A2 * uc3.y) * divxz;

            float dt = time_scalars[0];
            float rhs_om = 1.0f - K2 * beta * dt * visc;
            float rhs_fn = alpha * dt;
            float rhs_old_fn = beta * dt;
            float den = 1.0f + K2 * alpha * dt * visc;

            float2 om_new;
            om_new.x = (rhs_om * om_old.x + rhs_fn * fn.x + rhs_old_fn * fn_old.x) / den;
            om_new.y = (rhs_om * om_old.y + rhs_fn * fn.y + rhs_old_fn * fn_old.y) / den;

            om2[idx] = om_new;
            fnm1[idx] = fn;

            float2 out1;
            float2 out2;
            out1.x = 0.0f;
            out1.y = 0.0f;
            out2.x = 0.0f;
            out2.y = 0.0f;

            if (k >= 1) {
                float invK2 = 1.0f / (K2 + 1.0e-30f);
                float vx = om_new.x * invK2;
                float vy = om_new.y * invK2;

                out1.x = gz * vy;
                out1.y = -gz * vx;
                out2.x = -ax * vy;
                out2.y = ax * vx;
            } else {
                float invG = ((z + 1) >= 2 && (gz > 0.0f || gz < 0.0f)) ? (1.0f / gz) : 0.0f;
                out1.x = om_new.y * invG;
                out1.y = -om_new.x * invG;
            }

            size_t idx_uc1_out = (size_t)k + (size_t)NK_full
                                * ((size_t)z + (size_t)NZ_full * 0u);
            size_t idx_uc2_out = (size_t)k + (size_t)NK_full
                                * ((size_t)z + (size_t)NZ_full * 1u);
            uc_full[idx_uc1_out] = out1;
            uc_full[idx_uc2_out] = out2;
        }
        ''', "turbo_ls_imex_rk3_stage")

    Nbase = int(S.Nbase)
    NX_half = Nbase // 2
    NZ = Nbase
    NK_full = int(S.NK_full)
    NZ_full = int(S.NZ_full)

    block = (64, 4)
    grid = ((NX_half + block[0] - 1) // block[0],
            (NZ + block[1] - 1) // block[1])

    _LS_IMEX_RK3_STAGE_KERNEL(
        grid,
        block,
        (
            S.om2,
            S.fnm1,
            S.uc_full,
            S.time_scalars,
            _np.int32(NX_half),
            _np.int32(NZ),
            _np.int32(NZ_full),
            _np.int32(NK_full),
            _np.float32(S.step3_divxz),
            _np.float32(S.visc),
            _np.float32(alpha),
            _np.float32(beta),
        ),
    )


def dns_step_ls_imex_rk3(S: DnsState) -> None:
    xp = S.xp
    alpha_vals = (_np.float32(8.0 / 15.0), _np.float32(5.0 / 12.0), _np.float32(3.0 / 4.0))
    beta_vals = (_np.float32(0.0), _np.float32(-17.0 / 60.0), _np.float32(-5.0 / 12.0))

    if S.backend == "gpu" and _cp is not None and S.time_scalars is not None:
        for stage in range(3):
            dns_step2b(S)
            _dns_step_ls_imex_rk3_stage_gpu(S, alpha_vals[stage], beta_vals[stage])
            dns_step2a(S)
        return

    if S.backend == "mlx":
        dt_f = _np.float32(S.dt)
        visc_f = _np.float32(S.visc)
        K2 = S.step3_K2
        for stage in range(3):
            a = alpha_vals[stage]
            b = beta_vals[stage]
            dns_step2b(S)
            fn = _compute_nonlinear_vorticity_term_mlx(S)

            rhs = 1.0 - K2 * (b * dt_f * visc_f).item()
            num = S.om2 * rhs
            num = num + fn * (a * dt_f).item()
            num = num + S.fnm1 * (b * dt_f).item()
            den = K2 * (a * dt_f * visc_f).item() + 1.0

            S.om2 = _div_by_real(xp, num, den)
            S.fnm1 = fn

            _reconstruct_velocity_from_om2_mlx(S)
            dns_step2a(S, eval_mlx_state=(stage == 2))
        return

    dt = xp.float32(S.dt)
    visc = xp.float32(S.visc)
    K2 = S.step3_K2
    fn = S.scratch1
    tmp = S.scratch2
    den = S.step3_DEN
    num = S.step3_NUM

    alpha = tuple(xp.float32(v) for v in alpha_vals)
    beta = tuple(xp.float32(v) for v in beta_vals)

    for stage in range(3):
        dns_step2b(S)
        _compute_nonlinear_vorticity_term(S, fn)

        xp.multiply(K2, beta[stage] * dt * visc, out=tmp)
        xp.subtract(xp.float32(1.0), tmp, out=tmp)
        xp.multiply(S.om2, tmp, out=num)

        xp.multiply(fn, alpha[stage] * dt, out=tmp)
        xp.add(num, tmp, out=num)
        xp.multiply(S.fnm1, beta[stage] * dt, out=tmp)
        xp.add(num, tmp, out=num)

        xp.multiply(K2, alpha[stage] * dt * visc, out=den)
        xp.add(den, xp.float32(1.0), out=den)
        xp.divide(num, den, out=S.om2)

        S.fnm1[...] = fn

        _reconstruct_velocity_from_om2(S)
        dns_step2a(S)


# ===============================================================
# STEP2A core (dealias + reshuffle + inverse FFT)
# ===============================================================
def dns_step2a(S: DnsState, eval_mlx_state: bool = True) -> None:
    xp = S.xp
    N = S.Nbase
    NX = S.NX
    NZ = S.NZ
    NX_full = S.NX_full
    NZ_full = S.NZ_full
    NK_full = S.NK_full

    UC = S.uc_full

    if S.backend == "gpu" and _cp is not None:
        global _STEP2A_PREPARE_KERNEL
        if _STEP2A_PREPARE_KERNEL is None:
            _STEP2A_PREPARE_KERNEL = _cp.RawKernel(r'''
            extern "C" __global__
            void turbo_step2a_prepare(float2* uc_full,
                                      int Nbase,
                                      int NZ_full,
                                      int NK_full)
            {
                int tx = (int)(blockIdx.x * blockDim.x + threadIdx.x);
                int tz = (int)(blockIdx.y * blockDim.y + threadIdx.y);
                int nx_start = Nbase / 2;
                int nx_end = 3 * Nbase / 4;
                int nx_len = nx_end - nx_start + 1;

                if (tz < NZ_full && tx < nx_len) {
                    int kx = nx_start + tx;
                    if (kx < NK_full) {
                        for (int c = 0; c < 2; ++c) {
                            size_t idx = (size_t)kx
                                       + (size_t)NK_full
                                       * ((size_t)tz + (size_t)NZ_full * (size_t)c);
                            uc_full[idx].x = 0.0f;
                            uc_full[idx].y = 0.0f;
                        }
                    }
                }

                if (tx < Nbase / 2 && tz < Nbase / 2 && tx < NK_full) {
                    int z_mid = tz + Nbase / 2;
                    int z_top = tz + Nbase;
                    if (z_top < NZ_full) {
                        for (int c = 0; c < 2; ++c) {
                            size_t idx_mid = (size_t)tx
                                           + (size_t)NK_full
                                           * ((size_t)z_mid + (size_t)NZ_full * (size_t)c);
                            size_t idx_top = (size_t)tx
                                           + (size_t)NK_full
                                           * ((size_t)z_top + (size_t)NZ_full * (size_t)c);
                            float2 v = uc_full[idx_mid];
                            uc_full[idx_top] = v;
                            uc_full[idx_mid].x = 0.0f;
                            uc_full[idx_mid].y = 0.0f;
                        }
                    }
                }
            }
            ''', "turbo_step2a_prepare")

        block = (32, 8)
        nx_len = 3 * N // 4 - N // 2 + 1
        work_x = max(nx_len, N // 2)
        grid = ((work_x + block[0] - 1) // block[0],
                (NZ_full + block[1] - 1) // block[1])
        _STEP2A_PREPARE_KERNEL(
            grid,
            block,
            (UC, _np.int32(N), _np.int32(NZ_full), _np.int32(NK_full)),
        )
    else:
        zc = _zero_c(xp)
        hi_start = N // 2
        hi_end = min(3 * N // 4, NK_full - 1)
        if hi_start <= hi_end:
            UC[0:2, :, hi_start:hi_end + 1] = zc

        halfN = N // 2
        k_max = min(halfN, NK_full)
        if k_max > 0:
            z_mid_start = halfN
            z_mid_end = N
            z_top_start = N
            z_top_end = N + halfN
            # Source and destination z-bands do not overlap, so the read-then-write
            # is safe whether the RHS is a NumPy view or an MLX copy.
            UC[0:2, z_top_start:z_top_end, :k_max] = UC[0:2, z_mid_start:z_mid_end, :k_max]
            UC[0:2, z_mid_start:z_mid_end, :k_max] = zc

    # Inverse FFT UC_full → UR_full
    vfft_full_inverse_uc_full_to_ur_full(S)

    if not S.populate_compact_ur:
        # STEP2A closes out every step (and every RK3 stage), so this is the
        # natural point to realize MLX's lazy graph. Without it the graph would
        # grow without bound and the step timings would be meaningless.
        if eval_mlx_state:
            S.eval_state()
        return

    off_x = (NX_full - NX) // 2
    off_z = (NZ_full - NZ) // 2

    if S.backend == "gpu" and _cp is not None:
        global _STEP2A_CROP_KERNEL
        if _STEP2A_CROP_KERNEL is None:
            crop_src = r'''
            extern "C" __global__
            void turbo_step2a_crop(
                const float* __restrict__ ur0,
                const float* __restrict__ ur1,
                float* __restrict__ ur,
                const int NX,
                const int NZ,
                const int NX_full,
                const int off_x,
                const int off_z
            ){
                int tid = (int)(blockIdx.x * blockDim.x + threadIdx.x);
                int n = NZ * NX;
                if (tid >= n) return;

                int z = tid / NX;
                int x = tid - z * NX;

                int src = (z + off_z) * NX_full + (x + off_x);
                float u0 = ur0[src];
                float u1 = ur1[src];

                int dst = (tid * 3);
                ur[dst + 0] = u0;
                ur[dst + 1] = u1;
                ur[dst + 2] = 0.0f;
            }
            '''
            _STEP2A_CROP_KERNEL = _cp.RawKernel(crop_src, "turbo_step2a_crop")

        threads = 256
        n = int(NZ) * int(NX)
        blocks = (n + threads - 1) // threads

        _STEP2A_CROP_KERNEL(
            (blocks,),
            (threads,),
            (
                S.ur_full[0],
                S.ur_full[1],
                S.ur,
                _np.int32(NX),
                _np.int32(NZ),
                _np.int32(NX_full),
                _np.int32(off_x),
                _np.int32(off_z),
            ),
        )
    else:
        S.ur[:, :, 0] = S.ur_full[0, off_z:off_z + N, off_x:off_x + N]
        S.ur[:, :, 1] = S.ur_full[1, off_z:off_z + N, off_x:off_x + N]
        S.ur[:, :, 2] = 0.0

    S.eval_state()

# ---------------------------------------------------------------------------
# NEXTDT — CFL based timestep
# ---------------------------------------------------------------------------

def compute_cflm(S: DnsState):
    xp = S.xp
    NX3D2 = S.NX_full
    NZ3D2 = S.NZ_full

    u = S.ur_full[0, :NZ3D2, :NX3D2]
    w = S.ur_full[1, :NZ3D2, :NX3D2]

    if S.backend == "gpu" and _cflm_max_abs_sum is not None:
        CFLM = _cflm_max_abs_sum(u, w, xp.float32(S.inv_dx))  # GPU scalar (already scaled)
        return CFLM

    if S.backend == "mlx":
        # Scale in float32 before returning the host scalar, as on the CPU.
        peak = xp.max(xp.abs(u) + xp.abs(w))
        return float((peak * _f32(xp, S.inv_dx)).item())

    # CPU (or fallback): keep current code path
    tmp = S.cfl_tmp[:NZ3D2, :NX3D2]
    absw = S.cfl_absw[:NZ3D2, :NX3D2]
    xp.abs(u, out=tmp)
    xp.abs(w, out=absw)
    xp.add(tmp, absw, out=tmp)
    CFLM = xp.max(tmp) * S.inv_dx
    return float(CFLM) if S.backend == "cpu" else CFLM

def next_dt(S: DnsState, sync_host: bool = False) -> None:
    global _NEXT_DT_UPDATE_KERNEL
    PI = math.pi
    CFLM = compute_cflm(S)

    if S.backend == "gpu":
        if S.time_scalars is None:
            CFLM = float(CFLM)
        else:
            if _NEXT_DT_UPDATE_KERNEL is None:
                _NEXT_DT_UPDATE_KERNEL = _cp.RawKernel(r'''
                extern "C" __global__
                void turbo_next_dt_update(const float* cflm, float* time_scalars, float cflnum) {
                    float CFLM = cflm[0];
                    float dt = time_scalars[0];
                    if (CFLM <= 0.0f || dt <= 0.0f) return;

                    float CFL = CFLM * dt * 3.14159265358979323846f;
                    float cn = 0.8f + 0.2f * (cflnum / CFL);
                    time_scalars[1] = cn;
                    time_scalars[0] = dt * cn;
                }
                ''', "turbo_next_dt_update")
            _NEXT_DT_UPDATE_KERNEL(
                (1,),
                (1,),
                (CFLM, S.time_scalars, _np.float32(S.cflnum)),
            )
            S.cnm1_needs_update = True
            if sync_host:
                sync_time_scalars_from_device(S)
                return float(CFLM)
            return

    if CFLM <= 0.0 or S.dt <= 0.0:
        return CFLM

    CFL = CFLM * S.dt * PI
    S.cn = 0.8 + 0.2 * (S.cflnum / CFL)
    S.dt = S.dt * S.cn
    return CFLM


# ===============================================================
# Python equivalent of dnsCudaDumpFieldAsPGMFull
# ===============================================================
def dump_field_as_pgm_full(S: DnsState, comp: int, filename: str) -> None:
    NX_full = S.NX_full
    NZ_full = S.NZ_full

    if S.backend == "gpu":
        ur_full_host = _np.asarray(S.ur_full.get(), dtype=_np.float32)
    else:
        ur_full_host = _np.asarray(S.ur_full, dtype=_np.float32)

    field = ur_full_host[comp, :, :]

    minv = float(field.min())
    maxv = float(field.max())

    try:
        f = open(filename, "wb")
    except OSError as e:
        print(f"[DUMP] fopen failed for {filename!r}: {e}")
        return

    header = f"P5\n{NX_full} {NZ_full}\n255\n"
    f.write(header.encode("ascii"))

    rng = maxv - minv

    if abs(rng) <= 1.0e-12:
        c = bytes([128])
        row = c * NX_full
        for _ in range(NZ_full):
            f.write(row)
    else:
        for j in range(NZ_full):
            for i in range(NX_full):
                val = float(field[j, i])
                norm = (val - minv) / rng
                pixf = 1.0 + norm * 254.0
                pix = int(pixf + 0.5)
                if pix < 1:
                    pix = 1
                if pix > 255:
                    pix = 255
                f.write(bytes([pix]))

    f.close()
    print(f"[DUMP] Wrote {filename} (PGM, {NX_full}x{NZ_full}, "
          f"comp={comp}, min={minv:g}, max={maxv:g})")


# ---------------------------------------------------------------------------
# Energy-spectrum time average
# ---------------------------------------------------------------------------

def _compute_energy_spectrum_omega_bins_gpu(S: DnsState, nbins: int, r_max: float, k_nyq: float) -> SpectrumSample:
    global _ENERGY_SPECTRUM_OMEGA_KERNEL

    if _cp is None:
        raise RuntimeError("CuPy backend requested but CuPy is unavailable")

    if _ENERGY_SPECTRUM_OMEGA_KERNEL is None:
        _ENERGY_SPECTRUM_OMEGA_KERNEL = _cp.RawKernel(r'''
        extern "C" __global__
        void turbo_energy_spectrum_omega_bins(const float2* om2,
                                              unsigned long long plane,
                                              int nx_half,
                                              int nz,
                                              int nbins,
                                              double r_max,
                                              double k_nyq,
                                              double fft_scale2,
                                              double* esum,
                                              int* cnt)
        {
            unsigned long long stride =
                (unsigned long long)blockDim.x * (unsigned long long)gridDim.x;
            unsigned long long idx =
                (unsigned long long)blockIdx.x * (unsigned long long)blockDim.x
                + (unsigned long long)threadIdx.x;

            while (idx < plane) {
                int kx = (int)(idx % (unsigned long long)nx_half);
                int z = (int)(idx / (unsigned long long)nx_half);
                int kz = (z < nz / 2) ? z : z - nz;

                double k2 = (double)kx * (double)kx + (double)kz * (double)kz;
                if (k2 > 0.0) {
                    double r = sqrt(k2) / k_nyq;
                    int bin = (int)floor((r / r_max) * (double)nbins);
                    if (bin < 0) bin = 0;
                    if (bin >= nbins) bin = nbins - 1;

                    float2 om = om2[idx];
                    double omega2 = (double)om.x * (double)om.x + (double)om.y * (double)om.y;
                    double p = (omega2 / k2) * fft_scale2;

                    int weight = (kx > 0) ? 2 : 1;
                    if (weight == 2) {
                        p *= 2.0;
                    }

                    atomicAdd(&esum[bin], p);
                    atomicAdd(&cnt[bin], weight);
                }

                idx += stride;
            }
        }
        ''', "turbo_energy_spectrum_omega_bins")

    nx_half = int(S.Nbase) // 2
    nz = int(S.Nbase)
    plane = int(nx_half * nz)

    d_esum = _cp.zeros((nbins,), dtype=_cp.float64)
    d_cnt = _cp.zeros((nbins,), dtype=_cp.int32)

    threads = 256
    blocks = min(65535, max(1, (plane + threads - 1) // threads))
    fft_scale = float(int(S.NX_full) * int(S.NZ_full))
    _ENERGY_SPECTRUM_OMEGA_KERNEL(
        (blocks,),
        (threads,),
        (
            S.om2,
            _np.uint64(plane),
            _np.int32(nx_half),
            _np.int32(nz),
            _np.int32(nbins),
            _np.float64(r_max),
            _np.float64(k_nyq),
            _np.float64(fft_scale * fft_scale),
            d_esum,
            d_cnt,
        ),
    )

    return SpectrumSample(
        nbins=nbins,
        r_max=r_max,
        k_nyq=k_nyq,
        energy=_cp.asnumpy(d_esum).astype(_np.float64, copy=False),
        count=_cp.asnumpy(d_cnt).astype(_np.int32, copy=False),
    )


def _spectrum_bin_geometry_cpu(S: DnsState, nbins: int, r_max: float, k_nyq: float):
    """
    Shell-binning geometry for the (NZ, NX_half) half-plane: the bin index of
    every mode, the constant weight each mode contributes with, and the per-bin
    mode counts. All of it is fixed for a given grid, so it is built once and
    cached on the state — the GUI samples the spectrum on every rendered frame.
    """
    key = (int(S.Nbase), int(S.NX_full), int(S.NZ_full), nbins, r_max, k_nyq)
    cached = getattr(S, "_spectrum_bin_geometry", None)
    if cached is not None and cached[0] == key:
        return cached[1], cached[2], cached[3]

    nz = int(S.Nbase)
    nx_half = int(S.Nbase) // 2
    fft_scale2 = float(int(S.NX_full) * int(S.NZ_full)) ** 2

    kx = _np.arange(nx_half, dtype=_np.float64)
    weight = _np.ones((nx_half,), dtype=_np.float64)
    if nx_half > 1:
        weight[1:] = 2.0

    # Signed z wavenumbers of the full FFT axis: 0, 1, … nz/2-1, -nz/2, … -1
    kz = _np.arange(nz, dtype=_np.float64)
    kz = _np.where(kz < nz // 2, kz, kz - nz)

    k2 = (kx * kx)[None, :] + (kz * kz)[:, None]
    keep = k2 > 0.0                                  # drops the k=0 mode only
    w = _np.where(keep, weight[None, :], 0.0)

    bins = _np.floor((_np.sqrt(k2) / k_nyq / r_max) * float(nbins)).astype(_np.int64)
    _np.clip(bins, 0, nbins - 1, out=bins)
    bins_flat = bins.ravel()

    # |ω̂|² is all that changes between samples; fold everything else into one
    # per-mode factor: weight / k² scaled to the full physical-grid FFT.
    factor = (w / _np.where(keep, k2, 1.0) * fft_scale2).ravel()
    count = _np.bincount(bins_flat, weights=w.ravel(), minlength=nbins).astype(_np.int32)

    S._spectrum_bin_geometry = (key, bins_flat, factor, count)
    return bins_flat, factor, count


def _compute_energy_spectrum_omega_bins_cpu(S: DnsState, nbins: int, r_max: float, k_nyq: float) -> SpectrumSample:
    bins_flat, factor, count = _spectrum_bin_geometry_cpu(S, nbins, r_max, k_nyq)

    om2 = _np.asarray(S.om2)
    if S.backend == "mlx":
        k2 = _np.asarray(S.step3_K2)
        nx_half = int(k2.shape[1])
        rfft_weight = getattr(S, "_eddy_rfft_w_np", None)
        if rfft_weight is None or rfft_weight.shape[0] != nx_half:
            rfft_weight = _np.full(nx_half, _np.float32(2.0), dtype=_np.float32)
            rfft_weight[0] = _np.float32(1.0)
            if nx_half > 1:
                rfft_weight[-1] = _np.float32(1.0)
            S._eddy_rfft_w_np = rfft_weight
        inv_k = getattr(S, "_eddy_inv_k_np", None)
        if inv_k is None or inv_k.shape != k2.shape:
            nonzero = k2 > _np.float32(0.0)
            inv_k = _np.where(
                nonzero,
                _np.float32(1.0) / _np.sqrt(_np.where(nonzero, k2, _np.float32(1.0))),
                _np.float32(0.0),
            )
            S._eddy_inv_k_np = inv_k
        esum, U, L, tau_l = _spectrum_and_flow_metrics_mlx(
            om2.ravel(), bins_flat, factor, nbins, k2.ravel(), rfft_weight, inv_k.ravel()
        )
        S._flow_eddy_metrics_cache = (int(S.it), (U, L, tau_l))
    else:
        esum = _spectrum_bincount(om2.ravel(), bins_flat, factor, nbins)

    return SpectrumSample(
        nbins=nbins,
        r_max=r_max,
        k_nyq=k_nyq,
        energy=esum,
        count=count.copy(),  # callers own their sample; the cache stays clean
    )


def compute_energy_spectrum_uv_bins(S: DnsState) -> SpectrumSample:
    """
    Shell-sum the kinetic energy spectrum from the compact vorticity spectrum.

    For 2D incompressible flow,
    |u_hat|^2 + |v_hat|^2 = |omega_hat|^2 / |k|^2.  S.om2 is the authoritative
    compact spectral state after STEP3; S.uc_full[0:2] is FFT workspace and the
    GPU inverse path may clobber it while producing S.ur_full.

    The saved spectrum is scaled to match a forward FFT of the full physical
    velocity grid, whose coefficients are NX_full*NZ_full larger than the
    stored normalized spectral coefficients.
    """
    if S is None:
        raise ValueError("DnsState is required")

    n = int(S.Nbase)
    nbins = max(32, 2 * n)
    r_max = math.sqrt(2.0)
    k_nyq = 0.5 * float(n)

    if S.backend == "gpu":
        return _compute_energy_spectrum_omega_bins_gpu(S, nbins, r_max, k_nyq)
    return _compute_energy_spectrum_omega_bins_cpu(S, nbins, r_max, k_nyq)


def mark_spectrum_average_since_restart(average: SpectrumAverageState, S: DnsState | None) -> None:
    average.__dict__.update(SpectrumAverageState().__dict__)
    average.since_restart = True
    if S is not None:
        average.Nbase = int(S.Nbase)
        average.NX_full = int(S.NX_full)
        average.NZ_full = int(S.NZ_full)
        average.NK_full = int(S.NK_full)


def _spectrum_average_geometry_matches(
    average: SpectrumAverageState,
    S: DnsState,
    sample: SpectrumSample,
) -> bool:
    return (
        average.Nbase == int(S.Nbase)
        and average.NX_full == int(S.NX_full)
        and average.NZ_full == int(S.NZ_full)
        and average.NK_full == int(S.NK_full)
        and average.nbins == int(sample.nbins)
        and average.previous_energy.shape == sample.energy.shape
        and average.integral_energy.shape == sample.energy.shape
        and average.count.shape == sample.count.shape
    )


def initialize_spectrum_average_from_sample(
    average: SpectrumAverageState,
    S: DnsState,
    sample: SpectrumSample,
    sim_time: float,
    step: int,
) -> None:
    keep_since_restart = bool(average.since_restart)
    average.initialized = True
    average.has_previous = True
    average.since_restart = keep_since_restart
    average.Nbase = int(S.Nbase)
    average.NX_full = int(S.NX_full)
    average.NZ_full = int(S.NZ_full)
    average.NK_full = int(S.NK_full)
    average.nbins = int(sample.nbins)
    average.sample_count = 1
    average.first_step = int(step)
    average.last_step = int(step)
    average.r_max = float(sample.r_max)
    average.k_nyq = float(sample.k_nyq)
    average.total_dt = 0.0
    average.previous_time = float(sim_time)
    average.first_time = float(sim_time)
    average.last_time = float(sim_time)
    average.previous_energy = _np.asarray(sample.energy, dtype=_np.float64).copy()
    average.integral_energy = _np.zeros_like(average.previous_energy, dtype=_np.float64)
    average.count = _np.asarray(sample.count, dtype=_np.int32).copy()


def update_spectrum_average(
    average: SpectrumAverageState,
    S: DnsState,
    sample: SpectrumSample,
    sim_time: float,
    step: int,
) -> bool:
    if sample.nbins <= 0 or sample.energy.size == 0 or sample.energy.shape != sample.count.shape:
        return False

    if not average.initialized:
        initialize_spectrum_average_from_sample(average, S, sample, sim_time, step)
        return True

    if not _spectrum_average_geometry_matches(average, S, sample):
        return False

    sample_energy = _np.asarray(sample.energy, dtype=_np.float64)
    average.count = _np.asarray(sample.count, dtype=_np.int32).copy()
    average.r_max = float(sample.r_max)
    average.k_nyq = float(sample.k_nyq)

    sim_time = float(sim_time)
    step = int(step)
    if not average.has_previous:
        average.has_previous = True
        average.previous_energy = sample_energy.copy()
        average.previous_time = sim_time
        average.first_time = sim_time
        average.last_time = sim_time
        average.first_step = step
        average.last_step = step
        average.sample_count = 1
        return True

    if sim_time <= average.previous_time or step == average.last_step:
        average.previous_energy = sample_energy.copy()
        average.previous_time = sim_time
        average.last_time = sim_time
        average.last_step = step
        if average.sample_count == 0:
            average.sample_count = 1
            average.first_time = sim_time
            average.first_step = step
        return True

    dt = sim_time - average.previous_time
    average.integral_energy += 0.5 * dt * (average.previous_energy + sample_energy)
    average.total_dt += dt
    average.previous_energy = sample_energy.copy()
    average.previous_time = sim_time
    average.last_time = sim_time
    average.last_step = step
    average.sample_count += 1
    return True


def sample_and_update_spectrum_average(
    S: DnsState,
    average: SpectrumAverageState,
    sim_time: float | None = None,
    step: int | None = None,
) -> bool:
    sample = compute_energy_spectrum_uv_bins(S)
    if sim_time is None:
        sim_time = float(S.t)
    if step is None:
        step = int(S.it)
    return update_spectrum_average(average, S, sample, float(sim_time), int(step))


def spectrum_average_kind(average: SpectrumAverageState) -> str:
    if average.total_dt > 0.0:
        return (
            "simulation-time trapezoid since restart"
            if average.since_restart
            else "simulation-time trapezoid"
        )
    return "single snapshot since restart" if average.since_restart else "single snapshot"


def spectrum_average_mean_energy(average: SpectrumAverageState) -> _np.ndarray:
    if not average.initialized or not average.has_previous or average.previous_energy.size == 0:
        raise ValueError("no spectrum samples accumulated")
    if average.total_dt > 0.0:
        return average.integral_energy / average.total_dt
    return average.previous_energy.copy()


def save_energy_spectrum_csv_data(
    filename: str,
    energy,
    count,
    r_max: float,
    meta_header=None,
    meta_row=None,
    average: SpectrumAverageState | None = None,
    average_kind: str | None = None,
) -> None:
    energy_np = _np.asarray(energy, dtype=_np.float64)
    count_np = _np.asarray(count, dtype=_np.int64)
    nbins = min(int(energy_np.size), int(count_np.size))

    header = list(meta_header or ())
    values = list(meta_row or ())
    if average is not None and average_kind is not None:
        header.extend(
            [
                "AVERAGE_KIND",
                "SPECTRUM_SAMPLES",
                "T_START",
                "T_END",
                "TOTAL_DT",
                "FIRST_STEP",
                "LAST_STEP",
            ]
        )
        values.extend(
            [
                average_kind,
                int(average.sample_count),
                float(average.first_time),
                float(average.last_time),
                float(average.total_dt),
                int(average.first_step),
                int(average.last_step),
            ]
        )

    with open(filename, "w", newline="") as f:
        if header and values:
            f.write(_csv_comment_line(header))
            f.write(_csv_comment_line(values))
        writer = csv.writer(f)
        writer.writerow(("normalized_radius", "shell_sum_energy", "count"))
        for i in range(nbins):
            if count_np[i] > 0 and energy_np[i] > 0.0:
                r0 = float(r_max) * float(i) / float(nbins)
                r1 = float(r_max) * float(i + 1) / float(nbins)
                writer.writerow((0.5 * (r0 + r1), energy_np[i], int(count_np[i])))

    label = f"{average_kind} energy spectrum" if average_kind else "energy spectrum"
    print(f"[CSV] Wrote {filename} ({label}, {nbins} bins)")


def save_energy_spectrum_uv_csv(
    S: DnsState,
    filename: str,
    meta_header=None,
    meta_row=None,
) -> None:
    sample = compute_energy_spectrum_uv_bins(S)
    save_energy_spectrum_csv_data(
        filename,
        sample.energy,
        sample.count,
        sample.r_max,
        meta_header=meta_header,
        meta_row=meta_row,
    )


def save_spectrum_average_csv(
    average: SpectrumAverageState,
    filename: str,
    meta_header=None,
    meta_row=None,
) -> None:
    mean_energy = spectrum_average_mean_energy(average)
    kind = spectrum_average_kind(average)
    save_energy_spectrum_csv_data(
        filename,
        mean_energy,
        average.count,
        average.r_max,
        meta_header=meta_header,
        meta_row=meta_row,
        average=average,
        average_kind=kind,
    )


# ---------------------------------------------------------------------------
# Helpers for visualization fields (energy, vorticity, streamfunction)
# ---------------------------------------------------------------------------

def dns_kinetic(S: DnsState):
    xp = S.xp

    u = S.ur_full[0, :, :]
    w = S.ur_full[1, :, :]

    ke = xp.sqrt(u * u + w * w)
    field = _astype32(ke, xp.float32)
    if S.backend != "mlx":
        S.ur_full[2, :, :] = field
    return field


def _spectral_band_to_phys_full_grid(S: DnsState, band) -> any:
    xp = S.xp

    N = S.Nbase
    NX_full = S.NX_full
    NZ_full = S.NZ_full
    NK_full = S.NK_full

    NX_half = N // 2
    NZ = N

    zc = _zero_c(xp)

    uc_tmp = xp.zeros((NZ_full, NK_full), dtype=xp.complex64)
    uc_tmp[:NZ, :NX_half] = band

    hi_start = N // 2
    hi_end = min(3 * N // 4, NK_full - 1)
    if hi_start <= hi_end:
        uc_tmp[:, hi_start:hi_end + 1] = zc

    halfN = N // 2
    k_max = min(halfN, NK_full)

    if k_max > 0:
        z_mid_start = halfN
        z_mid_end = halfN + halfN
        z_top_start = N
        z_top_end = N + halfN

        uc_tmp[z_top_start:z_top_end, :k_max] = uc_tmp[z_mid_start:z_mid_end, :k_max]
        uc_tmp[z_mid_start:z_mid_end, :k_max] = zc

    z_mid = NZ
    if z_mid < NZ_full:
        uc_tmp[z_mid, :NX_half] = zc

    fft = S.fft

    if S.backend == "cpu":
        phys = fft.irfft2(uc_tmp, s=(NZ_full, NX_full), axes=(0, 1), overwrite_x=True, workers=S.fft_workers, norm='forward')
    elif S.backend == "mlx":
        phys = fft.irfft2(uc_tmp, s=(NZ_full, NX_full), axes=(0, 1), norm='forward')
    else:
        phys = fft.irfft2(uc_tmp, s=(NZ_full, NX_full), axes=(0, 1), overwrite_x=True, norm='forward')

    return xp.asarray(phys, dtype=xp.float32)


def dns_om2_phys(S: DnsState):
    band = S.om2
    phys = _spectral_band_to_phys_full_grid(S, band)
    if S.backend != "mlx":
        S.ur_full[2, :, :] = phys
    return phys


def dns_stream_func(S: DnsState):
    xp = S.xp

    N = S.Nbase
    NX_half = N // 2
    NZ = N

    alfa_1d = _astype32(S.alfa, xp.float32)
    gamma_1d = _astype32(S.gamma, xp.float32)

    ax = alfa_1d[None, :]
    gz = gamma_1d[:, None]

    K2 = ax * ax + gz * gz
    K2 = K2 + _f32(xp, 1.0e-30)

    phi_hat = _div_by_real(xp, S.om2, K2)
    phys = _spectral_band_to_phys_full_grid(S, phi_hat)
    if S.backend != "mlx":
        S.ur_full[2, :, :] = phys
    return phys


def dns_phi_phys(S: DnsState) -> None:
    xp = S.xp
    fft = S.fft

    NZ_full = S.NZ_full
    NX_full = S.NX_full

    u_hat = fft.rfft2(S.ur_full[0], s=(NZ_full, NX_full), axes=(0, 1))
    v_hat = fft.rfft2(S.ur_full[1], s=(NZ_full, NX_full), axes=(0, 1))

    kx = xp.arange(S.NK_full, dtype=xp.float32)[None, :]
    kz_i = xp.arange(NZ_full, dtype=xp.int32)
    kz_i = xp.where(kz_i <= NZ_full // 2, kz_i, kz_i - NZ_full)
    kz = kz_i.astype(xp.float32)[:, None]

    k2 = kx * kx + kz * kz
    div = kx * u_hat + kz * v_hat
    zero_f = _f32(xp, 0.0)
    denom = xp.where(k2 > zero_f, k2, _f32(xp, 1.0))
    phi_hat = _div_by_real(xp, -1j * div, denom)
    phi_hat = xp.where(k2 > zero_f, phi_hat, _zero_c(xp))

    phys = fft.irfft2(
        phi_hat,
        s=(NZ_full, NX_full),
        axes=(0, 1),
        norm="forward",
    )
    S.ur_full[2, :, :] = _astype32(xp.asarray(phys), xp.float32)


# ---------------------------------------------------------------------------
# Main driver (Python version of main in dns_all.cu)
# ---------------------------------------------------------------------------
def run_dns(
    N: int = 8,
    Re: float = 100,
    K0: float = 10.0,
    STEPS: int = 2,
    CFL: float = 0.75,
    backend: Backend = "auto",
    start_spectrum: SPECTRUM = "KM3",
    UPDATE: int = 100,
    method: TimeStepper = "CNAB2",
) -> DnsState | None:
    if method == "CHECK":
        compare_time_steppers(N, Re, K0, STEPS, CFL, backend, start_spectrum, UPDATE)
        return None

    print("--- RUN DNS ---")
    print(f" N   = {N}")
    print(f" Re  = {Re}")
    print(f" K0  = {K0}")
    print(f" Steps = {STEPS}")
    print(f" CFL  = {CFL}")
    print(f" Update = {UPDATE}")
    print(f" Start spectrum = {start_spectrum}")
    print(f" Method = {method}")
    print(f" requested = {backend}")

    seed = pao_seed_from_env(1)

    start =  time.perf_counter()

    free_before = None
    if backend == "gpu" and _cp is not None:
        _cp.cuda.Device().synchronize()
        free_before, _ = _cp.cuda.Device().mem_info

    S = create_dns_state(
        N=N,
        Re=Re,
        K0=K0,
        CFL=CFL,
        backend=backend,
        seed=seed,
        start_spectrum=start_spectrum,
        populate_compact_ur=False,
    )
    xp_label = {"gpu": "cupy", "mlx": "mlx.core"}.get(S.backend, "scipy")
    print(f" effective = {S.backend} (xp = {xp_label})")
    elapsed = time.perf_counter() - start
    print(f" DNS INITIALIZATION took {elapsed:.3f} seconds")

    if S.backend == "mlx":
        print(f" MLX active memory ≈ {_mx.get_active_memory() / (1024 * 1024):.2f} MiB")

    if S.backend == "gpu" and _cp is not None and free_before is not None:
        _cp.cuda.Device().synchronize()
        free_after, _ = _cp.cuda.Device().mem_info
        driver_used_mib = (free_before - free_after) / (1024 * 1024)
        pool_used_mib = _cp.get_default_memory_pool().used_bytes() / (1024 * 1024)
        print(f" create_dns_state: GPU memory ≈ {driver_used_mib:.2f} MiB"
              f"  (pool used {pool_used_mib:.2f} MiB)")

    if S.backend == "cpu" and _spfft is not None and S.fft_workers > 1:
        fft_ctx = _spfft.set_workers(S.fft_workers)
    else:
        fft_ctx = nullcontext()

    with fft_ctx:
        if _spfft is not None:
            print(f" scipy.fft workers in-context = {_spfft.get_workers()}")
        else:
            print(" scipy.fft workers in-context = n/a (gpu or scipy.fft missing)")

        dns_step2a(S)

        CFLM = compute_cflm(S)
        if S.backend == "gpu":
            CFLM0 = float(CFLM)  # one sync here at init (fine)
        else:
            CFLM0 = float(CFLM)

        S.dt = S.cflnum / (CFLM0 * math.pi)
        S.cn = 1.0
        S.cnm1 = 0.0
        S.t = 0.0

        print(f" [NEXTDT INIT] CFLM={CFLM0:11.4f} DT={S.dt:11.7f} CN={S.cn:11.7f}")
        print(f" Initial DT={S.dt:11.7f} CN={S.cn:11.7f}")

        S.sync()
        t0 = time.perf_counter()

        for it in range(1, STEPS + 1):
            S.it = it
            dt_old = S.dt
            if method == "LS_IMEX_RK3":
                dns_step_ls_imex_rk3(S)
            elif method == "CNAB2":
                dns_step2b(S)
                dns_step3(S)
                dns_step2a(S)
            else:
                raise ValueError(f"unknown time stepper: {method}")
            if (it % UPDATE) == 0 or it == 1 or it == STEPS:
                CFLM = next_dt(S, sync_host=True)
                if CFLM is None:
                    CFLM = compute_cflm(S)
                _, _, tau_l = S.flow_eddy_metrics()
                print(f" ITERATION {it:6d} T={S.t:12.10f} DT={S.dt:10.8f} CN={S.cn:10.8f} CFLM={float(CFLM):.6f} TAU_L={tau_l:.6f}")
            S.t += dt_old

        S.sync()
        t1 = time.perf_counter()

        elap = t1 - t0
        fps = (STEPS / elap) if elap > 0 else 0.0

        print(f" Elapsed CPU time for {STEPS} steps (s) = {elap:.8g}")
        print(f" Final T={S.t:.8g}  CN={S.cn:.8g}  DT={S.dt:.8g}")
        print(f" FPS = {fps:.8g}")
        return S


def _as_numpy_field(field) -> _np.ndarray:
    if hasattr(field, "get"):
        return _np.asarray(field.get())
    return _np.asarray(field)


def _relative_errors(reference, candidate) -> tuple[float, float]:
    ref = _as_numpy_field(reference).astype(_np.complex128, copy=False)
    cand = _as_numpy_field(candidate).astype(_np.complex128, copy=False)
    diff = cand - ref
    l2 = float(_np.linalg.norm(diff.ravel()) / max(_np.linalg.norm(ref.ravel()), 1.0e-30))
    linf = float(_np.max(_np.abs(diff)) / max(float(_np.max(_np.abs(ref))), 1.0e-30))
    return l2, linf


def compare_time_steppers(
    N: int,
    Re: float,
    K0: float,
    STEPS: int,
    CFL: float,
    backend: Backend,
    start_spectrum: SPECTRUM,
    UPDATE: int,
) -> None:
    print("--- CHECK TIME STEPPERS ---")
    print(" Compare CNAB2 and LS_IMEX_RK3 from the same PAO seed and report final-field errors.")
    cnab2 = run_dns(N, Re, K0, STEPS, CFL, backend, start_spectrum, UPDATE, "CNAB2")
    rk3 = run_dns(N, Re, K0, STEPS, CFL, backend, start_spectrum, UPDATE, "LS_IMEX_RK3")
    if cnab2 is None or rk3 is None:
        return

    om_l2, om_linf = _relative_errors(cnab2.om2, rk3.om2)
    u_l2, u_linf = _relative_errors(cnab2.ur_full[0:2], rk3.ur_full[0:2])
    e0, _, tau0 = cnab2.flow_eddy_metrics()
    e1, _, tau1 = rk3.flow_eddy_metrics()

    print("--- CHECK RESULT ---")
    print(f" OM2 relative L2={om_l2:.6e} Linf={om_linf:.6e}")
    print(f" U   relative L2={u_l2:.6e} Linf={u_linf:.6e}")
    print(f" Energy CNAB2={float(e0):.8e} LS_IMEX_RK3={float(e1):.8e} diff={abs(float(e1) - float(e0)):.6e}")
    print(f" Tau_L  CNAB2={float(tau0):.8e} LS_IMEX_RK3={float(tau1):.8e} diff={abs(float(tau1) - float(tau0)):.6e}")

def main():
    args = sys.argv[1:]
    N = int(args[0]) if len(args) > 0 else 512
    Re = float(args[1]) if len(args) > 1 else 10000
    K0 = float(args[2]) if len(args) > 2 else 10.0
    STEPS = int(args[3]) if len(args) > 3 else 1001
    CFL = float(args[4]) if len(args) > 4 else 0.25

    BACK = cast(Backend, args[5].lower()) if len(args) > 5 else "auto"
    UPDATE = int(float(args[6])) if len(args) > 6 else 100
    START_SPECTRUM = cast(SPECTRUM, args[7].upper()) if len(args) > 7 else "KM3"
    METHOD = cast(TimeStepper, args[8].upper()) if len(args) > 8 else "CNAB2"

    echo_command("sim", N, Re, K0, STEPS, CFL, BACK, UPDATE, START_SPECTRUM, METHOD)

    run_dns(N=N, Re=Re, K0=K0, STEPS=STEPS, CFL=CFL, backend=BACK, start_spectrum=START_SPECTRUM, UPDATE=UPDATE, method=METHOD)


if __name__ == "__main__":
    main()
