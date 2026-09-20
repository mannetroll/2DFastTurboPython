# Native Linux GUI refinement — 2026-09-20

Branch: `IMGUI_LINUX`. The Linux presentation was replaced to follow `GUI.png`.
The CUDA/VkFFT core, generated controller adaptations, shared renderer, numerical
fixtures and benchmark workload were not changed.

## Implementation

| File | Change |
| --- | --- |
| `native-linux/main.cpp` | Five menus and shared action handlers, editor routing, PNG path/overwrite dialogs, screenshot-defined header/inspector/overlays/metrics, centered square field, focus and DPI handling |
| `native-linux/presentation.hpp` | Compact widgets, full-width palette gradients, vector icons, fonts and scaled theme measurements |
| `native-linux/x11_imgui.{hpp,cpp}` | DPI resource query, window actions, cached EWMH hidden state, actual X11 keyboard/wheel test events |
| `native-linux/tests/desktop_checks.inc` | 275 ordered desktop checks with real X11 input and GPU texture validation; captures and layout geometry assertions |
| `native-linux/tests/cli.cmake` | Invalid window dimensions and scale rejection |
| `native-linux/CMakeLists.txt` | Desktop timeout and four separate close/quit lifecycle cases |
| `native-linux/scripts/validate.py` | Reproducible desktop, numerical, dependency and performance commands with archived outputs |
| `README.md`, `native-linux/README.md` | Layout, interaction, lifecycle, setup and validation documentation |

The pre-existing untracked `theme.hpp` was reused without changing its bytes.
Its saved copy and SHA-256 comparison are in `before/user-theme.hpp` and
`baseline-hashes.txt`. The pre-existing `evidence/windows-style/` directory and
all earlier evidence remain untouched. Failed refinement iterations are also
retained under `validation-1` through `validation-9`.

The application still executes:

`CUDA/VkFFT worker → immutable GPU frame → existing CUDA/OpenGL interop → GL texture → Dear ImGui → X11/GLX / XWayland`.

No extra solver synchronization or normal-frame CPU readback was introduced.
The original presentation cadence, visualization interval, frame leases, controller
benchmark warmup/reset and solver/rendering separation remain. Readback occurs
only for explicit PNG export, screenshot capture or acceptance pixel checks.
The minimization fix observes `_NET_WM_STATE_HIDDEN`: GNOME/XWayland may keep a
minimized window mapped, which the old map-state-only visibility check missed.

## Visual comparison and evidence

Use [the side-by-side comparison](comparison.html) to select raw captures.
The images are not retouched or synthesized.

| Evidence | Path |
| --- | --- |
| Primary specification | [`GUI.png`](../../../GUI.png) |
| Before, 1280×720 | [`before/1280x720.png`](before/1280x720.png) |
| Before, initial oversized request, clamped by GNOME | [`before/1920x1080.png`](before/1920x1080.png) — actual 1511×981 |
| Before, full 1920×1080 framebuffer | [`before/1920x1080-exact.png`](before/1920x1080-exact.png) |
| Final, comparable reference proportions | [`final-tests-2/gui/reference-proportions.png`](final-tests-2/gui/reference-proportions.png) — 940×900, paused |
| Final, running | [`final-tests-2/gui/reference-running.png`](final-tests-2/gui/reference-running.png) |
| Final, 1280×720 | [`final-tests-2/gui/1280x720.png`](final-tests-2/gui/1280x720.png) |
| Inspector scrolled | [`final-tests-2/gui/1280x720-scrolled.png`](final-tests-2/gui/1280x720-scrolled.png) |
| Final, 1920×1080 | [`final-tests-2/gui/1920x1080.png`](final-tests-2/gui/1920x1080.png) |
| Focus | [`final-tests-2/gui/1280x720-focus.png`](final-tests-2/gui/1280x720-focus.png) |
| 150% DPI | [`final-tests-2/gui/1920x1080-dpi150.png`](final-tests-2/gui/1920x1080-dpi150.png) |
| 150% DPI, focus | [`final-tests-2/gui/1920x1080-dpi150-focus.png`](final-tests-2/gui/1920x1080-dpi150-focus.png) |
| Maximized | [`final-tests-2/gui/maximized.png`](final-tests-2/gui/maximized.png) |
| Menu captures | [`File`](final-tests-2/gui/menu-File.png), [`Edit with editor`](final-tests-2/gui/menu-edit-active.png), [`Edit without editor`](final-tests-2/gui/menu-Edit.png), [`Simulation`](final-tests-2/gui/menu-Simulation.png), [`View`](final-tests-2/gui/menu-View.png), [`Window`](final-tests-2/gui/menu-Window.png) |
| Keyboard menu navigation / focus menu | [`keyboard`](final-tests-2/gui/menu-keyboard.png), [`focus`](final-tests-2/gui/focus-menu.png) |
| About / PNG export | [`About`](final-tests-2/gui/about.png), [`exported field`](final-tests-2/gui/snapshot.png) |

