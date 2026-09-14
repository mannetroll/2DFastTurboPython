# Developing FastTurbo

These instructions are for source checkouts. For installation from PyPI, see
[README.md](README.md). For building and publishing wheels, see
[PUBLISHING.md](PUBLISHING.md).

Python 3.13+, with macOS 15+ / Apple Silicon / Metal, or Windows 10/11 x64 /
NVIDIA CUDA. macOS source builds need Xcode with Metal tools. Windows source
builds use the existing CUDA 13.0, MSVC x64 C++ tools, CMake and Ninja.
Dependencies are pinned in `uv.lock`; the native bridge builds automatically.

From the repository root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File python/scripts/build-windows.ps1
powershell -ExecutionPolicy Bypass -File python/scripts/test-windows.ps1 -AllResolutions
powershell -ExecutionPolicy Bypass -File python/scripts/run-windows.ps1
powershell -ExecutionPolicy Bypass -File python/scripts/benchmark-windows.ps1
```

Alternatively, from `python/`, use `uv sync --python 3.13` and `uv run 2dfastturbo`.
The scripts use a project-local uv cache. The Windows DLL and native baseline are
built together under `build/windows`; CUDA runtime DLLs and licenses are staged
under `src/fastturbo/_native`. See `../WINDOWS_PORT.md` and `../GUI_PARITY.md`.

```sh
uv sync
uv run 2dfastturbo
uv run python -m fastturbo
# Explicit rebuild, if needed:
uv run fastturbo-build --force
```

The source checkout references `../reference-macos/2DFastTurbo` directly. Source
archives include the necessary reference sources and vendored headers. Installed
wheels and packaged applications load prebuilt resources without compiling.

`fastturbo.create_engine` aliases `Engine`; `fastturbo.get_backend_info()` reports
the selected native backend, GPU, defaults and capabilities. Windows loaders
preload bundled NVRTC builtins by absolute path so no CUDA Toolkit PATH is needed.

Python controls the original native engine. There are no NumPy/CPU FFTs, Python
CFD kernels, field readbacks for interactive rendering, or callbacks per timestep.
The shared PySide6 window embeds a native Metal or Direct3D canvas and transport
controls; the native controller handles batching, diagnostics and regulation.

```python
from fastturbo import Configuration, Engine

with Engine(Configuration(resolution=1024, reynolds=50_000, seed=2)) as engine:
    completed = engine.advance(steps=32)
    diagnostics = engine.inspect()  # Refresh E, Z, P after completed GPU work.
    engine.set_parameters(reynolds=40_000, cfl=2)  # Keeps field and clock.
    engine.reset()  # Same seed, reused GPU allocations and FFT plans.
```

`Engine` is a synchronous headless batch API; `NativeUI` starts/pauses the native
asynchronous GUI controller. Initialize the library on the main thread before
using headless engines from workers; all GUI calls stay on the main thread.
`Configuration` has the Swift core defaults. The GUI additionally applies the
reference's automatic Re calibration/regulation. When changing N or k₀ in a
headless script, set Re explicitly if a different calibration is desired.

Field exports are deliberate file operations for testing and analysis:

```python
engine.export(spectrum="spectrum.c64", fields="fields.f32")
engine.export(png="flow.png", display="vorticity", palette="ember")
```

Use those calls inside the engine's context. Spectrum files are little-endian,
normalized complex64 Fourier-series coefficients `[ky,kx]`, shape N×N. Fields
are physical little-endian Float32 `[omega,u,v]`, shape 3×N×N. Energy/enstrophy
returned by `advance` remain cached until `inspect`, as in the native API.

```sh
uv run 2dfastturbo --benchmark --resolution 1024 --reynolds 50000 \
  --until 3 --batch 8 --output results/headless.json
uv run 2dfastturbo --gui-benchmark --resolution 1024 --reynolds 50000 \
  --until 3 --canvas-size 640 --output results/live.json
uv run 2dfastturbo --help

uv run python scripts/build_reference.py
uv run pytest -q
uv run python scripts/check_gui.py --all-resolutions
uv run python scripts/compare_performance.py
uv run python scripts/profile_overhead.py

uv build
uv run python scripts/package_app.py
open 'dist/2DFastTurbo Python.app'
```

Fixed dt, fixed batch and imported initial fields are developer options, as in
the reference. GUI defaults/ranges follow the actual Swift source. There is no
forcing/drag, Streamfunction selector, mouse vortex painting, or preset system
in that source. Qt typography, switch shapes, spacing and slider quantization
are the remaining presentation differences.

See `../PORT_REFERENCE.md`, `../ARCHITECTURE.md`, and `../PERFORMANCE.md` for the
complete control map, numerical conventions, measured overhead and evidence.

Windows distributions are created with `scripts/package-windows.ps1` (portable
EXE folder and ZIP) or `uv build` (source archive and `py3-none-win_amd64` wheel).
`scripts/check_windows_package.py` audits the frozen app under Windows-only PATH;
`scripts/check_windows_wheel.py` validates isolated wheel loading. The portable
folder includes Python and Qt. A wheel needs Python and the declared PySide6
dependency. The default CUDA target is compute capability 8.6 or later; see
`../WINDOWS_PORT.md` for other build targets and the hardware validation limits.
