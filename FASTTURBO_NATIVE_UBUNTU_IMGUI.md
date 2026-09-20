# Task: Maintain Native Ubuntu/Linux FastTurbo and Match All Four GUIs

Work directly in the existing `2DFastTurboPython` repository.

The project already has a native Linux Dear ImGui application, a validated Linux CUDA/VkFFT implementation, a headless `reference-linux`, Python/PySide6 application support, cross-platform `2dbench`, and existing Linux rendering/interoperability work. Refine the existing `./fastturbo-linux` application.

Do not redesign the numerical solver.

The objective is a **native Linux GUI application** for Ubuntu using C++/CUDA, Dear ImGui, OpenGL, and VkFFT, with no Python, PySide6, or Qt runtime dependency, matching the macOS, Windows, and Python applications as closely as possible.

For the current UI refinement, read [FASTTURBO_LINUX_IMGUI_WINDOWS_STYLE.md](FASTTURBO_LINUX_IMGUI_WINDOWS_STYLE.md) alongside this document. It provides the detailed shared layout, control placement, screenshot comparison, and acceptance requirements. Keep changes confined to the Linux frontend and associated UI tests/documentation.

Target: native Ubuntu 26.04 with NVIDIA RTX 3090.

## 1. Objective

Keep all four interfaces as equal as possible in layout, visual appearance, controls, and interaction behavior while retaining their existing platform backends:

```text
macOS    SwiftUI             Metal + VkFFT
Windows  Win32 / Direct2D    CUDA + VkFFT + Direct3D
Python   PySide6             existing platform-native backends
Linux    Dear ImGui          CUDA + VkFFT + OpenGL
```

The Linux GUI must use the existing validated CUDA/VkFFT production solver. Do not implement another solver.

## 2. No Numerical Duplication

Do not duplicate or rewrite CUDA kernels, VkFFT integration, ETDRK4, CNAB2, timestep/controller logic, dealiasing, spectral operations, initialization, PAO initialization, GPU memory ownership, or benchmark implementation.

The Linux GUI is a frontend/rendering integration task, not a numerical solver task. Existing numerical parity must remain unchanged.

## 3. Preserve Existing Working Paths

Do not break or substantially modify:

```text
reference-linux/
python/native/cuda/
python/native/linux/
uv run 2dbench
legacy PAO/CNAB2 reference mode
Python/PySide6 application
Windows implementation
macOS implementation
```

The numerical backend must remain usable with no display.

## 4. Inspect Before Implementing

Inspect the native CUDA/Linux code, Python application, reference implementations, scripts, evidence, `ARCHITECTURE.md`, and README before changing files.

Open [GUI.png](GUI.png) in the repository root. This user-supplied screenshot is the primary visual specification for the application content: layout, proportions, control order/grouping, typography, colors, overlays, and bottom statistics. Follow its detailed visual requirements in [FASTTURBO_LINUX_IMGUI_WINDOWS_STYLE.md](FASTTURBO_LINUX_IMGUI_WINDOWS_STYLE.md).

Inspect the four layout implementations explicitly:

- `reference-macos/2DFastTurbo/Sources/FastTurboApp/FastTurboView.swift`
- `reference-windows/src/app.cpp` (`App::draw()` and `App::drawInspector()`)
- `python/src/fastturbo/app.py` (`MainWindow.__init__()` and `_inspector()`)
- `native-linux/main.cpp` (`Application::frame()` and `controlsUI()`)

Also inspect the menu implementations: `installMenus()` and `validateMenuItem(_:)` in `reference-macos/2DFastTurbo/Sources/FastTurboApp/FastTurboApplication.swift`, `createDesktopMenus()` in `reference-windows/src/desktop_ui.cpp` and its handlers in `app.cpp`, and `_menus()` in `python/src/fastturbo/app.py`.

Use `GUI.png` as the visual authority and the macOS/Windows/Python source implementations for behavior and details absent from the image. For those absent details, use the common design and resolve minor visual differences using native Windows. Capture the current Linux layout and performance before editing.

In particular inspect the existing Linux work for OpenGL/Vulkan interop, EGL, Wayland, X11/XCB, CUDA/OpenGL interoperability, rendering, GUI validation, and headless execution. Reuse validated infrastructure.

## 5. Dear ImGui

Retain upstream Dear ImGui as the native Linux GUI layer and preserve the existing pinned dependency/build integration.

The native GUI must not depend at runtime on Python, CPython, PySide6, Qt, GTK, Electron, or a browser runtime.

## 6. Windowing