`GUI.png` was opened before edits. macOS `FastTurboView.swift` and
`FastTurboApplication.swift`, Windows `app.cpp` and `desktop_ui.cpp`, and Python
`app.py` were inspected for layout, menus, state handling and parameter behavior.
Direct image comparisons used the primary screenshot, Windows
`reference-windows/benchmarks/2026-09-15/windows-gui.png` and Python Linux
`python/evidence/linux-clean-build/clean-gui.png`.
No separate native SwiftUI application screenshot was available; its comparison
uses source plus the supplied macOS window capture. The other platform apps were
not modified or rerun on their respective operating systems.

The final layout uses a 196-logical-pixel inspector, 18-pixel gap, 77-pixel header,
34-pixel field strip and 54-pixel metrics strip, plus Linux's integrated menu bar.
The inspector follows K0, spectrum, seed, N, Re/regulation, the three fields,
four palettes, range/contrast, velocity direction, then Method/Form/CFL.
Linux-specific presentation controls follow these shared controls. The field
remains square and centered in the available viewport, including letterboxing
on wide windows; its entire domain remains visible. Seed, transport and time are
positioned relative to the image. Final field captures use vorticity/Ember,
N=2048, K0=10, seed 2, PAO/stress and Re≈54095, evolved to approximately t=0.355.
Statistics are real backend values; they are not copied from the specification.

Actual geometry from [`desktop.json`](final-tests-2/gui/desktop-test.json):

| Client size / mode | Image x,y | Square side | Scale |
| --- | ---: | ---: | ---: |
| 1280×720 | 276,132 | 514 | 1.0 |
| 1280×720 focus | 352,132 | 576 | 1.0 |
| 1920×1080 | 416,132 | 874 | 1.0 |
| 1920×1080 high DPI | 414,198 | 770 | 1.5 |
| 1920×1080 high DPI focus | 528,198 | 863 | 1.5 |
| Maximized 1511×981 | 261,132 | 775 | 1.0 |
| Reference proportions 940×900 | 22,138 | 682 | 1.0 |

The available GNOME remote Wayland desktop was 1578×1050 on `DISPLAY=:0`,
`WAYLAND_DISPLAY=wayland-0`. Ordinary initial 1920×1080 requests were constrained
by GNOME. The acceptance test temporarily sets a 1920×1080 minimum-size hint to
obtain a real oversized XWayland client/framebuffer; part lies beyond the visible
remote desktop. This is real NVIDIA OpenGL rendering, not a physical 1920×1080
monitor test. High DPI was explicitly exercised at 1.5× on that framebuffer;
automatic Xft DPI detection ran at the session's normal scale. Per-monitor
fractional-DPI transitions were not available to test. Captures contain the client
area; normal Linux OS decorations remain owned by the window manager.

The current executable on disk at task start contained a newer presentation than
the tracked `main.cpp`. The baseline executable and `2dbench` were copied to
`/tmp/fastturbo-parity-before` before implementation. Fresh baseline performance,
1280×720 and the initial clamped oversized capture were taken before implementation.
The supplementary full-size baseline screenshot was captured later from that
unchanged saved executable using `before/window-sizing.cpp`, the same minimum-hint
approach used by the final test. Its hash is retained in `baseline-hashes.txt`.

