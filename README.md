# 2DFastTurbo Python

A Python-hosted macOS turbulence application with the original native
Swift/Metal/VkFFT solver and HDR renderer. Python supplies the PySide6 controls;
all CFD timesteps, FFTs, buffers and high-rate field rendering stay native.

Requires Apple Silicon, macOS 15+, and Python 3.13+. Development builds also need
Xcode with the Metal toolchain and [uv](https://docs.astral.sh/uv/).

```sh
cd python
uv run 2dfastturbo
# Equivalent:
uv run python -m fastturbo
```

First launch builds the native library automatically. Default retained resolution
is **1024²**, with the classical **1536² padded** nonlinear workspace. The default
is ETD RK4, stress form, CFL 3, k₀=4, seed 2, with native palinstrophy regulation.

Space pauses/resumes, Right advances one step, R resets, Cmd-Shift-R increments
the seed, F focuses the flow, and Cmd-S exports a PNG. Closing or minimizing the
window keeps the simulation running; View → Show Simulation or the Dock reopens it.

```sh
# Verify the native bridge, mathematics, Swift parity and actual GUI.
uv run python scripts/build_reference.py
uv run pytest -q
uv run python scripts/check_gui.py --all-resolutions

# Build a self-contained, locally signed macOS app.
uv run python scripts/package_app.py
open 'dist/2DFastTurbo Python.app'

# Build source and platform wheel distributions.
uv build
```

The `.app` includes Python, Qt, the native engine and shaders. It needs no terminal,
uv, Xcode or repository checkout at runtime. It is locally signed, not notarized
for public distribution. No app is installed into `/Applications` automatically.

[Reference contract](PORT_REFERENCE.md) · [Architecture](ARCHITECTURE.md) ·
[Measured performance and validation](PERFORMANCE.md) · [Python API](python/README.md)

Five matched M1 Max benchmark pairs measured 0.40% headless overhead; native GPU
step times matched within 0.02%. Live Python throughput was 1.52% higher in this
sample, with about 60 FPS. All paired endpoint spectra were byte-identical.