Retain the existing X11/GLX windowing and input integration, including XWayland on Wayland desktops. This UI refinement requires no replacement window toolkit or rendering API.

Do not create separate complete X11 and Wayland GUI implementations.

## 7. Rendering

Keep the existing GPU-resident rendering path:

```text
CUDA solver
→ GPU-resident field
→ CUDA/OpenGL interop
→ OpenGL texture
→ Dear ImGui image
→ display
```

Avoid full-field GPU→CPU→GUI→GPU copies every frame. Reuse the existing validated CUDA/OpenGL interop path without additional transfers or synchronization.

## 8. Preserve Solver Performance

Keep persistent VkFFT plans, GPU-resident state, native batching, existing synchronization strategy, and optimized production execution path.

Do not synchronize after every simulation step merely for GUI convenience. Preserve the existing configurable rendering/snapshot cadence and solver/rendering decoupling. Layout changes must not couple solver stepping to monitor refresh rate or introduce expensive per-frame allocations.

## 9. Headless Baseline

`uv run 2dbench` is the performance baseline. Existing RTX 3090 Ubuntu results are approximately:

```text
2048² → 3072²   ~4.85 ms/step   ~206 steps/s
4096² → 6144²  ~21.69 ms/step    ~46 steps/s
```

These are reference measurements, not hardcoded expectations. Measure GUI overhead.

## 10. GUI Parity Across All Four Applications

All four applications—native macOS/SwiftUI, native Windows, Python/PySide6, and native Linux/Dear ImGui—should be as equal as possible in layout, visual appearance, controls, and interaction behavior. Use the same centered simulation image and compact right-hand controls pane, with consistent visual hierarchy, spacing, colors, typography, and status presentation. Allow differences only where required by platform capabilities or native OS conventions.

Replace the current 340-pixel left controls pane and statistics-above-image arrangement with:

- An application menu bar across the top of the Linux window's client area, below the OS title bar and above the header, containing **File, Edit, Simulation, View, and Window**, in that order.
- The reference header with application/environment information, backend, focus, and export actions where supported by existing backend APIs.
- A dominant square simulation image centered within the viewport area beside the inspector, with the complete domain visible and no cropping or stretching.
- A compact **right-hand inspector**, matching shared control order and placement. Start from the references' 196-logical-pixel width and 18-logical-pixel gap, scaled for DPI.
- The reference field label, color legend, grid label, seed/time overlays, and transport controls near the bottom of the simulation view.
- A compact bottom statistics strip, with detailed device diagnostics available separately.

Use `GUI.png` and the actual reference source as the layout authority, not ASCII diagrams or simplified mockups. Reproduce the screenshot's inspector order, stacked field buttons, gradient palette buttons, overlays, and metric styling. The screenshot excludes the global macOS menu bar; Linux must still provide the required in-window menu bar above the pictured header. Keep Linux OS chrome and actual Linux GPU/backend/environment labels.

Implement the menu hierarchy and action list in the **Application menus** section of [FASTTURBO_LINUX_IMGUI_WINDOWS_STYLE.md](FASTTURBO_LINUX_IMGUI_WINDOWS_STYLE.md). Include snapshot/close/About/quit, editing actions, simulation transport/reset/new initial condition, focus/show simulation/dismiss message, and minimize. Match separators, shortcuts, dynamic labels, enabled/disabled states, and any checked states. Menu actions must share handlers/state with corresponding buttons and shortcuts. Keep menus accessible in focus mode, direct editing actions to the active editor, and prevent simulation shortcuts from consuming text/numeric input. Use real existing backend/platform operations, and document any necessary Close Window/Quit lifecycle differences or unsupported actions; no placeholder actions or silent omissions.

Keep shared advanced settings in their corresponding inspector locations. Do not replace the inspector with a bottom toolbar or move these settings into a separate popup. Place Linux-specific controls unobtrusively within the inspector while preserving the order of shared controls.

Retain resolution/N, Reynolds number, palinstrophy regulation, CFL, K0, seed, method, nonlinear form, start spectrum, field/colormap selection, exposure/sigma, flow overlay, visualization enable/disable, and update interval. Match labels, palette previews, selected states, and keyboard shortcuts. Provide Run/Pause, Single Step, Reset, and reference focus mode; focus mode hides the inspector and metrics while keeping transport usable and the domain visible.

Match immediate display changes, live parameter edits, and reset-triggering configuration changes using existing backend APIs. Preserve method-specific numerical constraints. Report any behavior that cannot match without backend changes, rather than changing solver/controller semantics. Native modifier keys and OS chrome may follow platform conventions.

