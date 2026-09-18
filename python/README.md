# FastTurbo

Linux source-checkout status: the shared CUDA/VkFFT backend and PySide6 GUI with
GPU-resident OpenGL rendering are tested on native Ubuntu 26.04.1 / RTX 3090.
Use `uv run 2dfastturbo --headless` for execution without Qt/display initialization.
Desktop builds need OpenGL/X11/libpng development files and Qt's xcb runtime
libraries. The earlier WSLg CUDA sharing limitation remains; prebuilt Linux wheels
are not supplied. See [Linux build and validation status](../LINUX_PORT.md).

**Watch two-dimensional turbulence evolve on your GPU.**

FastTurbo combines an interactive desktop application with a Python API for
simulating decaying, incompressible 2D flow. Explore swirling vorticity, inspect
energy and enstrophy, and export fields for analysis. Native Metal / VkFFT on
macOS and CUDA / VkFFT on Windows and Linux handle the simulation and rendering.

<img src="https://raw.githubusercontent.com/mannetroll/fastturbo-media/6e497874eb6583d1db9dd1e61aa7af2d814a5f52/macos-5000-iterations.png" alt="FastTurbo on macOS showing orange and purple vorticity after exactly 5,000 iterations, with live controls and diagnostics" width="960">

*An actual run on Apple M1 Max after 5,000 iterations: a 2048 × 2048 grid,
3072 × 3072 padded workspace, k₀=10, seed 2, Palinstrophy regulation off, and the Ember palette.*

## Install and launch

### Run with uv (recommended)

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed,
launch FastTurbo in an isolated environment:

```sh
uv tool run --python 3.13 --no-build --from fastturbo 2dfastturbo
```

Or use the equivalent `uvx` shortcut:

```sh
uvx --python 3.13 --no-build --from fastturbo 2dfastturbo
```

uv manages the environment, downloads Python 3.13 if needed, and installs
FastTurbo and PySide6 automatically. `--no-build` requires prebuilt wheels.
Add application options after `2dfastturbo`, such as `--help` or
`--benchmark --steps 200`.

### Install with pip

Use **Python 3.13 or later** in a virtual environment on a supported platform:

```sh
python -m pip install --only-binary=:all: fastturbo
2dfastturbo
```

You can also launch with `python -m fastturbo`.

Prebuilt wheels include the native engine. **No Xcode, CUDA Toolkit, C++
compiler, or source checkout is needed to install and run a wheel.** uv or pip
installs the PySide6 desktop dependency automatically. The binary-only option
ensures installation uses available wheels.

| Platform | Requirements |
| --- | --- |
| macOS | macOS 15 or later, Apple Silicon (arm64), Metal |
| Windows | Windows 10 or 11, x64 Python, NVIDIA GPU with compute capability 8.6 or later, and a driver compatible with CUDA 13.0 |

The Windows wheel bundles the CUDA runtime and NVRTC; install the NVIDIA driver
separately. Linux, Intel Macs, Windows ARM64 and CPU-only execution are not
supported by these wheels. If pip reports no matching distribution, check your
Python version, operating system and architecture.

## Explore the flow

- **Live visualization:** switch between vorticity, enstrophy and speed; choose
  Ember, Inferno, Aurora or Glacier colors and adjust the display range.
- **Interactive controls:** pause, single-step, reset the flow or change the
  seed. Adjust resolution, Reynolds number, timestep control and integration
  method from the window.
- **Native GPU computation:** FFTs, simulation buffers, timesteps and field
  rendering stay in the native backend while Python provides the controls.
- **Reproducible experiments:** use a fixed seed, advance exact step counts,
  inspect diagnostics and export numerical fields or PNG images.

The GUI starts with a 2048 × 2048 grid, ETD RK4 integration, stress form, CFL 3,
k₀=10, seed 2 and palinstrophy regulation off. Enable **Regulate with Palinstrophy**
to adjust the Reynolds number automatically. Changing N or k₀ recalculates the
initial Re from the calibration formula in either mode. Press
**Space** to pause or resume, **Right Arrow** to single-step and **R** to reset.
Press **Ctrl+Q on Windows or Linux** or **Cmd+Q on macOS** to quit the application.
The title-bar close button (**X**, or the red button on macOS) also quits and
stops the simulation on all three platforms. **File → Close Window** quits on
macOS and Linux; on Windows it hides the window to the notification area while
computing continues, and **Alt+F4** quits. Minimizing keeps the simulation running.

## Use from Python

```python
from fastturbo import Configuration, Engine, get_backend_info

print(get_backend_info())

with Engine(Configuration(resolution=1024, reynolds=50_000, seed=2)) as engine:
    engine.advance(steps=32)
    diagnostics = engine.inspect()
    print(diagnostics)

    engine.export(png="flow.png", display="vorticity", palette="ember")
    engine.export(spectrum="spectrum.c64", fields="fields.f32")
```

`Engine` is a synchronous batch API. `create_engine` is an alias for `Engine`.
Call `inspect()` after advancing to refresh energy, enstrophy and palinstrophy.
Use `engine.set_parameters(reynolds=40_000, cfl=2)` to change parameters while
preserving the field and clock, or `engine.reset()` to restart with the same seed.

