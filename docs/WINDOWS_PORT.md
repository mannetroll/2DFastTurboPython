# Windows Python port

One PySide6 application now drives either native backend through the existing
version 1 C ABI and ctypes. `create_engine` is an alias of the existing `Engine`:

```python
import fastturbo

with fastturbo.create_engine(fastturbo.Configuration(resolution=1024, reynolds=50000)) as engine:
    engine.advance(32)
    print(engine.inspect())
```

macOS loads the preserved Swift/Metal/VkFFT library. Windows loads a C++ DLL
linked to the authoritative Windows CUDA/VkFFT core. The full timestep, FFT
plans, persistent buffers, padding, integrators and diagnostics remain native.
N is retained resolution: N=1024 has a 1536² nonlinear workspace.

The shared `NativeUI` maps configuration, transport, live parameters, regulation,
display options, exports and benchmark limits into the native controller. Its
Windows worker advances complete native batches without Python callbacks. A
separate C++ rendering thread uses the reference D3D11 renderer in a Qt-hosted
child HWND. CUDA frame leases and device-to-device interop keep physical fields
off the Python boundary. Native rendering has a 60 Hz deadline. Only explicit
validation/PNG exports read back bulk data.

Windows uses native system fonts in the same Qt layout. Closing the window keeps
the simulation running, as on macOS; the Windows tray icon provides Show
Simulation, Pause/Resume and Quit. macOS retains Dock reopen behavior.

`python/native/windows/README.md` documents source reuse and guarded build-time
adaptations. Neither reference tree is edited. `GUI_PARITY.md` maps all existing
controls. The source has no drag, forcing or vortex painting; those operations
are reported unavailable rather than introducing new physics.

Public scripting API: `Configuration`, `Engine` / `create_engine`,
`get_backend_info`, and the existing `advance`, `inspect`, `set_parameters`,
`reset`, `export`, and `close` methods. `NativeUI` retains its existing coarse
JSON command API. Opaque handles, owned UTF-8 response strings and explicit string
freeing are shared by both backends; no CPython-specific extension ABI is added.

## Reproduce on Windows

