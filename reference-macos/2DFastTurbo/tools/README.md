# Python / Swift CNAB2 solver comparison

From `reference-macos/2DFastTurbo`, run the equivalent of the Python baseline:

```bash
SCIPYTURBO_SEED=1 ./tools/sim 512 10000 10 1001 0.25 metal 100 PAO CNAB2 \
  > tools/sim_512_1001_metal_macOS.txt

python3 tools/compare_sim.py \
  ../palinstrophy/tools/sim_512_1001_gpu_macOS.txt \
  tools/sim_512_1001_metal_macOS.txt
```

`./tools/sim` without arguments selects that same case. Arguments follow Python's
order: `N Re K0 STEPS CFL BACKEND UPDATE SPECTRUM METHOD`. This tool supports
`metal PAO CNAB2`, power-of-two N from 16 to 2048, and `SCIPYTURBO_SEED` from
1 to 5010 (default 1). Zero steps prints initialization and final diagnostics.

## Requirements

- macOS 15 or later, a Metal GPU, and Xcode's Swift/C++ toolchain (`xcrun`).
- The existing vendored VkFFT and metal-cpp headers in `ThirdParty/VkFFTSource`.
- This repository's `python/.venv`, prepared with `uv sync` from `python/`.
  The development dependencies include NumPy and SciPy for PAO initialization.
  By default, the runner uses `repo/python/.venv/bin/python` and the checked-in
  `tools/turbo_simulator.py`; no sibling checkout is needed for `tools/sim`.
  `--python-repo /path/to/source` overrides the initializer directory (either
  `turbo_simulator.py` or `palinstrophy/turbo_simulator.py` within it).
  `--python /path/to/python` overrides the interpreter independently.

The first invocation builds a release binary in `tools/.build/`; later invocations
reuse it until the solver, launcher, bridge, vendored headers, or toolchain changes.
Build messages go to stderr, so stdout can be redirected directly to a comparison
log. The tool does not require building or launching the GUI.

## Numerical compatibility

This is a dedicated Swift/Metal CNAB2 mode. It reuses the app's VkFFT bridge and
leaves the app's existing ETDRK4/RK3 solvers unchanged.

`export_pao.py` calls the authoritative Python PAO **initializer only** and exports
its pristine complex64 velocity and vorticity coefficients. This gives both solvers
the same actual field, since the app's native PAO generator uses a different RNG.
The native runner verifies the exported arrays' SHA-256 hashes. All timesteps,
FFTs, nonlinear products, and field evolution then run in Swift/Metal. Python's
solver and existing `sim_512_1001_*.txt` references are never rewritten.

The comparison mode preserves Python's:

- Fourier coefficient normalization, vorticity sign, 3/2 padding, retained modes,
  and three nonlinear products (`u²`, `v²`, `uv`).
- CNAB2 startup with zero nonlinear history and `CNM1=0`, subsequent history
  updates, and Float32 coefficient arithmetic with contraction disabled in the
  comparison kernels.
- CFL maximum `max(|u|+|v|) × N/(2π)`, Float32 scaling, and the host timestep
  controller. Timestep updates occur at step 1, every `UPDATE`, and the last step.
- MLX's spectral `TAU_L` diagnostic, including its stored endpoint weighting.

As in Python, each iteration prints **T before that step's time increment**, while
DT and CN are the newly regulated values and field diagnostics describe the
completed step. The final T includes the last increment. Full-precision JSON also
provides `state_time` for each checkpoint to make this convention explicit.

VkFFT and MLX use different FFT implementations, so results are not bit-for-bit
identical. `compare_sim.py` checks case parameters, seed, checkpoint completeness,
and all initial/iteration/final diagnostics. It allows an absolute tolerance of
`5e-10`, relative tolerance of `1e-5`, and half a printed unit from each log;
override these with `--atol` and `--rtol`. It exits 0 for agreement, 1 for numerical
differences outside tolerance, and 2 for invalid or incompatible logs.

For the supplied N=512, seed=1, 1001-step reference, validation found matching
printed iteration DT and TAU_L, a final T difference of approximately `3e-8`, and
maximum printed CFLM difference of `0.000123`. Comparison with a fresh unchanged
Python MLX run gave final relative L2 differences of approximately `4.0e-6` in
vorticity, `3.9e-7` in velocity, and `9.4e-5` in nonlinear history. The differentiated
nonlinear history is more sensitive to FFT roundoff than the evolved fields.

## Diagnostics and timing

```bash
SCIPYTURBO_SEED=1 ./tools/sim 512 10000 10 1001 0.25 metal 100 PAO CNAB2 \
  --json /tmp/swift-cnab2.json --state-out /tmp/swift-cnab2-state
```

JSON includes full-precision diagnostics, configuration, PAO source/array hashes,
elapsed wall time, FPS, and `T_R = final T / elapsed wall seconds`. Optional final
arrays are raw little-endian files:

| File | Type | Shape |
| --- | --- | --- |
| `omega.c64` | complex64 | `[N, N/2]` |
| `history.c64` | complex64 | `[N, N/2]` |
| `velocity.f32` | float32 | `[2, 3N/2, 3N/2]` |

Despite the inherited `Elapsed CPU time` label, both logs measure **wall time**.
The native timer includes the step loop, GPU completion, diagnostic reductions,
and iteration printing; it excludes compilation, PAO export, FFT plan creation,
initialization, and final file exports. `DNS INITIALIZATION` measures native setup,
including FFT plans and the first inverse FFT, after PAO export.

The native runner batches dependent steps in command buffers, completing at every
diagnostic checkpoint and at most every 128 steps. Timings measure this CNAB2
comparison runner; use the app's existing benchmark for ETDRK4/RK3 throughput.
The comparator reports measured timings and T_R for both logs. Repeat runs on an
otherwise idle GPU before drawing performance conclusions from them.

## Regression validation

```bash
../palinstrophy/.venv/bin/python tools/validate_cnab2.py --full \
  --output /tmp/cnab2-validation
```

This checks that the shared initial arrays exactly equal MLX's, compares the
independently evolved vorticity, velocity, and nonlinear history, and checks all
printed diagnostics. Small cases cover startup, every-step and intermittent CFL
updates, a final step off the update interval, several seeds, and command-buffer
boundaries. `--full` adds the N=512, 1001-step case. Relative L2 and relative maximum
errors must be at most `1e-5` for the evolved fields and `3e-4` for nonlinear history;
logs use the comparator's diagnostic tolerances. The validator also checks that
missing checkpoints and changed diagnostic values are rejected. Retained output
contains both logs, native JSON, and measured field error norms.
