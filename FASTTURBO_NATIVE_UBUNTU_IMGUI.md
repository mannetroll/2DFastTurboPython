# Task: Build a Native Ubuntu/Linux Dear ImGui GUI for FastTurbo

Work directly in the existing `2DFastTurboPython` repository.

The project already has a validated Linux CUDA/VkFFT implementation, a headless `reference-linux`, Python/PySide6 application support, cross-platform `2dbench`, and existing Linux rendering/interoperability work.

Do not redesign the numerical solver.

The objective is a **native Linux GUI application** for Ubuntu using C++/CUDA, Dear ImGui, OpenGL, and VkFFT, with no Python, PySide6, or Qt runtime dependency.

Target: native Ubuntu 26.04 with NVIDIA RTX 3090.

## 1. Objective

Create a native Ubuntu GUI analogous to the existing native platform applications:

```text
macOS    Swift native GUI   Metal + VkFFT
Windows  native GUI/ImGui   CUDA + VkFFT
Linux    Dear ImGui         CUDA + VkFFT + OpenGL
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

In particular inspect the existing Linux work for OpenGL/Vulkan interop, EGL, Wayland, X11/XCB, CUDA/OpenGL interoperability, rendering, GUI validation, and headless execution. Reuse validated infrastructure.

## 5. Dear ImGui

Use upstream Dear ImGui as the native Linux GUI layer. Prefer a small vendored or CMake-fetched dependency consistent with repository policy.

The native GUI must not depend at runtime on Python, CPython, PySide6, Qt, GTK, Electron, or a browser runtime.

## 6. Windowing

Choose the smallest robust native windowing solution compatible with the existing Linux renderer. Prefer existing infrastructure. If needed, SDL3, GLFW, or existing XCB/X11/Wayland infrastructure are acceptable.

Do not create separate complete X11 and Wayland GUI implementations.

## 7. Rendering

Keep simulation fields GPU-resident wherever practical. Preferred path:

```text
CUDA solver
→ GPU-resident field
→ CUDA/OpenGL interop
→ OpenGL texture
→ Dear ImGui image
→ display
```

Avoid full-field GPU→CPU→GUI→GPU copies every frame. Reuse the existing validated CUDA/OpenGL interop path if available.

## 8. Preserve Solver Performance

Keep persistent VkFFT plans, GPU-resident state, native batching, existing synchronization strategy, and optimized production execution path.

Do not synchronize after every simulation step merely for GUI convenience. Rendering should occur at a configurable lower frequency and must not couple solver stepping to monitor refresh rate.

## 9. Headless Baseline

`uv run 2dbench` is the performance baseline. Existing RTX 3090 Ubuntu results are approximately:

```text
2048² → 3072²   ~4.85 ms/step   ~206 steps/s
4096² → 6144²  ~21.69 ms/step    ~46 steps/s
```

These are reference measurements, not hardcoded expectations. Measure GUI overhead.

## 10. Initial GUI

Keep the first version deliberately simple.

Controls should include resolution/N, Reynolds number, CFL, K0, seed, solver/method where appropriate, field selection, and visualization update interval.

Provide Run, Pause, Single Step, and Reset.

Display iteration, simulation T, DT, CN/CFL where applicable, steps/s, ms/step, `R_turbo` if available, GPU/device, and backend.

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

XWayland is acceptable if it is the simplest robust path. Prioritize correctness, maintainability, GPU interoperability, and performance. Document the actual presentation path.

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

Do not require an interactive GUI for ordinary numerical unit tests.

## 19. Performance Validation

Compare:

```text
headless
native GUI with visualization disabled/minimal
native GUI with normal visualization frequency
```

Report ms/step, steps/s, and percentage overhead using the real production solver path.

## 20. Avoid Premature Vulkan Rewrite

Do not replace working OpenGL interop with Vulkan merely because Vulkan is newer. Use the simplest validated path. Document Vulkan only as a future direction if there is demonstrated need.

## 21. Dependency Audit

Run `ldd` on the executable. Confirm no runtime linkage to Python, Qt, PySide6, or GTK. CUDA/NVIDIA/OpenGL/windowing/system C/C++ libraries are expected.

## 22. Documentation

Document native Linux GUI architecture, dependencies, Ubuntu setup, build/run commands, backend, presentation path, CUDA/OpenGL interoperability, relationship to headless execution, performance, and limitations.

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

Do not remove the working Python/PySide6 Linux application. This task adds the native ImGui application. Any later simplification requires separate validation.

## 25. Git Discipline

Start from a clean working tree. Work incrementally. Avoid unrelated macOS/Windows changes. Do not rewrite existing evidence. Create separate native-Linux-GUI evidence.

## 26. Execution Policy

This task requires real GPU execution. If a sandbox blocks `/dev/nvidia*`, CUDA, OpenGL, the display server, or profiling, obtain permission to execute the required commands outside the sandbox.

Never fall back to CPU, replace VkFFT, fake GUI execution, or claim GPU validation without real execution.

## 27. Final Verification

Before finishing:

1. Build Release.
2. Run existing numerical/parity tests.
3. Run native Linux tests.
4. Launch native GUI on the real Ubuntu desktop.
5. Verify RTX 3090 CUDA execution.
6. Verify VkFFT execution.
7. Verify CUDA/OpenGL interop if used.
8. Exercise Run/Pause/Step/Reset.
9. Exercise field switching.
10. Exercise resize/close.
11. Verify headless execution remains display-independent.
12. Run `uv run 2dbench`.
13. Compare solver performance before/after.
14. Run `ldd`.
15. Record exact commands/results.

## 28. Final Report

Report:

### Implementation
Files created/modified and architecture.

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
Only genuine remaining issues.

# Success Criterion

The end state should let me boot native Ubuntu on the RTX 3090 and run:

```bash
./fastturbo-linux
```

to get a small, fast native FastTurbo application using Dear ImGui, CUDA, VkFFT, and OpenGL, with:

- no Python runtime
- no PySide6
- no Qt
- GPU-resident solver state
- efficient GPU visualization
- existing numerical parity preserved
- headless reference preserved
- solver performance close to the validated headless baseline

The native Linux GUI must be a thin frontend over the existing validated CUDA/VkFFT solver, not another implementation of FastTurbo.
