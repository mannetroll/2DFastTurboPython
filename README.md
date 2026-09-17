# 2DFastTurbo Python

A shared Python-hosted turbulence application for macOS, Windows and Linux, using
native Swift/Metal/VkFFT or C++/CUDA/VkFFT backends. Python supplies the PySide6 controls;
all CFD timesteps, FFTs, buffers and high-rate field rendering stay native.

Linux headless and the shared GUI now pass on native Ubuntu / RTX 3090 using
CUDA/VkFFT and GPU-resident OpenGL rendering through Xwayland. The prior WSLg
interop limitation remains separate. See [LINUX_PORT.md](LINUX_PORT.md) for setup,
exact commands, numerical/pixel parity and measured headless/GUI overhead.

![2DFastTurbo Python on macOS, showing vorticity and simulation controls after 5,000 iterations](docs/images/macos-5000-iterations.png)

*Captured on Apple M1 Max after exactly 5,000 iterations: 2048² grid, 3072² padded
workspace, and the native Metal / VkFFT backend.*

Requires Python 3.13+ and Apple Silicon/macOS 15+, Windows 10/11 x64, or Linux
with a compatible NVIDIA GPU for the CUDA backends. Source builds need Xcode/Metal on macOS, or
CUDA 13.0, MSVC C++ tools, CMake and Ninja on Windows, plus [uv](https://docs.astral.sh/uv/).

```sh
cd python
uv run 2dfastturbo
# Equivalent:
uv run python -m fastturbo
```

First launch builds the native library automatically. Default retained resolution
is **2048²**, with the classical **3072² padded** nonlinear workspace. The default
is ETD RK4, stress form, CFL 3, k₀=10, seed 2, with palinstrophy regulation off.
Enable **Regulate with Palinstrophy** to adjust the Reynolds number automatically.
Changing N or k₀ recalculates the initial Re from the calibration formula in either mode.

Space pauses/resumes, Right advances one step, R resets, Cmd-Shift-R increments
the seed, F focuses the flow, and Cmd-S exports a PNG. Closing or minimizing the
window keeps the simulation running; View → Show Simulation or the Dock reopens it.
On Windows, use Ctrl in place of Cmd; the tray icon can reopen the retained run.
Press **Ctrl+Q on Windows** or **Cmd+Q on macOS** to quit the application.

Windows build, test and run from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File python/scripts/build-windows.ps1
powershell -ExecutionPolicy Bypass -File python/scripts/test-windows.ps1 -AllResolutions
powershell -ExecutionPolicy Bypass -File python/scripts/run-windows.ps1
```

The portable Windows ZIP is generated under `python/dist/` by
`python/scripts/package-windows.ps1`; it includes Python, Qt and native runtimes.
See the [Windows port report](WINDOWS_PORT.md), [GUI parity matrix](GUI_PARITY.md)
and [Windows validation evidence](python/evidence/windows/validation.json).
The default Windows build targets compute capability 8.6 or later.

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

Windows RTX 3090 measurements found 0.52% headless time overhead at N1024 and
0.33% at N2048; visible GUI timings were within 1% of native. All 52 Python tests,
two native CTest checks and 57 GUI checks passed. Native numerical comparisons
were bitwise equal. macOS execution was not repeated on the Windows machine.