Keep iteration, simulation T, DT, configured CFL, steps/s, ms/step, `R_turbo`, GPU/device, and backend accessible in the corresponding reference status/header areas. Show measured CN only if the existing backend exposes it, clearly distinguished from configured CFL.

Verify 1280×720, 1920×1080, maximized, focus-mode, and large/high-DPI layouts. Keep the image square and centered as the window changes, the inspector compact and scrollable, and every control reachable. Compare screenshots directly against `GUI.png` and other available macOS, Windows, and Python evidence, allowing for Linux menu bar/OS chrome placement, and iterate until the specified layout is reproduced.

## 11. Visualization

Support existing useful visualization fields such as vorticity, velocity magnitude, enstrophy, and other already implemented fields. Reuse existing rendering and color-mapping logic.

Preserve established Aurora/Ember/Glacier/Inferno, exposure, and sigma behavior where applicable.

## 12. Performance Metrics

Simulation throughput and rendering FPS are different quantities. Report at least ms/step and steps/s. Prefer:

```text
R_turbo = ΔT_sim / ΔT_wall
```

if available without disturbing the solver. Label rendering FPS separately if shown.

## 13. GUI/Solver Decoupling

Keep simulation, render extraction/interoperability, and GUI logically separate. Do not embed numerical solver logic directly in ImGui callbacks.

## 14. Build and Runtime

Integrate cleanly with existing CMake. A desired workflow is roughly:

```bash
cmake -S . -B build-linux   -DCMAKE_BUILD_TYPE=Release   -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build-linux -j
```

Adapt to the repository.

The resulting native executable should launch directly, e.g.:

```bash
./fastturbo-linux
```

Python may assist build/tests but must not be a runtime dependency.

## 15. Ubuntu/NVIDIA Environment

Target Ubuntu 26.04, NVIDIA 595-series working driver stack, CUDA 13.x, VkFFT 1.3.4, RTX 3090.

Do not modify or downgrade the working NVIDIA/CUDA installation. If development packages are required for ImGui/OpenGL/windowing, list them explicitly and avoid broad package changes.

## 16. Wayland/XWayland

Preserve the existing X11/GLX path and XWayland support on Wayland desktops. Prioritize correctness, maintainability, GPU interoperability, and performance. Document the actual presentation path.

## 17. Remote Desktop

RDP may be used for functional testing, but not as the authoritative rendering benchmark. Distinguish solver performance, GUI rendering performance, and remote-display performance.

## 18. Tests

Preserve all existing tests and verify at least:

1. Native Linux application builds.
2. CUDA device initializes.
3. VkFFT initializes.
4. Solver initializes.
5. GUI starts.
6. Run/Pause/Reset/Single Step work.
7. Field visualization updates.
8. Resize does not crash.
9. Window close exits cleanly.
10. Headless solver still works without a display.
11. Existing parity tests remain unchanged.
12. `uv run 2dbench` performance remains within normal variation.
13. Field/colormap, exposure/sigma, flow overlay, visualization toggle, and update interval still work.
14. Parameter edits, regulation, and reset behavior match the references wherever the existing backend supports them.
15. The centered square image, right-hand inspector, transport, and bottom metrics match the references at the required sizes, with working scrolling, focus mode, and keyboard actions.
16. Reference export actions work where supported by existing backend APIs; any gap is documented.
17. All five menus work, including keyboard navigation, action states, shortcuts during text/numeric editing, and menu access in focus mode. Verify Close Window and Quit against the documented Linux lifecycle behavior.

Do not require an interactive GUI for ordinary numerical unit tests.

## 19. Performance Validation

Measure before and after frontend changes under identical hardware, solver settings, window size, visualization interval, and display conditions. Compare:

```text
headless
native GUI with visualization disabled/minimal
native GUI with normal visualization frequency
```

Report ms/step, steps/s, and percentage overhead using the real production solver path.

Investigate and fix material regressions. Treat historical measurements as context, and use repeated measurements to distinguish regressions from normal noise.

## 20. Avoid Premature Vulkan Rewrite

Do not replace working OpenGL interop with Vulkan merely because Vulkan is newer. Use the simplest validated path. Document Vulkan only as a future direction if there is demonstrated need.

## 21. Dependency Audit

Run `ldd` on the executable. Confirm no runtime linkage to Python, Qt, PySide6, or GTK. CUDA/NVIDIA/OpenGL/windowing/system C/C++ libraries are expected.

## 22. Documentation

Document native Linux GUI architecture, dependencies, Ubuntu setup, build/run commands, backend, presentation path, CUDA/OpenGL interoperability, relationship to headless execution, performance, and limitations.

