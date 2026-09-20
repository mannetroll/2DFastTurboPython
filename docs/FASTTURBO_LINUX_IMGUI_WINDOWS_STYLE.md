# Codex Prompt — Align Native Linux ImGui with All Four FastTurbo Applications

## Task

Work directly on the existing `IMGUI_LINUX` branch of `2DFastTurboPython`.

Inspect the working tree and preserve existing user changes. Implement and validate the refinement in the existing application.

The native Ubuntu FastTurbo application is already implemented and validated using C++, Dear ImGui, CUDA, VkFFT, OpenGL and X11/XWayland. It runs as `./fastturbo-linux`.

This task is **UI/UX refinement only**. All four applications—native macOS/SwiftUI, native Windows, Python/PySide6, and native Linux/Dear ImGui—must be as equal as possible in layout, visual appearance, controls, and interaction behavior. Bring Linux into alignment with the existing reference applications while retaining Dear ImGui and the existing Linux architecture.

Read this document together with [FASTTURBO_NATIVE_UBUNTU_IMGUI.md](FASTTURBO_NATIVE_UBUNTU_IMGUI.md), which defines the architecture and validation requirements. Both documents require the same centered simulation image and compact right-hand controls pane.

## Primary goal

Open [GUI.png](../GUI.png) in the repository root before editing. This user-supplied screenshot is the primary visual specification for the application content. Reproduce its layout, proportions, control grouping, spacing, colors, typography, and overlays as closely as possible.

Inspect all four layout implementations and available screenshots/evidence before editing:

- `reference-macos/2DFastTurbo/Sources/FastTurboApp/FastTurboView.swift` — SwiftUI layout and inspector.
- `reference-windows/src/app.cpp` — native Windows layout in `App::draw()` and `App::drawInspector()`; this implementation uses Win32/Direct2D/Direct3D.
- `python/src/fastturbo/app.py` — PySide6 layout in `MainWindow.__init__()` and `_inspector()`.
- `native-linux/main.cpp` — Linux ImGui layout in `Application::frame()` and `controlsUI()`.
- `reference-macos/2DFastTurbo/Sources/FastTurboApp/FastTurboApplication.swift` — macOS menus in `installMenus()` and state handling in `validateMenuItem(_:)`.
- `reference-windows/src/desktop_ui.cpp` — Windows menu structure in `createDesktopMenus()`; follow the action dispatch and state updates in `app.cpp`.
- `python/src/fastturbo/app.py` — Python menus in `_menus()` and their connected actions.
- README screenshots and available GUI evidence under the reference, Python, and native Linux directories.

Use `GUI.png` as the visual authority and the macOS, Windows, and Python source implementations to establish behavior and details not visible in the image. For details absent from the image, use their common design and resolve minor visual differences using native Windows. Preserve Linux functionality.

Allow differences only where required by platform capabilities or native OS conventions. Record unavoidable differences and their concrete reasons. Keep macOS, Windows, and Python as reference implementations; this task changes the Linux frontend and associated UI tests/documentation.

## Screenshot-defined appearance

Follow `GUI.png` closely:

- Header: application icon, title, environment subtitle, backend badge, focus button, and snapshot button in the shown positions.
- Simulation panel: square field, field label/legend/grid strip above it, seed overlay at the upper left, compact transport overlay at the lower left, and simulation time at the lower right.
- Right inspector: K0, Start spectrum, Seed, N, Reynolds and regulation; Enstrophy/Vorticity/Speed buttons; Inferno/Ember/Aurora/Glacier gradient buttons; color range and Sigma/Exposure; Velocity direction; then the NUMERICS section with Method, Form, and CFL. Match this order, grouping, and visual treatment.
- Bottom strip: reproduce the metric order, spacing, number/unit typography, and accent treatment, using correctly labeled real backend values.
- Overall styling: reproduce the dark surfaces, restrained borders and rounding, turquoise accents, panel proportions, and compact spacing. Use appropriate DPI scaling.

The image is a macOS window capture and does not include the global macOS menu bar. This does **not** remove menus from the specification: Linux must include the in-window File/Edit/Simulation/View/Window menu bar described below, above the pictured application header. Keep Linux OS chrome and show the actual Linux GPU/backend/environment information.

## Hard rule: UI only

Do not modify CUDA kernels, VkFFT, ETDRK4, CNAB2, timestep logic, CFL/controller behavior, padding, dealiasing, numerical initialization, GPU memory ownership, benchmark workload, parity tolerances or PAO behavior.

Do not perform a numerical refactor or solver optimization.

Preserve:

```text
CUDA/VkFFT solver
       ↓
GPU-resident state
       ↓
CUDA/OpenGL interoperability
       ↓
OpenGL texture
       ↓
Dear ImGui
       ↓
X11/XWayland
```

Do not replace Dear ImGui/OpenGL/X11 with Qt, GTK, Vulkan, Python, PySide6, Electron or another GUI framework.

## Preserve performance

Historical approximate measurements:

```text
2048²
Headless                  5.427 ms/step
Visualization off         5.471 ms/step
Normal visualization      5.581 ms/step

4096²
Headless                 24.253 ms/step
Visualization off        24.414 ms/step
Normal visualization     25.087 ms/step
```

These measurements showed normal visualization overhead of roughly 3%. They are context, not fixed acceptance thresholds. Capture a fresh baseline before changing the frontend, then compare before/after under identical hardware, solver settings, window size, visualization interval, and display conditions.

Do not materially increase GUI overhead. Do not introduce extra solver synchronization, full-field CPU copies, extra CUDA/OpenGL transfers, expensive per-frame allocations, or coupling between solver stepping and monitor refresh. Preserve the existing rendering/snapshot cadence and solver/rendering decoupling.

## Required layout

Replace the current 340-pixel left controls pane and statistics-above-image arrangement with the shared reference layout:

- Place the application menu bar across the top of the Linux window's client area, below the OS title bar and above the application header. Include **File, Edit, Simulation, View, and Window**, in that order, with the actions specified below.
- Keep the complete square simulation domain visible, without cropping or stretching. Center it within the viewport area available beside the inspector, rather than across the entire window.
- Place a compact inspector on the **right**. The references use a 196-logical-pixel inspector and an 18-logical-pixel gap; use these as starting proportions with appropriate DPI scaling.
- Match the reference header, field label, color legend, grid label, seed/time overlays, transport placement, and bottom metrics strip.
- Keep Run/Pause, Single Step, and Reset near the bottom of the simulation view, as in the references.
- Implement the reference focus mode, hiding the inspector and metrics while keeping transport usable and the square domain visible.
- Keep shared advanced controls in their corresponding inspector locations. Do not relocate them into a separate settings popup or replace the inspector with a bottom toolbar.

Use the actual reference source and screenshots to determine placement and appearance. Do not substitute an ASCII diagram or a simplified mockup for those references. Menus are part of the required interface, alongside the header, viewport, inspector, overlays, and statistics.

## Application menus

Implement an integrated Dear ImGui menu bar matching the Windows/Python menu hierarchy. On Linux it belongs inside the application window; macOS's global menu bar and separate application menu are platform conventions.

| Menu | Required actions in reference order |
| --- | --- |
| File | Save Snapshot… (Ctrl+S), Close Window (Ctrl+W), About 2DFastTurbo, Quit 2DFastTurbo (Ctrl+Q) |
| Edit | Undo (Ctrl+Z), Redo (Ctrl+Y), Cut (Ctrl+X), Copy (Ctrl+C), Paste (Ctrl+V), Select All (Ctrl+A) |
| Simulation | Pause / Resume (Space), Single Step (Right), Reset Flow (R), New Initial Condition (Ctrl+Shift+R) |
| View | Focus on Flow / Show Controls (F), Show Simulation, Dismiss Message |
| Window | Minimize (Ctrl+M) |

Match the reference separators, dynamic labels, enabled/disabled states, and any checked states. Menu actions and their corresponding buttons/shortcuts must use the same underlying actions and reflect the same application state. Keep the menu bar accessible in focus mode.

Route Edit actions to the active text/numeric editor. Simulation shortcuts must not consume typing, cursor navigation, or editing commands while an input is active. Support menu keyboard navigation and show the shortcut labels.

Use existing controller, renderer, and platform APIs for real actions, including snapshot export, About, window operations, and simulation control. Inspect Close Window versus Quit behavior explicitly and document any necessary Linux lifecycle difference. Do not create placeholder menu actions or silently omit an action; report any remaining implementation gap and its concrete cause.

## Controls and behavior

Match the reference control order, labels, selected states, palette previews, button dimensions, and spacing. Preserve existing fields and validated Aurora, Ember, Glacier, and Inferno color behavior. Make Exposure and Sigma compact, with unobtrusive numeric values.

Retain resolution/N, Reynolds number, palinstrophy regulation, CFL, K0, seed, method, nonlinear form, start spectrum, visualization enable/disable, update interval, and flow overlay. Place Linux-specific controls unobtrusively within the right inspector while preserving the order of shared controls.

Match immediate display updates, live parameter edits, reset-triggering configuration changes, keyboard shortcuts, and focus/export actions wherever supported by the existing Linux backend. Use existing controller and renderer APIs; do not change numerical semantics to reproduce a widget. Preserve method-specific constraints, and report any behavior that cannot match without backend changes. Platform modifier keys may follow native conventions.

## Status

Match the reference bottom statistics strip and header GPU/backend summary. Keep iteration, T, DT, steps/s, ms/step, and R_turbo accessible without placing a large telemetry block above the image. Keep rendering FPS distinct from solver throughput and configured CFL distinct from any measured quantity.

Detailed CUDA/device diagnostics belong in an About/Diagnostics panel rather than dominating the main view.

## Typography, spacing and theme

Improve default ImGui appearance substantially.

Use a clean system-appropriate Ubuntu font if practical; do not embed proprietary font files. Support high DPI.

