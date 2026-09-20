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
with palinstrophy regulation off. Its presentation follows the repository's
[`GUI.png`](../GUI.png): an application header, square field centered in the
space beside a 196-logical-pixel right inspector, field legend, seed/time and
transport overlays, and eight bottom metrics. The inspector scrolls independently.

K0, spectrum, N, method and form selections reset immediately. Seed commits on
Enter (Escape cancels editing). Reynolds and CFL are live edits and do not reset.
Ctrl+click a slider for numeric entry. Grid/K0 changes retain the controller's
Reynolds recalibration rule. CNAB2 caps CFL at 0.25 and disables Advective; its
power-of-two N and seed 1..5010 restrictions are unchanged. Invalid edits show a
dismissible message. No numerical behavior is implemented in the widgets.

Fields appear as Enstrophy, Vorticity, Speed; palettes as Inferno, Ember, Aurora,
Glacier. Vorticity offers Sigma or Exposure; the other fields offer Exposure.
Velocity direction requests the existing velocity frame/flow renderer.
The PRESENTATION section (Show simulation, Update interval and rendering FPS)
is available only in Debug builds (`NDEBUG` undefined); the normal inspector ends
with NUMERICS and CFL.
The interval defaults to 100 ms (4..10000 ms). Disabling visualization or minimizing
suppresses snapshots while stepping continues. Re-enabling while paused refreshes
the current field.

The integrated menu bar stays above the header, including in focus mode:

| Menu | Actions / shortcuts |
| --- | --- |
| File | Save Snapshot… (Ctrl+S), Close Window (Ctrl+W), About 2DFastTurbo, Quit 2DFastTurbo (Ctrl+Q) |
| Edit | Undo (Ctrl+Z), Redo (Ctrl+Y), Cut/Copy/Paste (Ctrl+X/C/V), Select All (Ctrl+A) |
| Simulation | Pause/Resume (Space), Single Step (Right), Reset Flow (R), New Initial Condition (Ctrl+Shift+R) |
| View | Focus on Flow / Show Controls (F), Show Simulation, Dismiss Message |
| Window | Minimize (Ctrl+M) |

Alt enters menu navigation; arrows and Enter navigate/activate, Escape dismisses.
Edit commands return to the active text/numeric editor with its selection and undo
history. Plain simulation shortcuts are suppressed during editing, popup handling,
and widget keyboard navigation. Buttons, menu items and shortcuts share handlers.
Pause finishes the current batch; Single Step advances exactly once.

F hides the inspector and metrics while retaining transport, the header and menus.
Show Simulation restores the window and enables visualization. Both Close Window
and Quit terminate this single-window Linux process, joining its worker cleanly;
there is no tray/dock host to reopen a closed process. This differs from Windows'
tray and macOS's application lifecycle and matches the Python Linux close action.

Save Snapshot opens an ImGui path dialog, with overwrite confirmation, and uses the
existing renderer's PNG export of the displayed frame and display settings. It is
an explicit GPU readback, never part of normal rendering. The field exports at 1800×1800 pixels, matching the reference default. The dialog needs no Qt/GTK runtime.

The metrics retain the reference order: simulation time per wall second
(`R_turbo`), initial turnovers per wall second, kinetic energy/history, enstrophy,
timestep, solver steps/s, step, GPU allocation. The reference's FPS heading has the
explicit unit **steps/s**; its tooltip gives **ms/step** and separate rendering FPS.
About also exposes these values. GPU allocation is labeled in its tooltip as solver
plus VkFFT memory; the existing renderer does not expose its allocation. CFL is a
configured target; the backend does not expose a measured CN.

Ubuntu and Ubuntu Mono system fonts are used when available, with system font and
ImGui fallbacks. Xft.dpi determines desktop scale, checked once per second; no font
files are bundled. Override scale and initial size for reproducible checks:

```sh
./fastturbo-linux --width 1280 --height 720
./fastturbo-linux --width 1920 --height 1080 --ui-scale 1.5
./fastturbo-linux --focus
```

Fonts, geometry, hit targets and scrolling scale together. XWayland exposes a
session-wide Xft DPI; compositor-specific per-monitor fractional scaling is outside
this X11 host's interface. The window manager may constrain initial window sizes.

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

`main.cpp` handles controls, menus, frame selection and telemetry; `presentation.hpp`
provides DPI-aware drawing over the preserved `theme.hpp`.
`tests/desktop_checks.inc` drives the production widgets using X11 events.
`x11_imgui.cpp` forwards X11 input and window-manager messages to ImGui.
The existing renderer gains a small optional desktop interface in
`python/native/linux/render_api.hpp` and `renderer.cpp`; its embedded and PNG
entry points retain their behavior. No field shader is changed.

The renderer checks `cudaGLGetDevices` against the selected CUDA device. Its
existing map/copy/unmap sequence makes a device-to-device copy into a persistent
OpenGL buffer. The frame lease stays alive until the separate interop stream
has completed; only that stream is waited on for an upload. The solver keeps
its own stream, persistent plans and buffers. The normal GUI path performs no
full-field or pixel CPU readback. Explicit PNG export, `--capture` and `--self-test` perform pixel readback only
on request.

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
python3 native-linux/scripts/validate.py desktop --output native-linux/evidence/my-validation/desktop
python3 native-linux/scripts/validate.py tests --output native-linux/evidence/my-validation/tests
python3 native-linux/scripts/validate.py performance --output native-linux/evidence/my-validation/performance
```

The self-test sends actual X11 pointer and keyboard events through ImGui. It
checks field/palette pixels, paused display updates, live edits and resets, all
methods, menu actions and keyboard navigation, text/numeric editor clipboard and
undo, snapshot export/overwrite, scrolling, focus, DPI, resize, minimize/restore
and `WM_DELETE_WINDOW` while running. Separate CTest cases verify File Close,
File Quit, Ctrl+W and Ctrl+Q. ImGui retains its own undo grouping: replacing a
selection records deletion and insertion separately. A closed process with exit status zero is part of the
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

See [the GUI refinement report](evidence/gui-parity-20260920/REPORT.md) for current
commands, screenshots, numerical results, before/after performance and limitations.
The [earlier validation report](evidence/REPORT.md) and previous evidence are retained.

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