Development prerequisites: Windows x64, NVIDIA GPU and compatible driver, the
existing CUDA Toolkit 13.0 installation, MSVC x64 C++ Build Tools, CMake 3.26+,
Ninja and uv. This port uses the reference toolchain and Release flags without
upgrading or reconfiguring it. The build discovers MSVC with `vswhere` and uses
`CUDA_PATH`, falling back to the reference's CUDA 13.0 location.

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File python/scripts/build-windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File python/scripts/test-windows.ps1 -AllResolutions
powershell -NoProfile -ExecutionPolicy Bypass -File python/scripts/run-windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File python/scripts/benchmark-windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File python/scripts/package-windows.ps1
```

Equivalent development workflow inside `python/`:

```powershell
uv sync --python 3.13
uv run 2dfastturbo
uv run 2dfastturbo --benchmark --resolution 1024 --reynolds 50000 --steps 200
uv run pytest -q
```

Close running FastTurbo/Python processes before rebuilding a loaded Windows DLL.
The scripts use a project-local uv cache. First installation builds both
`python/build/windows/reference/2DFastTurbo.exe` and
`python/src/fastturbo/_native/fastturbo.dll`. Runtime CUDA DLLs and licenses are
staged beside the native library. Installed wheels use those resources without
source trees or compilers. Source distributions include the reference inputs.
Source-archive compilation was also tested. The builder publishes unchanged
VkFFT headers in a short, content-addressed temporary cache to keep MSVC's nested
includes within Windows path limits when uv extracts an archive into a long path.

The default development target is the reference's `86` (RTX 3090 target with
PTX). The default package therefore targets compute capability 8.6 or later;
older RTX hardware needs an explicitly rebuilt target. For the reference's
broader RTX distribution targets, build with
`FASTTURBO_CUDA_ARCHITECTURES=75-real;80-real;86-real;89-real;120-real;120-virtual`.
`FASTTURBO_CUDA_ARCHITECTURES` accepts CMake architecture lists;
`FASTTURBO_DISTRIBUTION=ON` selects the reference's static CUDA/NVRTC/MSVC runtime
configuration. These are build settings, not simulation controls.

## Validation and limitations

The port has been built and executed on Windows 10 Pro with an RTX 3090,
NVIDIA driver 616.56, CUDA Toolkit 13.0.88, MSVC 19.44, and Python 3.13.9.
The existing 39 numerical tests passed against the Windows reference. Additional
Windows tests verify the shared ABI, Unicode exports, bitwise native agreement
for all six method/form combinations, independent shader pixel equations and
native simulation/rendering while Python processes no events. The shared GUI
automation covers every available resolution, N=128 through 4096.

Final gate: **52/52 Python tests, 2/2 CTest checks, 57/57 shared GUI checks**.
Native transport buttons, unchanged configuration, tray reopening and Save-dialog
cancellation were also exercised through Windows input paths. All six native
method/form comparisons and the N1024/N2048 comparisons were bitwise equal.
The 24 exported shader combinations agree with the independent pixel model
within its two-byte channel tolerance. Full results and captures are in
[`python/evidence/windows`](../python/evidence/windows).

## Final performance and distributions

Three interleaved five-second runs per case, same retained N, seed=2, k0=4,
Re=50000, CFL=3, ETDRK4/stress and native batching. Values are medians:

| Case | Native steps/s | Python steps/s | Native / Python R_turbo | Native / Python ms/step | Python time overhead |
|---|---:|---:|---:|---:|---:|
| N1024 headless | 760.17 | 756.23 | 1.256196 / 1.250329 | 1.31549 / 1.32235 | +0.52% |
| N1024 GUI | 692.12 | 698.42 | 1.155744 / 1.165038 | 1.44483 / 1.43180 | -0.90% |
| N2048 headless | 197.67 | 197.02 | 0.177490 / 0.176909 | 5.05893 / 5.07554 | +0.33% |
| N2048 GUI | 184.95 | 185.24 | 0.165345 / 0.165615 | 5.40692 / 5.39838 | -0.16% |

These results show no material performance regression. Negative GUI values are
small timing differences, not a general Python speedup. `PERFORMANCE.md` gives
the timing definitions, variability, profiling and the isolated frame-pacing fix.

Generated artifacts under `python/dist/`:

* `2DFastTurbo-Python-Windows.zip`: portable EXE folder including Python, Qt,
  CUDA runtime DLLs, NVRTC, shaders and licenses.
* `fastturbo-0.1.0-py3-none-win_amd64.whl`: built native wheel for Python users.
* `fastturbo-0.1.0.tar.gz`: self-contained source archive, also built into a wheel.

The frozen GUI and headless app passed with Windows-only PATH, development
variables removed, CUDA cache disabled and an empty working directory. Both
produced exactly the same spectrum as the native reference. Loaded-module audits
found bundled libraries and Windows/driver components (including Windows
Defender), with no Toolkit, compiler or external Python dependencies. The package
directory remained unchanged. This is an isolated-environment check on the
development PC, not a fresh-machine or VM certification. The separate wheel
check also initialized VkFFT and advanced N34/M51 without Toolkit paths.

The Windows desktop must be visible for GUI/capture tests. A composited desktop
capture is required to see DXGI flip surfaces: Qt's HWND-only capture can produce
a black canvas even though the field is displayed correctly. PNG exports use the
native renderer and do not have that capture limitation.

macOS execution cannot be repeated from this Windows machine. The Swift bridge,
Metal kernels, rendering source, native controller and macOS build commands are
preserved. Existing macOS tests remain available; Windows-only code and libraries
are isolated behind platform selection.

Windows 11, other GPUs, and HDR monitors require separate hardware validation.
No installer signing or publishing is performed. The portable package uses an
EXE with an adjacent `_internal` directory containing Python, Qt and native
runtime libraries; keep the folder intact.

The frozen bundle explicitly includes NVRTC's builtins next to PyInstaller's
collected runtime compiler as well as beside the native DLL. This is necessary
for NVRTC's delayed load with a Windows-only PATH. All DLLs and licenses are
collected during packaging; runtime startup does not compile application source.
The common loader also preloads builtins by absolute path because NVRTC's legacy
delayed lookup does not honor Python's `AddDllDirectory` in ordinary wheel installs.
