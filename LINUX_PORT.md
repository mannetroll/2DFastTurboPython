# Linux port status — 2026-09-17

**Native Ubuntu now runs the shared application with GPU-resident OpenGL rendering.
The earlier WSLg interoperability limitation remains; it has not been retested on
WSL or claimed fixed.** The current checkout is on a different host environment
from the one originally described in `PythonPortLinux.txt`.

## Current native Ubuntu validation

Ubuntu 26.04.1 LTS, kernel `7.0.0-31-generic`, x86_64, GNOME Wayland with Xwayland,
RTX 3090 / 24 GiB / sm_86, NVIDIA driver 595.91.07, driver CUDA capability 13.2.
This fresh checkout had no toolkit, uv, CMake, Ninja or Qt xcb development/runtime
dependencies. Missing development components were installed **under the user's
home directory**, using NVIDIA's checksum-verified CUDA redistributables and
Ubuntu development packages extracted locally. No system driver, compiler or
system package was replaced. The actual tools are CUDA/nvcc 13.2.51, GCC/G++ 15.2,
CMake 4.4.3, Ninja 1.13.2 (Kitware build), uv 0.12.15, CPython 3.14.4 and PySide6
6.11.2. Python's `>=3.13` policy is unchanged.

CUDA/OpenGL registration succeeds on the actual NVIDIA context. Direct Qt Wayland
context creation failed on this host; the application uses Qt's `xcb` platform
and an embedded GLX child through Xwayland. It rejects a Wayland QApplication
before treating a native surface pointer as an X11 window ID. Desktop screenshots
use the application's XComposite backing pixmap, because QScreen's root-screen
capture returns black under this compositor.

## Architecture and GPU ownership

The shared `Configuration`, `Engine` / `create_engine`, `NativeUI`, CLI and PySide6
window remain the application API. macOS uses Metal/VkFFT; Windows and Linux
compile the **same unchanged CUDA solver, kernels and VkFFT plans** from
`reference-windows/src`. No numerical reference file was changed.

`python/native/cuda/controller.cmake` applies the existing Windows controller
adaptations for both platforms. Generated Linux controller files are byte-for-byte
identical to the previous Windows build's adaptations. The native simulation
worker retains batching, live parameter updates, regulation, snapshots and run
clocks. The reference has no CUDA Graph mode.

`libfastturbo.so` contains the C ABI bridge and controller. It loads
`libfastturbo_gl.so` only on a UI/PNG request; headless initialization never loads
Qt, X11, GL or EGL. The OpenGL module owns a separate native presentation thread's
X11 connection, GLX context, persistent texture buffer, CUDA interop registration
and nonblocking copy stream. Each new immutable CUDA frame is copied **device to
device** into the mapped OpenGL buffer. Unmapping and waiting for that copy stream
protect the frame lease before GL sampling. Simulation uses its own CUDA stream.
Recoloring a paused frame reuses the uploaded buffer. A native 60 Hz deadline
controls presentation while the native solver runs independently.

The shaders preserve the Windows/Metal periodic interpolation, orientation,
normalization, palettes and velocity-direction lines. PNG export renders into an
offscreen GL framebuffer and reads pixels only on explicit request. Normal steps
and frames do not read physical fields or pixels into Python or host arrays.
Readback helpers under `scripts/` are explicit validation captures, not the
production rendering path. Linux presentation and PNG output are SDR; HDR display
validation is not claimed.

A new loader fix locates the **actually loaded NVRTC** library and preloads its
matching builtins from that directory. NVRTC's internal `dlopen` did not inherit
the caller's RUNPATH in the user-local toolkit. This fixes the observed plan
failure without `LD_LIBRARY_PATH` for CUDA, preserving the prior conditional WSL
JIT-loader fix.

## Build, test and run

For this prepared checkout, from the repository root:

```sh
source python/build/local-env.sh
bash python/scripts/build-linux.sh
bash python/scripts/test-linux.sh             # Headless gate + native CTest
bash python/scripts/test-linux.sh --gui       # Complete suite on this desktop
bash python/scripts/run-linux.sh --headless --resolution 1024 --reynolds 50000 \
  --k0 4 --steps 3832 --output results/headless.json
bash python/scripts/run-linux.sh              # Existing shared GUI
bash python/scripts/benchmark-linux.sh --mode both --runs 3 --seconds 5 \
  --output evidence/linux-native/final
```

The generated `python/build/local-env.sh` selects the local tools and locally
extracted Qt xcb dependencies. It is machine setup, not a required application
source file. On a normally provisioned Linux host, put uv/CMake/Ninja on PATH and
set `CUDA_HOME` to the existing CUDA 13.x toolkit. Desktop builds also need the
OpenGL, X11 and libpng development packages, and the Qt xcb runtime dependencies
listed by [Qt](https://doc.qt.io/qt-6/linux-requirements.html). From `python/`:

```sh
uv sync
FASTTURBO_LINUX_RENDERER=ON uv run fastturbo-build --force
uv run 2dfastturbo
uv run 2dfastturbo --headless --resolution 1024 --reynolds 50000 --k0 4
uv run pytest -q
uv run python scripts/check_linux_rendering.py
uv run python scripts/check_linux_workers.py
uv run python scripts/profile_overhead.py
```

`FASTTURBO_LINUX_RENDERER=AUTO` is the default: build graphics when dependencies
are found. `ON` fails clearly if graphics development dependencies are missing;
`OFF` builds only the headless library and harnesses. After installing missing
dependencies, use `fastturbo-build --force` to reconfigure an existing AUTO cache.
`CMAKE_PREFIX_PATH` supports development packages outside standard system paths.
Only the Linux platform adapter selects xcb; platform checks do not enter the
shared solver or control definitions.

## Verification and actual measurements

**48 Python tests passed, 12 Windows-specific tests skipped; native CTest 1/1
passed.** These include the formerly failing GUI and PNG checks, headless execution
without display variables or Qt/GL libraries (also verified with `python3 -S`
and only the standard library), all integrator/form combinations,
independent FP64 numerical checks, long-run decay, and deterministic Windows
trajectory comparison. Maximum relative diagnostic error in the stored 3,832-step
Windows trajectory is **1.8721445e-06**, within the unchanged 3 ppm tolerance.
The independently invoked Linux build of the Windows CLI harness also matches
spectra byte-for-byte, including N1024/N2048. This is binding parity on Linux;
no new Windows execution or cross-OS raw-field comparison is claimed.

The shared GUI check passes **52 assertions**. All 24 field/palette/range pixel
comparisons pass at a maximum error of **1/255** against the independent reference
(tolerance remains 2/255). The native worker check validates actual X11 canvas
transport clicks, Save-dialog cancellation, recovery after an export error,
visible GPU pixels, fatal-renderer-error shutdown, and continued
stepping/presentation with no Python/Qt events.
An observed 0.6-second interval contained 6,279 steps and 36 presentations.

The optional `--all-resolutions` experiment successfully rebuilt/stepped through
N9216, then failed VkFFT allocation at N12288 on this 24 GiB GPU. Its failed report
is retained in `gui-all-resolutions.json`; it was not relabeled a passing test.
N16384 was not reached. The original resolution choices are preserved. Successful
higher-resolution control checks do not imply all high-resolution rendering or
physics configurations fit this GPU.

Three alternating headless/GUI five-second samples per grid, seed 2, k0=4,
Re=50000, CFL=3, ETDRK4/stress. Headless uses batch 8 and eight warmup steps/reset;
GUI uses native adaptive batches, two-second warmup/reset and a 640-pixel
Vorticity/Ember/Sigma canvas. Medians:

| Retained → padded | Headless ms/step | Headless steps/s | Headless R_turbo | GUI ms/step | GUI steps/s | GUI R_turbo | GUI overhead |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1024² → 1536² | 1.386244 | 721.374 | 1.198584 | 1.482454 | 674.557 | 1.129336 | 5.78% |
| 2048² → 3072² | 5.422594 | 184.414 | 0.164884 | 6.030713 | 165.818 | 0.147771 | 10.38% |

GUI overhead is `100*(R_headless-R_gui)/R_headless`. Presentation is about 60 Hz,
measured over actual presentation intervals, including final paused frames;
it is separate from the active simulation clock. Compared with historical
configuration-matched Windows Python runs, native Linux headless R_turbo is
4.14% lower at N1024 and 6.80% lower at N2048. Driver, toolchain, desktop activity
and measurement date differ, so this does not isolate an operating-system cost.

Native GPU-event intervals occupy 99.66% and 99.90% of headless wall time.
A separate N1024 profile measured batch8 11.10328 ms, inspection 0.03277 ms,
vorticity snapshot 0.08068 ms, velocity snapshot 0.15855 ms, and no-op ABI upper
bound 0.00866 ms. In representative GUI runs the upload/map/unmap/wait intervals
totaled 0.41949 s (N1024) and 0.51091 s (N2048) for 301 uploads each; these overlap
the simulation worker and are not additive GUI overhead. GL draw submission and
swap calls were much smaller. The measured bottleneck is native GPU work;
rendering's largest measured host interval is CUDA/GL sharing and copy-stream
waiting. No per-kernel profile or speculative solver optimization is claimed.

Final timing records are in [`python/evidence/linux-native/final`](python/evidence/linux-native/final);
the parent evidence directory retains the first timing round and all validation.
The table uses the final three-run medians, including the completed canvas styling.
The headless checkpoint precedes GUI work and is recorded in
`headless-milestone.json` and `python/build/checkpoints/native-headless`.

## H100 and remaining limits

A separate Release build with `FASTTURBO_RENDERER=OFF` and CUDA architecture 90
succeeds, and cuobjdump confirms native `sm_90.cubin`. The installed application
retains sm_86. For a prepared H100 host, use:

```sh
cd python
export FASTTURBO_CUDA_ARCH=90
export FASTTURBO_LINUX_RENDERER=OFF
uv sync
uv run fastturbo-build --force
uv run 2dfastturbo --headless --resolution 1024 --reynolds 50000 --k0 4 \
  --wall-seconds 5 --output results/h100.json
```

H100 execution/performance, macOS/Windows runtime regressions on this host,
Linux HDR, native Wayland-surface embedding and distributable Linux wheels remain
untested or unfinished. Headless runtime needs no desktop or Qt initialization;
the shared desktop package still declares PySide6 as an installation dependency.
PNG exports currently need an X11/Xwayland display. WSLg's historical CUDA graphics
sharing limitation remains as documented below. No WSL display driver was installed.

---

# Historical WSL headless checkpoint — 2026-09-16

The remainder records the previous WSL environment and **pre-renderer source
state**. Its two GUI failures describe that checkpoint, not the current native
Ubuntu test results above.

**The headless Linux CUDA milestone passes. The complete Linux application does
not yet pass: GPU rendering through the tested WSLg stack is blocked.** The
existing GUI/control and PNG-rendering tests remain failing; they were not
removed, weakened or marked as expected failures. Interactive R_turbo and GUI
overhead have not been measured.

## Validated environment

Ubuntu 24.04.5 LTS, WSL2, kernel
`6.6.114.1-microsoft-standard-WSL2`, x86_64, on Windows 10 Pro. The user launches
Ubuntu from PowerShell. NVIDIA GeForce RTX 3090, 24 GiB, compute capability 8.6;
Windows KMD 616.56, WSL interface 615.65.06, CUDA driver capability 13.4.
Toolkit `/usr/local/cuda-13.3`, nvcc 13.3.73, GCC/G++ 13.3.0, CMake 3.28.3,
Ninja 1.11.1, uv 0.11.33, CPython 3.14.6, PySide6 6.11.2. The Python policy
remains `>=3.13`. No driver, toolkit or system compiler was installed/replaced.

WSLg has working local Xwayland and Wayland sockets. A visible Qt Wayland window
with `DISPLAY` unset and `GALLIUM_DRIVER=d3d12` reports OpenGL 4.6,
`D3D12 (NVIDIA GeForce RTX 3090)`. The unforced Wayland context selected llvmpipe;
that software context was used only for diagnosis, never as a solver or production
renderer. The NVIDIA-selected context was also tested and failed CUDA interop.

The Codex sandbox hides `/dev/dxg`: sandboxed NVML reports GPU access blocked and
CUDA enumeration returns error 35. **All reported CUDA tests, timings and graphics
probes ran with approved execution outside the sandbox.** A separately compiled
minimal kernel allocates GPU memory, writes 42, synchronizes and reads back 42.
This distinguishes an execution permission restriction from CUDA availability.

## Architecture and build

`fastturbo.build` explicitly dispatches macOS to the existing Metal/VkFFT build,
Windows to the existing CUDA/VkFFT build, and Linux to `build_linux.py`.
Linux builds `libfastturbo.so` with Release CMake/Ninja, strict host FP math and
the reference's `--fmad=false`. Native sm_86 machine code is present, alongside
PTX. Compiler, source and architecture hashes invalidate the build cache;
locking and atomic library replacement protect concurrent builds.

Both CUDA backends compile **the same unchanged** `solver.cu`, `kernels.cuh`,
`fft.cpp` and headers in `reference-windows/src`. There is no Linux solver copy.
`python/native/cuda/bridge_common.hpp` shares configuration/diagnostic JSON and
explicit export helpers with Windows. Linux excludes Win32, Direct3D and all
window libraries from its numerical library. See
[`python/native/cuda/README.md`](python/native/cuda/README.md).

The binding remains version 1 of `python/native/fastturbo.h`: opaque C handles
and owned JSON strings through ctypes. The common Python API remains
`Configuration`, `Engine` / `create_engine`, `get_backend_info`, `advance`,
`inspect`, `set_parameters`, `reset`, `export`, and `close`. ctypes releases the
GIL during native calls, including coarse native batches. Plans and buffers are
persistent; timestep scheduling, integrators and reductions remain native. The
reference has no CUDA Graph mode, so Graph/non-Graph comparison is inapplicable.

Retained N1024 means a 1024² spectrum with a 1536² nonlinear workspace, using the
same classical 3/2 padding, normalization, zero/Nyquist treatment and truncation.
Only small diagnostics cross into Python normally. Explicit field/spectrum file
exports are restricted to requested analysis/validation. GPU snapshot requests
return scalar metadata and retain their full fields on the device.

### Process-local WSL loader fix

The first compiled library crashed because the active WSL CUDA driver loaded
the distro's older `libnvidia-ptxjitcompiler.so.580.178.04`. `cuda_loader.cpp`
initializes the driver, discovers the **actually loaded** WSL UMD directory from
`/proc/self/maps`, and preloads its matching GPU compiler/JIT libraries. It does
not modify system files, guess a driver-store version, or affect ordinary Linux
driver selection. CUDA library RPATHs allow execution with `LD_LIBRARY_PATH`
unset; this is covered by a subprocess test.

## Reproduce the headless milestone

From the repository root:

```sh
bash python/scripts/build-linux.sh
bash python/scripts/test-linux.sh
bash python/scripts/benchmark-linux.sh
```

Or from `python/`:

```sh
uv sync
uv run fastturbo-build
uv run 2dfastturbo --headless --resolution 1024 --reynolds 50000 \
  --k0 4 --seed 2 --cfl 3 --integrator etdrk4 --operator stress \
  --steps 3832 --warmup 8 --batch 8 --output results/headless.json
uv run pytest -q tests/test_numerics.py tests/test_linux.py -k 'not rendering'
ctest --test-dir build/linux --output-on-failure
uv run python scripts/benchmark_linux.py --runs 3 --seconds 5
```

The headless subprocess explicitly removes `DISPLAY`, `WAYLAND_DISPLAY`,
`XDG_RUNTIME_DIR` and `LD_LIBRARY_PATH`, rejects Qt imports, and checks that Qt,
X11, Wayland, OpenGL and EGL libraries were not loaded. It successfully advances
N1024/M1536. The current dependency declaration still installs PySide6 for the
shared desktop project; the headless runtime does not import or initialize it.
A smaller headless installation extra remains packaging work, deferred along
with distribution packaging until the GUI requirement can be met.

## Correctness and performance evidence

The complete suite was run, with **43 passing tests, 12 Windows-specific skips,
and 2 failures**: existing GUI control validation and PNG-rendering validation.
The numerical/headless subset and native CTest lifecycle suite pass. Coverage
includes independent FP64 FFT, projection, derivatives, padding/truncation and
nonlinear evolution checks; all three integrators and both forms; odd padded
sizes; deterministic initialization/reset; live Re/CFL; UInt64 seeds; fixed and
adaptive dt; exact endpoints; batching/thread serialization; snapshot lifetime;
and a finite decaying 10,000-step run.

Linux binding output is byte-identical to an independently invoked CLI harness
compiled from the Windows reference source, including N1024 and N2048. This is
**same-platform binding parity**, not a new Windows execution. Separately, the
actual stored Windows N1024/M1536, seed-2, k0-4, Re-50000, CFL-3, ETDRK4/stress
trajectory was repeated for 3,832 steps. Maximum diagnostic relative error was
`1.87215e-6` (palinstrophy); time `1.47406e-7`, energy `6.77591e-8`, enstrophy
`1.55384e-8`. All satisfy the unchanged 3 ppm FP32 trajectory tolerance. Raw
Windows spectra for that stored run are unavailable; cross-OS field-by-field
parity is not claimed.

Three sequential five-second runs per grid; batch 8, eight warmup steps and
reset; matching physical configuration to the recorded Windows benchmark:

| Retained → padded | Median ms/step | Median steps/s | Median R_turbo |
|---|---:|---:|---:|
| 1024² → 1536² | 1.334983 | 749.0734 | 1.239378 |
| 2048² → 3072² | 5.044150 | 198.2495 | 0.178010 |

Compared with the historical Windows Python results, R_turbo is 0.876% lower
at N1024 and 0.622% higher at N2048. These are measured configuration-matched
comparisons across different toolchains/dates, not an interleaved OS experiment.
No major regression was observed and no speculative numerical optimization was
made. Full wall/simulated times, dt, steps, GPU identity, versions and turnover
rates are recorded in [`python/evidence/linux`](python/evidence/linux).

Native GPU event intervals occupy 94.18% and 96.14% of the representative
headless wall times. A later operation profile measured batch8 11.58094 ms,
inspection 0.08709 ms, vorticity snapshot 0.14112 ms, velocity snapshot 0.22489 ms,
and no-op ABI upper bound 0.01747 ms. These separate profiling samples are not
substituted for the benchmark medians. Nsight recorded 67 native stream waits
and 22,806 API events for 512 warmed steps plus diagnostics/snapshots. Waiting
for native GPU execution dominates recorded CPU API time. Its installed 13.3
tracing libraries warn about the 13.4 driver and provide no kernel/memcpy activity
tables: per-kernel cost and copy-size profiling are **not established**. This
limitation did not prevent execution or correctness verification.

```sh
uv run python scripts/profile_overhead.py
nsys profile --trace=cuda,nvtx --sample=none --cpuctxsw=none \
  --capture-range=cudaProfilerApi --capture-range-end=stop \
  --output=results/linux-cuda-profile .venv/bin/python scripts/profile_linux.py
```

The pre-GUI headless checkpoint is recorded in
`python/evidence/linux/headless-milestone.json`; source/native snapshots are in
`python/build/checkpoints/headless-linux`. The repository was subsequently
checkpointed by the user. Reference trees are unchanged.

## WSLg rendering blocker and unfinished GUI

The following **real GPU tests ran outside the sandbox** on a visible Wayland
window with `DISPLAY` unset and the NVIDIA D3D12 renderer selected:

| Candidate | Observed result |
|---|---|
| CUDA/OpenGL buffer registration | `cudaGLGetDevices` and `cudaGraphicsGLRegisterBuffer`: error 304, OS operation unsupported |
| CUDA/EGL image registration | EGL creates a real texture image; `cudaGraphicsEGLRegisterImage`: error 999 |
| CUDA VMM → GL external-memory fd | CUDA allocation/export succeeds (`anon_inode:dxgresource`); `glImportMemoryFdEXT` crashes inside `libnvwgf2umx.so` with both dedicated-memory flag settings |
| EGL dma-buf export | Not exposed by this GPU-backed EGL display |
| Installed Vulkan ICDs | Only llvmpipe CPU device; no NVIDIA Vulkan GPU exposed |

The Xwayland NVIDIA context also fails registration with error 304. Switching
from the earlier remote X display to local PowerShell-launched WSL/Wayland did
not remove the CUDA graphics sharing failure. A working xclock or GPU-backed Qt
window alone does not demonstrate CUDA graphics interoperability.

[NVIDIA's current WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html#features-not-yet-supported)
lists OpenGL/CUDA interoperability as unsupported. The probes above establish
the additional observed behavior of this exact installed stack. They do not
prove that every possible future interop mechanism or driver will fail.

The production GUI/renderer is therefore **not implemented or claimed to work**
on Linux yet. No per-frame CPU field/NumPy/QImage path was added. The shared GUI
source and existing macOS/Windows renderers remain intact. All Linux GUI controls,
render semantics, interaction, interactive throughput and GUI overhead remain
unverified; see `GUI_PARITY.md`. The source has no drag, forcing, vortex painting,
contour, preset, or separate maximum-dt GUI controls; none were invented.

Ordinary GUI launch is still the shared command:

```sh
uv run 2dfastturbo
```

It currently returns a descriptive unsupported-rendering error. To reproduce the
graphics checks (these are diagnostic windows, not the application):

```sh
env -u DISPLAY QT_QPA_PLATFORM=wayland GALLIUM_DRIVER=d3d12 \
  MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA \
  uv run python scripts/check_linux_interop.py --egl-image
uv run python scripts/check_linux_vulkan.py
uv run pytest -q  # Full status: retains the two rendering/GUI failures.
```

The opt-in `--external-memory` diagnostic reproduces the graphics-driver crash
recorded in `external-memory-probe.txt`; it is not part of normal launch or tests.
Completing the GUI requires a working GPU sharing mechanism on the target stack
or a change to the target/constraints. Installing a Linux NVIDIA display driver
inside WSL is not a solution and was not attempted.

## H100 / native Linux readiness

The shared core and binding have no WSL or desktop dependency. The WSL loader
adaptation is conditional on an actually loaded WSL driver; ordinary Ubuntu uses
the regular CUDA loader. On an H100 machine with a compatible existing toolkit:

```sh
cd python
FASTTURBO_CUDA_ARCH=90 uv sync
FASTTURBO_CUDA_ARCH=90 uv run fastturbo-build --force
FASTTURBO_CUDA_ARCH=90 uv run 2dfastturbo --headless --resolution 1024 \
  --reynolds 50000 --k0 4 --wall-seconds 5 --output results/h100.json
```

Keep `FASTTURBO_CUDA_ARCH=90` set for subsequent source-checkout invocations;
otherwise the default 86 configuration triggers a rebuild. The alias
`FASTTURBO_CUDA_ARCHITECTURES` and standard CMake architecture lists are also
accepted. Local development defaults to 86; no large architecture collection is
compiled. A separate Release **sm_90 build actually succeeded**, and cuobjdump
confirmed a native `sm_90.cubin`; the installed RTX build remains `sm_86.cubin`.
This verifies compilation only. H100/AWS execution, performance, ordinary Ubuntu
GUI behavior, and runtime regression tests on macOS/Windows are not yet tested.