Deliberately tune ImGui style parameters including padding, item spacing, frame spacing, scrollbar/grab sizing and restrained rounding.

Use the restrained dark theme, accent colors, and panel treatment shared by the reference applications. Avoid default-ImGui bright blue everywhere, excessive borders, excessive separators and debug-window appearance.

## One coherent application surface

Avoid multiple floating debug-style windows as the normal UI.

Use one coherent application surface. From top to bottom, it contains the application menu bar, the application header, the simulation viewport beside the right-hand inspector, and the bottom statistics strip. Place transport and seed/time overlays within the simulation view as in the references.

Development diagnostics may remain available but should not define the normal interface.

## Responsive layout

Test at least:

- 1280×720
- 1920×1080
- large/high-DPI desktop

The simulation viewport should consume extra space while its image stays square and centered. Keep the inspector compact and scroll its contents when needed. Verify the full field and all controls remain reachable at each size, when maximized, and in focus mode. Scale fonts and spacing for high DPI; OS fullscreen is optional and separate from focus mode.

## Preserve functionality

Verify all current functionality remains:

- Run
- Pause
- Single Step
- Reset
- field switching
- colormap switching
- exposure
- sigma
- resolution/settings
- Reynolds/CFL edits and palinstrophy regulation
- K0, seed, method, form, and start spectrum
- visualization enable/disable and update interval
- flow overlay
- all five application menus and their actions, state updates, and keyboard navigation
- keyboard shortcuts
- reference focus/export actions where supported by existing backend APIs
- resize
- close

Preserve `reference-linux`, headless CUDA/VkFFT, and `uv run 2dbench`.

## Compare All Four Interfaces

Before finalizing, compare Linux screenshots directly against `GUI.png`, then against other available macOS, Windows, and Python screenshots, using comparable field/palette settings and window proportions. Account for Linux's in-window menu bar and OS chrome when comparing content alignment. Cross-check the source where evidence is unavailable, and identify any comparison that could not be performed. Iterate on:

- visual hierarchy
- menu bar placement, menu hierarchy, labels, and action states
- viewport size
- right-hand inspector and transport placement
- control density
- colors
- typography
- spacing
- status presentation

The Linux application should match the shared design as closely as possible. A generally similar dark theme alone does not satisfy layout and behavior parity.

## Screenshot evidence

Capture before/after screenshots and finished layouts at 1280×720 and 1920×1080 under `native-linux/evidence/`, plus high-DPI and focus-mode evidence where available. Include the menu bar in the main screenshots and additional captures of open menus showing their contents and states. Record display scaling and screenshot paths. Preserve earlier evidence by using new filenames.

## Regression verification

Capture baseline screenshots and performance before UI work. After UI work:

1. Build Release.
2. Run native tests.
3. Run existing numerical/parity tests.
4. Launch GUI on RTX 3090.
5. Exercise all controls.
6. Resize repeatedly, verify the required window sizes and inspector scrolling, and exercise focus mode.
7. Switch fields and colormaps.
8. Adjust exposure/sigma.
9. Pause/resume/single-step/reset; verify display changes, live edits, and configuration resets against the reference behavior.
10. Close normally.
11. Run `uv run 2dbench`.
12. Repeat headless, visualization-disabled, and normal-visualization measurements under identical conditions. Confirm regression is within normal noise.
13. Verify `ldd` still shows no Python, Qt, PySide6 or GTK dependencies.
14. Exercise every menu action, menu keyboard navigation, state-dependent labels/enabling, and shortcuts both with and without an active text/numeric editor. Verify menu access in focus mode and the documented Close Window/Quit behavior.

Do not accept a prettier GUI that materially slows the solver. If performance regresses, identify and fix the cause.

Use real GPU/display execution for acceptance. Report exact commands and results, distinguish remote presentation from local GPU performance, and identify anything that could not be tested. Do not claim visual parity or GPU validation from source inspection alone.

## Do not overengineer

Do not introduce a new GUI framework, rendering API, solver abstraction, plugin system or application architecture unless absolutely necessary.

This is a focused refinement of the existing ImGui frontend.

## Final report

Report:

- visual changes
- layout and behavior parity with macOS, Windows, and Python
- files changed
- functional validation
- numerical regression results
- before/after GUI performance
- dependency audit
- before/after and final screenshot paths, including comparison evidence
- remaining platform differences and any untested requirements

Confirm the executable remains:

```text
C++ / Dear ImGui / OpenGL / CUDA / VkFFT
```

with no Python, PySide6, Qt or GTK runtime dependency.

## Success criterion

All four FastTurbo interfaces should be as equal as possible in layout, appearance, menus, controls, and behavior. Linux must have the reference application menus, centered square simulation image, compact right-hand inspector, reference transport placement, and bottom statistics while retaining native C++, Dear ImGui, CUDA, VkFFT, OpenGL, GPU-resident rendering, and low GUI overhead.

Do not sacrifice numerical correctness or solver performance for appearance.
