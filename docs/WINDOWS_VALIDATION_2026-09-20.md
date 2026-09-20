# Windows validation — 2026-09-20

## CUDA 13.4 follow-up

After the toolkit/driver upgrade, both native and Python backends rebuilt with
CUDA **13.4.92** and driver **616.92** on the same RTX 3090. The full Windows
test script passed again: **98 Python tests passed, 27 expected skips**, and
**2/2 Python backend CTest checks passed**. The standalone Release executable
passed **1/1 CTest checks** and **68/68 live GUI checks**, including PNG export.
Independent FP64 numerical validation passed **76/76 checks**; see the
[CUDA 13.4 numerical summary](../reference-windows/artifacts/windows-cuda134/numerics/summary.json).

Switching CUDA compilers exposed a CMake cache-reset issue: automatic
reconfiguration discarded explicit options, producing a Debug native build
and stalling Python configuration. Both Windows build command files now use
`cmake --fresh` so the supplied options apply to a fresh cache. Final caches
were checked for `Release`, CUDA 13.4, native architecture `86`, and Python
architectures `86-real;89-real;120-real;86-virtual` before validation.
Windows source builds and the native launcher now require `CUDA_PATH`;
hardcoded toolkit path fallbacks have been removed.

CUDA 13.4 local evidence is under
`reference-windows/artifacts/windows-cuda134/`, including
[GUI results](../reference-windows/artifacts/windows-cuda134/gui-check.json)
and a [screenshot](../reference-windows/artifacts/windows-cuda134/gui-window.png).
Release distribution packages were not rebuilt.

## Earlier CUDA 13.1 validation

Validated checkout `489ec84` plus the Windows fixes described below on Windows
10 build 19045, NVIDIA RTX 3090 (24 GiB), driver 591.44, CUDA Toolkit 13.1.80,
MSVC 19.44.35221, and Python 3.13.11.

| Check | Result |
| --- | --- |
| Python backend forced Release rebuild | Passed |
| Standalone native Windows Release build | Passed |
| Full Python suite, including GUI | 98 passed, 27 expected skips |
| Python backend CTest | 2/2 passed |
| Standalone native CTest | 1/1 passed |
| Native live GUI validation | 68/68 passed |
| Independent FP64 numerical validation | 76/76 passed |

The final `python/scripts/test-windows.ps1` invocation passed end to end.
Python tests cover Windows bitwise native parity through N=2048, Unicode
exports, independent rendered-pixel comparisons, KM3 initialization and reset,
native worker execution, GUI controls and window-close cleanup. Skips cover
Linux/macOS features and the Windows Close Window menu's intentional tray behavior.
CNAB2 remains a Linux/macOS feature.

The standalone GUI check exercised menus, all existing integration methods,
both operator forms, N=3072, regulation, pause/step/reset, tray reopening,
CUDA/Direct3D interoperability and PNG export. Numerical validation exercised
N=32 and N=34 with stress and advective forms, using the shared independent
FP64 validation suite (19 checks per group).

## Fixes required here

- Native build and launch scripts now honor `CUDA_PATH` and search both `bin`
  and `bin/x64` for runtime DLLs. Previously they hardcoded CUDA 13.0, which is
  not installed on this machine. Missing toolkits now fail immediately.
- The Python test script obtains CTest from the generated CMake cache, so it
  works when Visual Studio's CMake tools are absent from PowerShell's PATH.
- The native GUI test waits for the rendered regulation state before checking
  the disabled Reynolds slider. Presentation throttling could otherwise leave
  the test inspecting the previous frame's hit targets. The assertion remains
  unchanged; solver and application behavior were not modified.

## Local evidence

- [Python JUnit results](../python/results/windows-20260920/pytest.xml)
- [Native GUI results](../reference-windows/artifacts/windows-20260920/gui-check.json)
- [Native GUI screenshot](../reference-windows/artifacts/windows-20260920/gui-window.png)
- [Numerical summary](../reference-windows/artifacts/windows-20260920/numerics/summary.json)

These generated artifacts are local, ignored build/test outputs. Portable ZIP,
wheel and standalone distribution packaging were not rebuilt. Exhaustive
large-grid allocation checks through N=16384 were not run.