## Validation commands and results

```sh
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=86 -DFASTTURBO_NATIVE_GUI_TESTS=ON
cmake --build build-linux -j2
python3 native-linux/scripts/validate.py desktop \
  --output native-linux/evidence/gui-parity-20260920/validation-10
python3 native-linux/scripts/validate.py tests \
  --output native-linux/evidence/gui-parity-20260920/final-tests-2
python3 native-linux/scripts/validate.py performance \
  --output native-linux/evidence/gui-parity-20260920/after
```

Release build completed without warnings. The final CTest GUI run passed **275
checks**, including transport stability, three distinct field textures, all
palettes, sigma/exposure/velocity, live Re/CFL, regulation, all integrators and
CNAB2 constraints, configuration resets, every menu action except process exit
(covered separately), keyboard navigation/shortcuts, text and numeric clipboard/
undo/redo, preventing simulation shortcuts during editing, real PNG export and
overwrite/cancel, visibility gating, scrolling, focus, resize, DPI and maximization.
It closed via `WM_DELETE_WINDOW` while the solver was running.

**Native CTest: 13/13 passed**, including controller/direct-solver agreement,
headless reference/benchmark checks, GUI CLI validation, the 275-check desktop
run and four separate close/quit cases. See
[`ctest.log`](final-tests-2/ctest.log) and [`ctest.xml`](final-tests-2/ctest.xml).
The earlier startup Ctrl+W timeout is retained under `final-tests/`; the test
now activates its window and lets focus settle before injecting the shortcut.
All four lifecycle paths also passed independently under `lifecycle-2/`.

**Python/reference parity: 133 passed, 18 skipped in 155.78 s.** The skips are
six Metal/macOS CNAB2 cases and twelve Windows-only cases on Linux; none were
added or relaxed. This includes the existing Python GUI/pixel checks and
canonical `reference-linux` comparisons. See
[`pytest.log`](final-tests-2/pytest.log) and [`pytest.xml`](final-tests-2/pytest.xml).

The [`ldd` audit](final-tests-2/ldd.log) contains X11/XCB, libpng/zlib,
OpenGL/GLX/GLdispatch, CUDA runtime/driver/NVRTC and standard C/C++ libraries.
There is **no Python, Qt, PySide6 or GTK runtime linkage**. The application
reported `NVIDIA GeForce RTX 3090/PCIe/SSE2` as the OpenGL renderer and
CUDA/VkFFT as its backend. CUDA runtime is 13.3; driver is 595.91.07;
VkFFT remains 1.3.4 and Dear ImGui remains pinned at 1.92.5.
The existing development packages are `libx11-dev`, `libgl-dev`, `libglx-dev`,
`libpng-dev`, the CUDA Toolkit, and the C++20/CMake toolchain. No packages,
drivers or runtime libraries were installed or changed.

[`environment.json`](final-tests-2/environment.json),
[`xrandr.log`](final-tests-2/xrandr.log), [`xresources.log`](final-tests-2/xresources.log)
and [`nvidia-smi.log`](final-tests-2/nvidia-smi.log) record the actual environment.
`Xft.dpi` was 96 (scale 1.0).

Exact invoked commands, return codes and elapsed times are retained in
`validation-10/commands.json`, `final-tests-2/commands.json` and `after/commands.json`.
The native controller test is display-independent and compares all four methods
to the direct production solver. Existing numerical/parity tolerances and tests
were not changed.

## Performance

The fresh baseline and after runs use the same RTX 3090, display session,
1032×900 client size, scale 1.0, 100 ms visualization interval, ETDRK4/stress,
PAO, seed 2, K0=10, Re=27898.36614974335, CFL 3 and batch 8. Each size/mode has
three ten-second runs, with rotating order. Headless clears display environment
variables. GUI warmup is the existing two-second controller warmup/reset;
headless warmup is the existing eight steps/reset. No GPU tests ran concurrently.
The image's size differs because the presentation changed; the window and
visualization cadence are held fixed.

Baseline command:

```sh
python3 native-linux/scripts/compare.py --build /tmp/fastturbo-parity-before \
  --output native-linux/evidence/gui-parity-20260920/before/performance \
  --runs 3 --seconds 10 --sizes 2048 4096
env -u DISPLAY -u WAYLAND_DISPLAY uv run --project python 2dbench --seconds 10 --json
```

Median wall measurements (three runs per cell):

| N | Mode | Before ms/step | After ms/step | Before steps/s | After steps/s | Change | Overhead before → after |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2048² | Headless | 5.446 | 5.463 | 183.63 | 183.06 | +0.31% | 0.00% → 0.00% |
| 2048² | GUI, visualization off | 5.454 | 5.476 | 183.34 | 182.62 | +0.40% | 0.16% → 0.24% |
| 2048² | GUI, normal visualization | 5.571 | 5.591 | 179.49 | 178.85 | +0.36% | 2.31% → 2.36% |
| 4096² | Headless | 24.233 | 24.334 | 41.27 | 41.09 | +0.42% | 0.00% → 0.00% |
| 4096² | GUI, visualization off | 24.304 | 24.476 | 41.15 | 40.86 | +0.71% | 0.29% → 0.58% |
| 4096² | GUI, normal visualization | 24.943 | 25.039 | 40.09 | 39.94 | +0.38% | 2.93% → 2.90% |

No material regression: normal-visualization wall cost changed by about +0.36%
at 2048² and +0.38% at 4096². Headless changed by about +0.31% and +0.42%,
respectively. Normal GUI overhead remains approximately 2.36% / 2.90%. The
small common movement in headless and GUI results is consistent with run-to-run
variation, not a new GUI bottleneck. All modes stayed within 0.8% of their baseline.

Raw measurements and ranges: [`before`](before/performance/comparison.json),
[`after`](after/performance/comparison.json), [`before/after table`](before-after.json).
The comparison runner verifies real positive VkFFT allocation and work, CUDA/OpenGL
uploads in normal mode, and zero snapshots/uploads with visualization disabled.

The required display-free `uv run --project python 2dbench --seconds 10 --json`
completed successfully at both sizes, also without material regression:

| N | Before ms/step | After ms/step | Change |
| --- | ---: | ---: | ---: |
| 2048² | 5.418 | 5.429 | +0.21% |
| 4096² | 24.195 | 24.300 | +0.43% |

Raw [`before JSON`](before/2dbench.json), [`after JSON`](after/2dbench.json) and
[`after command/build log`](after/2dbench.log) are retained. These are single
verification runs; the repeated native measurements above are the primary
GUI-overhead comparison.

These are real local RTX 3090 solver/GPU timings in a GNOME remote desktop
session. They do not measure physical-monitor scanout or RDP client frame rate.
Throughput and submitted GUI FPS are reported separately.

## Remaining differences and untested requirements

- ImGui controls and Ubuntu/Ubuntu Mono/DejaVu title fonts differ subtly from
  SF/SwiftUI, Qt and Win32 glyph rasterization, popup styling and text metrics.
  The transport uses a translucent purple surface; it does not reproduce macOS
  material blur. OS chrome/menu placement follows Linux conventions.
- Save Snapshot uses an ImGui filename/path dialog with overwrite confirmation,
  backed by the existing PNG exporter, rather than a platform file-browser dialog.
  It exports the displayed field at 1800×1800, matching the reference default,
  with no new GUI toolkit.
- Close Window and Quit both terminate this single-window Linux application.
  Windows retains a tray host and macOS retains an application/dock lifecycle;
  this Linux implementation has neither. Minimize preserves the running worker.
- ImGui's undo groups replacement deletion and insertion separately. Native
  ImGui keyboard and Edit menu behavior use the same history and clipboard.
- GPU allocation displays solver plus VkFFT memory and says so in its tooltip;
  the unchanged renderer API does not expose its allocation. Measured CN is not
  available from the existing backend, so only configured CFL is shown.
- Native macOS/Windows execution, physical-console/RDP scanout comparisons and
  per-monitor DPI transitions were not tested here. Native SwiftUI visual
  comparison was limited to source; the supplied macOS capture is the visual
  authority. WSLg remains outside this native Ubuntu validation.