Save before/after screenshots and final 1280×720 and 1920×1080 layouts under `native-linux/evidence/`, with high-DPI and focus-mode evidence where available. Include the menu bar and additional open-menu captures showing contents and states. Record display scaling and comparison evidence against the other three applications. Use source inspection when reference screenshots are unavailable, and state which visual comparisons could not be completed. Preserve previous evidence with new filenames.

Keep `reference-linux` as the canonical headless validation/reference executable.

## 23. Architecture

Update `ARCHITECTURE.md` only as needed. Conceptually:

```text
                       FastTurbo
                           |
              shared numerical mathematics
                           |
          +----------------+----------------+
          |                                 |
       Metal                            CUDA/VkFFT
          |                                 |
       macOS                      +----------+----------+
                                  |                     |
                               Windows                Linux
                                  |                     |
                              native GUI          +-----+-----+
                                                  |           |
                                               headless     ImGui
                                                  |           |
                                             reference     OpenGL
```

Do not overstate sharing where platform implementations differ.

## 24. Keep PySide6

Do not remove the working Python/PySide6 Linux application. This task refines the existing native ImGui application. Any later simplification requires separate validation.

## 25. Git Discipline

Inspect the working tree before editing and preserve existing user changes. Work incrementally on the existing branch. Avoid unrelated macOS/Windows/Python changes. Do not rewrite existing evidence. Create separate native-Linux-GUI evidence.

## 26. Execution Policy

This task requires real GPU execution. If a sandbox blocks `/dev/nvidia*`, CUDA, OpenGL, the display server, or profiling, obtain permission to execute the required commands outside the sandbox.

Never fall back to CPU, replace VkFFT, fake GUI execution, or claim GPU validation without real execution.

If required hardware, display access, or reference screenshots are unavailable, complete independent work and report the exact validation gap. Source inspection alone does not establish visual parity or GPU validation.

## 27. Final Verification

Before finishing:

1. Build Release.
2. Run existing numerical/parity tests.
3. Run native Linux tests.
4. Launch native GUI on the real Ubuntu desktop.
5. Verify RTX 3090 CUDA execution.
6. Verify VkFFT execution.
7. Verify the preserved CUDA/OpenGL interop path.
8. Exercise Run/Pause/Step/Reset.
9. Exercise field switching.
10. Exercise resize/close, inspector scrolling, focus mode, and keyboard actions at the required window sizes and display scaling.
11. Verify headless execution remains display-independent.
12. Run `uv run 2dbench`.
13. Compare solver performance before/after.
14. Run `ldd`.
15. Record exact commands/results.
16. Verify the remaining display/parameter controls and reference interaction behavior using existing backend APIs.
17. Capture final screenshots, compare with available macOS/Windows/Python evidence, and record remaining differences or untested requirements.
18. Exercise all menus, their shortcuts and state updates, editing focus, and window lifecycle actions; capture open-menu evidence.

## 28. Final Report

Report:

### Implementation
Files created/modified and architecture.

### GUI parity
Layout and behavior reproduced from macOS, Windows, and Python; before/after and final screenshot paths; comparison evidence; and concrete reasons for any remaining platform differences.

### Native dependencies
Exact libraries/packages.

### Rendering path
Actual implemented path, e.g. CUDA → CUDA/OpenGL interop → OpenGL texture → Dear ImGui → window.

### Numerical validation
Parity/test results after changes.

### Performance

```text
Mode                         ms/step      steps/s      overhead
Headless                     ...          ...          baseline
Native GUI / low update      ...          ...          ... %
Native GUI / normal update   ...          ...          ... %
```

### Dependency audit
Summarize `ldd`.

### Display environment
Report Wayland/XWayland/X11/RDP as applicable and distinguish local GPU execution from remote presentation.

### Remaining issues
Concrete remaining issues and any requirements or comparisons that could not be tested.

# Success Criterion

The end state should let me boot native Ubuntu on the RTX 3090 and run:

```bash
./fastturbo-linux
```

to get a fast native FastTurbo application using Dear ImGui, CUDA, VkFFT, and OpenGL, with:

- layout, appearance, controls, and behavior as equal as possible across all four applications
- reference application menus, a centered square simulation image, compact right-hand inspector, reference transport placement, and bottom statistics
- no Python runtime
- no PySide6
- no Qt
- GPU-resident solver state
- efficient GPU visualization
- existing numerical parity preserved
- headless reference preserved
- solver performance close to the validated headless baseline

The native Linux GUI must be a thin frontend over the existing validated CUDA/VkFFT solver, not another implementation of FastTurbo.
