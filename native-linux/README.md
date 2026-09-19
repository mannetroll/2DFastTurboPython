# Native Linux FastTurbo

`fastturbo-linux` adds a C++ Dear ImGui desktop to the existing CUDA/VkFFT
solver. It runs without Python, CPython, PySide6, Qt, GTK, or a browser.
The Python/PySide6 application remains available. `reference-linux` remains
the canonical headless validation executable; `2dbench` remains the benchmark.

## Ubuntu build and run

Use the working NVIDIA/CUDA installation. The tested environment is Ubuntu
26.04.1, RTX 3090, driver 595.91.07, CUDA Toolkit 13.3.1, GCC 15.2, CMake 4.4.3,
and the repository's VkFFT 1.3.4. Minimum build requirements are CMake 3.26,
a CUDA-compatible C++20 compiler, and CUDA Toolkit 13.x.

Desktop development packages, if missing:

```sh
sudo apt-get install --no-install-recommends libx11-dev libgl-dev libglx-dev libpng-dev
```

All four were already installed on the validation machine. No driver, toolkit,
or system package was changed. Dear ImGui **1.92.5** is fetched from its
[upstream tagged archive](https://github.com/ocornut/imgui/releases/tag/v1.92.5),
verified against a pinned SHA-256 in CMake, and statically linked with its
upstream OpenGL3 backend. There is no additional window toolkit.
The first configuration needs HTTPS access to `codeload.github.com`; subsequent
builds use the cached source. For offline builds, supply an extracted matching
source tree with `-DFETCHCONTENT_SOURCE_DIR_IMGUI=/absolute/path/to/imgui-1.92.5`.

From the repository root:

```sh
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=86 -DFASTTURBO_BUILD_NATIVE_GUI=ON
cmake --build build-linux -j2
./fastturbo-linux
```

The root launcher is a small shell script that executes the native ELF at
`./build-linux/fastturbo-linux`. That ELF can also be launched directly:

```sh
./build-linux/fastturbo-linux --resolution 2048 --interval-ms 100
./build-linux/fastturbo-linux --paused --device 0
./build-linux/fastturbo-linux --help
```

An optional user-selected install prefix needs only the executable and its
system CUDA/graphics libraries at runtime, with no checkout or Python:

```sh
cmake --install build-linux --prefix "$HOME/.local" --component native-gui
```

The build's CUDA library path is retained as an install RPATH, as for native
`2dbench`. This is a native local installation, not a portable CUDA bundle.
Dear ImGui's MIT license is installed alongside the executable under
`share/licenses/fastturbo-linux`; VkFFT and NVIDIA libraries retain their own
licenses (see the existing repository third-party notices).

For a compute-only machine, configuration performs no ImGui download or desktop
dependency lookup with:

```sh
cmake -S . -B build-headless -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=86 -DFASTTURBO_BUILD_NATIVE_GUI=OFF
cmake --build build-headless -j2
env -u DISPLAY -u WAYLAND_DISPLAY ./build-headless/2dbench
```

## Controls and metrics

The app starts running at N=2048, K0=10, seed 2, ETDRK4/stress, PAO, CFL 3,
with automatic palinstrophy regulation off. Edit N, Reynolds, CFL, K0, seed,
method, form, or PAO/KM3, then select **Apply and Reset**. Grid/K0 edits use the
existing controller's Reynolds recalibration rule. Selecting CNAB2 switches
to stress form and caps CFL at 0.25, matching the existing Linux application;
CNAB2 requires a power-of-two grid and seed 1..5010. Invalid configurations
produce an inline error without replacing the current run.

Run, Pause, Single Step, and Reset use the shared native controller. Pause
finishes the current batch; Single Step advances exactly once. Space toggles
running, Right steps, R resets, and Ctrl+Q or the title-bar close button exits.
The controls support mouse, keyboard navigation, numeric editing, and an X11
UTF-8 clipboard (small text selections). The layout scrolls on small windows.

Vorticity, velocity magnitude, enstrophy, flow overlay, Aurora/Ember/Glacier/
Inferno, exposure, sigma, and vorticity range all use the existing Linux shaders.
The update interval defaults to **100 ms** and accepts 4..10000 ms. Turning off
visualization or minimizing suppresses new GPU snapshots while stepping
continues. Re-enabling it while paused publishes the current field.

The statistics distinguish **ms/step**, **steps/s**, **R_turbo** (simulation time
per wall second), and **Rendering FPS** (UI frames submitted by this process).
The last quantity is not monitor scanout or remote-client FPS. CFL is labeled
as its configured target; the existing controller does not expose a measured CN.
The initial throughput rate appears after the controller's first measurement
interval. A very short run can therefore still show zero throughput when paused.

## Architecture and ownership

```text
ImGui controls ──► existing generated native Controller ──► shared turbo_core
                         independent worker                 CUDA / VkFFT
                                 │                               │
                                 └──── immutable GPU Frame ◄─────┘
                                               │
                                existing Linux CUDA/OpenGL upload
                                               │
                                 persistent GL texture buffer
                                               │
                                  unchanged field/flow shaders
                                               │
                                    OpenGL RGBA8 FBO texture
                                               │
                                      Dear ImGui image
                                               │
                                  existing X11/GLX window
```

`native-linux/CMakeLists.txt` links the same `turbo_core` built for
`reference-linux`. It uses `python/native/cuda/controller.cmake`, followed by
guarded, target-local substitutions for configurable snapshot cadence and
readiness without a frame. Only the native GUI's generated controller receives
these additions. The clock/file/report helpers are generated from the existing
Windows host source. Numerical kernels, initializers, FFT plans, stepping,
regulation, state ownership, and benchmark implementation are not copied.

`main.cpp` handles controls, frame selection, telemetry, and test orchestration.
`x11_imgui.cpp` forwards X11 input and window-manager messages to ImGui.
The existing renderer gains a small optional desktop interface in
`python/native/linux/render_api.hpp` and `renderer.cpp`; its embedded and PNG
entry points retain their behavior. No field shader is changed.

The renderer checks `cudaGLGetDevices` against the selected CUDA device. Its
existing map/copy/unmap sequence makes a device-to-device copy into a persistent
OpenGL buffer. The frame lease stays alive until the separate interop stream
has completed; only that stream is waited on for an upload. The solver keeps
its own stream, persistent plans and buffers. The normal GUI path performs no
full-field or pixel CPU readback. Explicit `--capture` and `--self-test` are the
only new pixel readback paths.

Both snapshot production and field texture updates respect the chosen interval.
The ImGui event loop wakes immediately for X11 input. While idle, presentation
follows the field interval (between 4 and 60 FPS); with visualization disabled
it refreshes telemetry at 4 Hz. Active numeric/mouse editing refreshes at up to
60 Hz. GLX vsync is disabled. This avoids repeatedly drawing unchanged fields
and never schedules individual solver steps. Closing
joins the simulation worker, releases leased frames/interop resources, and
destroys ImGui while the owning OpenGL context is still current.

## Validation and measurement

Ordinary CTest adds display-independent CLI and native controller tests. The
controller test compares all four methods against direct execution of the same
production solver, checks transport/reset, and checks autonomous stepping and
snapshot cadence. Real desktop acceptance is explicitly enabled:

```sh
cmake -S . -B build-linux -DFASTTURBO_NATIVE_GUI_TESTS=ON
ctest --test-dir build-linux --output-on-failure
./fastturbo-linux --self-test --output native-linux/evidence/desktop-test.json \
  --capture native-linux/evidence/desktop.png
```

The self-test sends actual X11 button events through ImGui, verifies three
distinct field images, reset determinism, resize, visibility changes and a
`WM_DELETE_WINDOW` close while the solver is running. A closed process with exit status zero is part of the
acceptance result. No mock display, CPU solver, replacement FFT, or relaxed
numerical tolerance is used. Keep desktop tests off for unattended GPU servers.

The optional comparison script orchestrates the **existing native `2dbench`**
and the **existing controller's timed GUI mode**, in rotating order:

```sh
python3 native-linux/scripts/compare.py --runs 3 --seconds 10 --sizes 2048 4096
uv run --project python 2dbench --seconds 10 --json
ldd build-linux/fastturbo-linux
```

Python assists this development script only. Each GUI measurement uses the
controller's two-second warmup/reset, fixed batch 8, and final settled
statistics. `2dbench` uses its unchanged eight-step warmup/reset. All modes use
ETDRK4/stress, PAO, K0=10, seed 2, Re=27898.36614974335 and CFL 3. Overhead means
`100 * (GUI wall ms/step / headless wall ms/step - 1)`; UI rendering time and
snapshot costs are included in the measured controller run.

See [the validation report](evidence/REPORT.md) for exact commands, dependency
versions, numerical results, measured performance, and the captured native UI.

## Presentation limits

This implements **X11/GLX**, running through **XWayland on Wayland**. It shares
the existing validated CUDA/OpenGL path, without a second native Wayland or
Vulkan renderer. A working X11/XWayland display backed by the same NVIDIA GPU
is required; failures are reported rather than silently using software rendering.
Headless CUDA remains display-independent. WSLg interop is still unvalidated.

The available validation desktop was a GNOME remote Wayland session. Its GPU
work is local to the RTX 3090, but its presentation travels over remote desktop.
Those GUI timings characterize that session and cannot establish local-monitor
scanout performance. Physical-console rendering performance remains unmeasured.
