# Native Swift/Metal CNAB2 command

From `reference-macos/2DFastTurbo`:

```bash
SCIPYTURBO_SEED=1 ./tools/sim 512 10000 10 1001 0.25 metal 100 PAO CNAB2

# KM3 initial spectrum, with full-precision diagnostics and raw state export:
SCIPYTURBO_SEED=1 ./tools/sim 512 10000 10 1001 0.25 metal 100 KM3 CNAB2 \
  --json /tmp/swift-cnab2.json --state-out /tmp/swift-cnab2-state
```

`./tools/sim` without arguments selects the first case. Positional arguments are
`N Re K0 STEPS CFL BACKEND UPDATE SPECTRUM METHOD`. Supported choices are:

- Metal + VkFFT, CNAB2, and PAO or KM3 initialization.
- Power-of-two N from 16 to 16384, subject to available GPU memory.
- `SCIPYTURBO_SEED` from 1 to 5010 (default 1).
- Nonnegative steps; zero steps exports and reports the initial state.
- Positive Re, K0, CFL and update interval.

## Requirements and build

The launcher and solver use **no Python**, Python environment, or exported
initialization fixture. PAO's historical random generator and KM3 shell
redistribution are implemented in Swift; evolution runs on Metal/VkFFT.
The old `--python` and `--python-repo` arguments are no longer needed.

Building requires macOS 15 or later, a Metal GPU, Xcode's Swift/C++ toolchain
(`xcrun`), and the vendored headers in `ThirdParty/VkFFTSource`.
The Bash launcher builds a release executable in `tools/.build/` on first use.
It reuses the executable until source, bridge, headers or toolchain changes.
Build messages go to stderr, so stdout can be redirected to a comparison log.
It does not build or launch the GUI.

```bash
# Print the cached executable's path without running the solver:
./tools/sim --build-only
```

The resulting executable can also be run directly with the same arguments;
direct execution needs neither Python nor the build toolchain.

## Numerical behavior

The command shares `FastTurboCNAB2.swift` and `FastTurboCNAB2Shaders.swift` with
the macOS app and Python Metal backend. It uses the corrected CNAB2 numerator,
division, Float32 timestep/controller arithmetic and KM3 cutoff handling of the
Linux CUDA implementation.

- Historical negative-curl, normalized compact Fourier state `[N, N/2]`.
- Full 3/2 padding and three nonlinear products (`u²`, `v²`, `uv`).
- Zero nonlinear history and `CNM1=0` at startup.
- CFL maximum `max(|u|/dx + |v|/dx)`, where `dx=2π/N`, evaluated in Float32.
- Initial `dt=CFL/(CFLM*π)`; subsequent 0.8/0.2 timestep regulation.
- Controller checkpoints after step 1, every `UPDATE`, and the final step.
  GUI/API operation uses step 1 and every 100 steps, independently of batching
  and snapshots. The command's extra final checkpoint changes its reported next
  timestep, not the completed field.
- KM3's unpaired compact cutoff mode is split into a conjugate pair before the
  padded inverse FFT, preserving real physical velocities.

Each iteration prints **T before that step's time increment**. DT and CN are the
newly regulated values; field diagnostics describe the completed step. Final T
includes the last increment. JSON also provides each checkpoint's `state_time`.

CNAB2's historical PAO random sequence differs from the RK3/ETDRK4 initializer.
App exports convert CNAB2 into the app's positive-vorticity convention and N×N
layout. The command's compact raw exports retain the historical convention.

## Diagnostics and timing

JSON includes configuration, native initialization diagnostics, checkpoints,
elapsed wall time, steps/s, and `T_R = final T / elapsed wall seconds`.
`--state-out` writes little-endian arrays:

| File | Type | Shape |
| --- | --- | --- |
| `initial-omega.c64` | complex64 | `[N, N/2]` |
| `omega.c64` | complex64 | `[N, N/2]` |
| `history.c64` | complex64 | `[N, N/2]` |
| `velocity.f32` | float32 | `[2, 3N/2, 3N/2]` |

Despite the inherited `Elapsed CPU time` label, timing measures **wall time**.
It includes the step loop, GPU completion, diagnostics and iteration printing;
it excludes compilation, native initialization, FFT plan creation and file exports.
Dependent steps are batched into command buffers, completed at each diagnostic
checkpoint and at most every 128 steps.

## Optional Python validation

Python is used only by these developer checks. From `python/`, with the source
Metal library built and development dependencies installed:

```bash
uv run pytest tests/test_cnab2.py tests/test_cnab2_macos.py
```

These check native PAO/KM3 initialization, independent FFT/one-step calculations,
app/command trajectory equality, batch independence, snapshots, reset, endpoint
stepping and GUI configuration. The launcher is also tested with Python commands
deliberately disabled in its PATH.

To compare against the checked-in independent Python solver, from
`reference-macos/2DFastTurbo`:

```bash
../../python/.venv/bin/python tools/validate_cnab2.py --full \
  --output /tmp/cnab2-validation
```

This optional validator requires NumPy, SciPy and MLX in the selected interpreter.
`--python-repo` selects another comparison source directory.
It checks native initialization within Float32 tolerance, independently evolved
fields, printed diagnostics, and rejection of incomplete or changed logs.
`--full` adds the N512, 1001-step case. FFT implementations and math libraries
differ, so comparisons use relative tolerances rather than bitwise equality.
The unchanged MLX reference also uses the older numerator, reciprocal division
and Double host timestep controller. Relative L2/maximum limits are `3e-5` for
evolved fields and `3e-4` for nonlinear history. On Apple M1 Max, the N512,
1001-step check measured vorticity L2 `2.28e-5` and velocity L2 `1.65e-5`.
Independent native one-step checks retain a tighter `3e-6` limit, and native
app/command spectra must match bit for bit.

Existing logs can be compared independently:

```bash
python3 tools/compare_sim.py /path/to/python.txt /path/to/swift.txt
```

The comparator checks parameters, seeds, every expected checkpoint, and all
initial/iteration/final diagnostics. Default tolerances are `atol=5e-10`,
`rtol=1e-5`, plus both printed rounding radii. It exits 0 on agreement, 1 on a
numerical mismatch and 2 on invalid or incompatible logs.