Spectrum exports contain normalized, little-endian complex64 Fourier-series
coefficients in `[ky, kx]` order, with shape `N × N`. Field exports contain
little-endian float32 vorticity and the two velocity components, in
`[omega, u, v]` order, with shape `3 × N × N`.

Initialize the native library on the main thread before creating engines from
worker threads. GUI calls must stay on the main thread. For headless experiments,
set Reynolds number explicitly when changing resolution or initial wavenumber;
the GUI's automatic regulation is separate from the batch API.

## Run a benchmark

FastTurbo 0.1.2 wheels include the **`2dbench`** command alongside `2dfastturbo`.
After installing the wheel, run `2dbench` directly. To run the published release
with uv on a platform with a prebuilt wheel:

```sh
uvx --python 3.13 --no-build --from "fastturbo>=0.1.2" 2dbench
```

The wheel contains the benchmark module and native engine; running it requires
no source checkout or compiler toolchain.

For a comparable architecture benchmark from a source checkout:

```sh
cd python
uv run 2dbench
```

This runs retained **2048² → padded 3072²** and **4096² → padded 6144²** for
at least **20 seconds each**, then prints device/platform metadata, one compact
performance table, and the 4096/2048 timestep ratio. It reuses the existing
headless `2dfastturbo --benchmark` harness and production native engine:
CUDA/VkFFT on Linux/Windows and Metal/VkFFT on macOS. It does not import Qt or
create a window; Linux works over SSH without DISPLAY, Wayland, X11 or a desktop.

```sh
uv run 2dbench                         # Standard architecture comparison
uv run 2dbench --seconds 2             # Quick test
uv run 2dbench --sizes 1024 2048 4096   # Custom retained sizes
uv run 2dbench --device 0              # CUDA visible-device / Metal device index
uv run 2dbench --json > h100.json       # Archive results
uv run 2dbench --help
```

The workload is fixed across devices: float32, ETDRK4, stress form, 3/2 padding,
seeded PAO initialization, k₀=10, seed=2, CFL=3, Re=27898.36614974335, adaptive
timesteps with palinstrophy regulation off, and batches of eight complete steps.
The existing eight-step warmup is followed by a reset and synchronous inspection.
`--warmup K` changes that step count. No workload setting changes with GPU model.

`Seconds` is the existing harness's synchronized wall time: batch submission,
completed native steps, and final diagnostic inspection. `ms/step` and `steps/s`
derive from that interval. Both backends wait for completed GPU work before
returning each batch. Startup, contexts, allocation, VkFFT plans, initialization,
warmup and reset are outside timing. The deadline is checked after each batch,
so overshoot is at most one batch plus final inspection. Expect roughly 40–50
seconds plus initialization. JSON also preserves the native report, GPU timing,
initialization time, exact configuration, per-case memory availability, Git
commit and dirty status.

Cases use separate processes to release native resources after failures. A lower
bound on the production buffers is checked against free CUDA memory where
available, or total unified memory on Metal. This is not a peak-memory guarantee:
VkFFT scratch and other allocations vary. Native allocation errors produce an
explicit `SKIPPED: insufficient device memory` row; other errors produce
`FAILED`. Sizes are never reduced, subsequent cases still run, and any
skipped/failed case returns exit status 1. JSON remains a single valid document;
native/build diagnostics go to stderr. An externally killed process is reported
as failed without guessing the cause.

Source checkouts build the existing backend automatically and need its normal
compiler/toolkit prerequisites; see [development setup](DEVELOPMENT.md). CUDA
source builds default to CMake's `native` architecture detection. Explicit
`FASTTURBO_CUDA_ARCH` (Linux) or `FASTTURBO_CUDA_ARCHITECTURES` overrides remain
available for cross compilation, including `90` for H100. The toolkit must
support the selected architecture. Metal indices follow `MTLCopyAllDevices`;
CUDA indices respect `CUDA_VISIBLE_DEVICES`. VkFFT's version is recorded from
the header used at build time; unavailable metadata is reported as unknown.

The lower-level reference executables, legacy positional PAO/CNAB2 interface,
parity tests, and existing benchmarks remain available.

Run 200 timesteps without opening the desktop window and save JSON diagnostics:

```sh
2dfastturbo --benchmark --resolution 1024 --reynolds 50000 --steps 200 --output results/benchmark.json
```

Save the final field as an image:

```sh
2dfastturbo --benchmark --steps 5000 --export flow.png
```

Use `2dfastturbo --help` for all options, including integration methods, display
settings, field exports and visible GUI benchmarks.

## License and included resources

FastTurbo's own code, documentation and screenshot are **MIT licensed**,
Copyright © 2026 Torbjörn Sjögren. Bundled VkFFT and nlohmann/json retain their
MIT notices; Apple metal-cpp uses Apache-2.0. The Windows CUDA components retain
the NVIDIA CUDA Toolkit agreement. License texts and component notices are
included in each distribution under `fastturbo-<version>.dist-info/licenses/`.
PySide6 and Qt are installed separately under their own licenses.

The screenshot above is hosted in the public
[FastTurbo media repository](https://github.com/mannetroll/fastturbo-media), so
it is visible without access to the private application repository. A copy is
also included in the installed package at
`fastturbo/assets/macos-5000-iterations.png`.
