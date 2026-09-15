# FastTurbo

**Watch two-dimensional turbulence evolve on your GPU.**

FastTurbo combines an interactive desktop application with a Python API for
simulating decaying, incompressible 2D flow. Explore swirling vorticity, inspect
energy and enstrophy, and export fields for analysis. Native Metal / VkFFT on
macOS and CUDA / VkFFT on Windows handle the simulation and rendering.

<img src="https://raw.githubusercontent.com/mannetroll/fastturbo-media/2640cfadc321b14ccd0b36d6b764d271429970ac/macos-5000-iterations.png" alt="FastTurbo on macOS showing orange and purple vorticity after exactly 5,000 iterations, with live controls and diagnostics" width="960">

*An actual run on Apple M1 Max after 5,000 iterations: a 1024 × 1024 grid,
1536 × 1536 padded workspace, and the Ember palette.*

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
Press **Ctrl+Q on Windows** or **Cmd+Q on macOS** to quit the application.
On Windows, the title-bar **X** and **Alt+F4** also quit and stop the simulation.
**File → Close Window** hides it to the notification area while computing continues.
On macOS, closing the window keeps the simulation running for Dock reopen.

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
