# Codex Prompt — Make Native Linux ImGui GUI Resemble Windows FastTurbo

## Task

Work directly on the existing `IMGUI_LINUX` branch of `2DFastTurboPython`.

The native Ubuntu FastTurbo application is already implemented and validated using C++, Dear ImGui, CUDA, VkFFT, OpenGL and X11/XWayland. It runs as `./fastturbo-linux`.

This task is **UI/UX refinement only**. Make the Linux application resemble the existing Windows FastTurbo application as closely as practical while retaining Dear ImGui and the existing Linux architecture.

## Primary goal

Inspect the Windows implementation and screenshots/evidence first, especially:

- `reference-windows/`
- `python/native/windows/`
- `python/evidence/windows/`
- README screenshots and GUI evidence

Use the Windows application as the authority for visual hierarchy, viewport placement, toolbar/control placement, typography, spacing, colors, button sizing, field/colormap selection, exposure/sigma controls and status presentation.

The goal is the same product design, not pixel-identical OS chrome.

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

Current approximate measurements:

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

Normal visualization overhead is roughly 3%.

Do not materially increase this. Do not introduce extra solver synchronization, full-field CPU copies, extra CUDA/OpenGL transfers, expensive per-frame allocations, or coupling between solver stepping and monitor refresh.

## Visual direction

The flow visualization must dominate the application. Aim for roughly 75–90% simulation area and 10–25% controls/status depending on aspect ratio.

Conceptually:

```text
┌──────────────────────────────────────────────────────────────┐
│ FastTurbo                                      RTX 3090 CUDA │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│                    SIMULATION FIELD                          │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ ▶ Run   ⏸ Pause   ↺ Reset    Vorticity ▾    Inferno ▾       │
│ T 0.234   DT 2.4e-4   179 steps/s   R_turbo ...             │
└──────────────────────────────────────────────────────────────┘
```

Use the actual Windows application as the visual authority.

## Simplify the normal interface

The main view should primarily expose:

- Run / Pause / Reset / Single Step
- field selector
- colormap
- exposure
- sigma
- iteration/time
- performance
- GPU/backend

Move advanced settings such as N, Re, CFL, K0, seed, method and update interval into a settings/collapsible panel/popup/menu. Do not remove functionality.

## Toolbar and controls

Create a compact toolbar inspired by Windows. Use consistent button dimensions and spacing. Icons are acceptable where useful without introducing a large icon framework.

Provide clean selectors such as:

```text
Field:   [ Vorticity ▾ ]
Palette: [ Inferno ▾ ]
```

Preserve existing fields and the validated Aurora, Ember, Glacier and Inferno color behavior.

Make Exposure and Sigma compact, with unobtrusive numeric values.

## Status

Create a compact status area showing important live information such as iteration, T, DT, steps/s, ms/step, R_turbo, GPU and backend.

Detailed CUDA/device diagnostics belong in an About/Diagnostics panel rather than dominating the main view.

## Typography, spacing and theme

Improve default ImGui appearance substantially.

Use a clean system-appropriate Ubuntu font if practical; do not embed proprietary font files. Support high DPI.

Deliberately tune ImGui style parameters including padding, item spacing, frame spacing, scrollbar/grab sizing and restrained rounding.

Use a restrained dark theme resembling Windows FastTurbo. Avoid default-ImGui bright blue everywhere, excessive borders, excessive separators and debug-window appearance.

## One coherent application surface

Avoid multiple floating debug-style windows as the normal UI.

Prefer one coherent application surface containing:

```text
viewport
toolbar
status
optional settings
```

Development diagnostics may remain available but should not define the normal interface.

## Responsive layout

Test at least:

- 1280×720
- 1920×1080
- large/high-DPI desktop

The simulation viewport should consume extra space. Controls should not expand unnecessarily. Maximized use should work well. Fullscreen visualization is optional if straightforward.

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
- resize
- close

Preserve `reference-linux`, headless CUDA/VkFFT, and `uv run 2dbench`.

## Direct Windows comparison

Before finalizing, compare Windows and Linux screenshots side by side and iterate on:

- visual hierarchy
- viewport size
- toolbar placement
- control density
- colors
- typography
- spacing
- status presentation

The Linux application should clearly belong to the same FastTurbo product family.

## Screenshot evidence

Capture the finished Linux application at a representative size under the native Linux evidence directory, for example `native-linux-final.png`. Preserve earlier evidence.

## Regression verification

After UI work:

1. Build Release.
2. Run native tests.
3. Run existing numerical/parity tests.
4. Launch GUI on RTX 3090.
5. Exercise all controls.
6. Resize repeatedly.
7. Switch fields and colormaps.
8. Adjust exposure/sigma.
9. Pause/resume/reset.
10. Close normally.
11. Run `uv run 2dbench`.
12. Confirm benchmark regression is within normal noise.
13. Verify `ldd` still shows no Python, Qt, PySide6 or GTK dependencies.

Do not accept a prettier GUI that materially slows the solver. If performance regresses, identify and fix the cause.

## Do not overengineer

Do not introduce a new GUI framework, rendering API, solver abstraction, plugin system or application architecture unless absolutely necessary.

This is a focused refinement of the existing ImGui frontend.

## Final report

Report:

- visual changes
- Windows design elements reproduced
- files changed
- functional validation
- numerical regression results
- before/after GUI performance
- dependency audit
- final screenshot path

Confirm the executable remains:

```text
C++ / Dear ImGui / OpenGL / CUDA / VkFFT
```

with no Python, PySide6, Qt or GTK runtime dependency.

## Success criterion

When shown beside Windows FastTurbo, Linux FastTurbo should immediately be recognizable as the same application/product family while retaining native C++, Dear ImGui, CUDA, VkFFT, OpenGL, GPU-resident rendering and low GUI overhead.

Do not sacrifice numerical correctness or solver performance for appearance.
