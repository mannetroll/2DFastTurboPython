# Windows port report — 13 September 2026

The application builds and runs on Windows 10 Pro with the RTX 3090. The port
is implemented under `windows/`; the authoritative macOS implementation remains
unchanged. The independent numerical suite, native GUI checks and SDR rendering
comparisons pass. A direct Metal-versus-CUDA trajectory comparison remains
pending because this checkout does not include the reference's Metal captures
and the available machine runs Windows.

## What was ported

The control and numerical contracts are recorded in [REFERENCE.md](REFERENCE.md).
The source review covered the Swift solver and embedded Metal kernels, VkFFT
bridge, controller, presentation models, renderer, window/application lifecycle,
CLI, diagnostics, tests, scripts and reference GUI image.

| Reference | Windows implementation |
|---|---|
| `FastTurboCore.swift`, `FastTurboShaders.swift` | `solver.cu`, generated `kernels.cuh`, `types.hpp` |
| Vendored VkFFT and Metal bridge | Same VkFFT 1.3.4 source with CUDA plans in `fft.cpp` |
| `SimulationController.swift` | Worker ownership, lifecycle, live parameters, batching and rates in `controller.cpp` |
| `FastTurboView.swift`, window/application classes | Win32 window, custom Direct2D interface, native menus/edit/save dialog in `app.cpp` |
| `TurboRendering.metal`, canvas | HLSL field/streamline shaders and CUDA/D3D11 interop in `renderer.cpp` |
| Benchmark CLI and validation interface | Native executable CLI, PowerShell runners, Python comparison scripts |

The solver retains the original vorticity sign, Fourier normalization, seeded
SplitMix64 initialization, full retained-mode mask, classical 3/2 zero padding,
all three integrators, both nonlinear formulations, compensated Float32 updates,
adaptive CFL rule and clipped physical endpoints. ETDRK4 and stress remain the
defaults. N=1024 means a retained 1024² state and 1536² nonlinear workspace.

Every normal display frame remains on the GPU. The controller publishes leased,
immutable CUDA snapshots. The renderer copies device-to-device into a CUDA
registered D3D11 buffer, then samples it from HLSL. CPU transfers are limited to
small reductions/clock data and explicit import/export operations. The interop
uses the supported [NVIDIA D3D11 resource API](https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__D3D11.html),
and the interface uses [Direct2D/DXGI interoperation](https://learn.microsoft.com/en-us/windows/win32/direct2d/direct2d-and-direct3d-interoperation-overview).

## Platform changes and GUI status

The port uses Win32, Direct2D and DirectWrite with the reference's spacing,
panel hierarchy, palette previews, square canvas, transport overlay and bottom
performance panel. It uses native Windows menus, text entry and Save PNG.
The original icon PNG is wrapped unchanged in an ICO resource. The header's
SF hurricane symbol is approximated with native vector strokes.

The actual source's controls are present: k₀, seed/dice, retained resolution,
manual Re, palinstrophy regulation/readout, three fields, four palettes,
Sigma/Exposure and their independent values, velocity direction, method,
formulation, CFL, transport, focus and PNG export. Disabled Re during regulation,
structural resets, live changes without resets, pause/step accounting and
retained throughput on pause are covered by the checks. No GUI presets, drag,
fixed-dt or manual batch controls exist in this reference revision.

Windows Ctrl replaces Command in shortcuts. A notification-area icon supplies
the reference's close-and-reopen behavior while computation continues. Ctrl+Q
and the icon's Quit command terminate the process. Window resizing retains the
square canvas, and focus preserves its size. Per-monitor DPI scaling is enabled.

Presentation targets 60 Hz, using the [DXGI frame-ready signal](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_3/nf-dxgi1_3-idxgiswapchain2-getframelatencywaitableobject)
and a balanced [1 ms timer-resolution request](https://learn.microsoft.com/en-us/windows/win32/api/timeapi/nf-timeapi-timebeginperiod).
The measured desktop presented approximately 55 frames/s. Active Windows HDR
at startup selects an FP16 scRGB swap chain using [Microsoft's HDR color-space convention](https://learn.microsoft.com/en-us/windows/win32/direct3darticles/high-dynamic-range);
the reference's linear highlight extension is retained. PNG exports remain SDR.

## Validation evidence

Machine: Windows 10 Pro 10.0.19045, RTX 3090 24 GiB, NVIDIA 616.56 WDDM,
CUDA Toolkit 13.0.88, MSVC 19.44.35221, Release C++20/CUDA build for sm_86.
GUI measurements used SDR, 100% DPI and a 640×640 physical-pixel canvas.

| Check | Result |
|---|---|
| CTest lifecycle/API suite | Passed; configuration, regulator, run averages, reset, finite-state failure/recovery, three immutable snapshot leases, endpoint and live Re/CFL checks across all six method/form pairs |
| Unmodified macOS `Scripts/validate.py` against CUDA | 114/114 checks at N=32,34,128 and both formulations |
| Independent native initializer | All six GUI resolutions 128–4096; worst relative spectral L2 error 7.55e-8, tolerance 2e-6 |
| Actual nonlinear step at each GUI resolution | Passed for N=128,256,512,1024,2048,4096 |
| Native GUI interaction/lifecycle checks | 59/59, including menus, controls, pause/step, focus, minimize/reopen and the native Save PNG dialog |
| Independent evaluation of Metal color equations against GPU PNGs | 32/32 field/palette/range combinations; maximum one 8-bit channel level of error, tolerance two levels |
| GUI versus headless trajectory | Bitwise-identical full endpoint spectra and diagnostics at N=128, 20 steps, dt=.001, Re=50000 |
| Matched Windows captures for later Metal comparison | 36 cases captured, with source/fixture/executable/output hashes |

The numerical suite checks mean/Nyquist projection, divergence/curl, analytic
diffusion, 3500-step low-viscosity diffusion, nontrivial nonlinear evolution,
high retained modes and alias rejection, inviscid conservation, batching and
exact endpoints. Its FP64 advective RK4 reference is independent of the port's
stress kernels. N=34 exercises odd M=51 padding.

The GUI check exposed and fixed a Windows save-dialog behavior that changed the
working directory. The final dialog preserves it, so subsequent relative file
exports still work. Rendering comparisons include signed and unsigned fields,
periodic interpolation/orientation and both contrast extremes. Streamline
activation and velocity snapshots are checked; streamline pixels have not been
compared against a captured Metal image.

Compact reports, the screenshot, hashes and individual measurements are in
[benchmarks/2026-09-13](../benchmarks/2026-09-13). The large raw numerical outputs
remain in ignored `windows/artifacts`. These results establish agreement with
the source's mathematics; they do not substitute for an actual Metal capture.

## Performance and optimization status

R_turbo = simulated-time advance / active wall seconds. Three interleaved
five-second runs used native seed 2, k₀=4, N=1024/M=1536, Re=50000, CFL=3,
ETDRK4 and stress. Headless runs warmed eight steps then reset and used batch 8.
GUI runs warmed visibly for two seconds, reset, then used the reference's
automatic 1–32-step batching. Initialization/FFT compilation and final file
exports are excluded; GUI snapshot and presentation contention are included.

The measured means are **1.2644 headless** (765.5 steps/s) and
**1.1589 with the GUI** (694.2 steps/s). See [runs.csv](../benchmarks/2026-09-13/runs.csv)
and [summary.json](../benchmarks/2026-09-13/summary.json) for exact individual
values and ranges. These are short-run measurements for this fixture and
desktop, not a performance claim for every resolution or a Mac speedup claim.
The Mac README's timing table uses a different imported realization.

The performance investigation identified the following baseline behavior:

| Area | Status |
|---|---|
| VkFFT batching | Preserved: stress uses two-channel inverse/forward plans; advective uses four-channel inverse and one-channel forward plans. Plans are cached. |
| Kernel fusion | Preserved: first-stage CFL reduction also forms nonlinear products; no additional arithmetic regrouping was introduced. |
| Memory reuse | State/stage/work buffers and three snapshot slots are allocated once; compatible resets reuse plans. |
| Synchronization | Clock readback occurs per submitted batch, inspections at 4 Hz, snapshots at 60 Hz; rendering has an independent interop stream. |
| CUDA Graphs | Feasibility reviewed, not enabled. A graph would need precompiled VkFFT plans and correct updates for live Re/CFL and endpoint parameters. |

Further solver optimization is deliberately deferred until genuine Metal
captures close the remaining cross-backend parity check. The delivered code is
the validated baseline, including the reference's existing batching/fusion and
the Windows presentation-pacing repair. No unvalidated fast-math or graph path
is enabled.

## Remaining differences

- Direct Metal/CUDA endpoint comparison is pending. The scripts and 36 Windows
  captures are ready; the macOS half must be produced on a Mac. The repository's
  separate CNAB2 `tools/sim` output is not an ETDRK4 app reference.
- HDR output is implemented but this machine's active display was SDR. HDR
  appearance and switching HDR modes/monitors during a session are unverified;
  restart after changing the Windows HDR mode.
- Windows typography, menu chrome and symbol rendering differ from macOS.
  Window position/size is not restored across application launches. Rendering
  has been visually inspected at 100% DPI; other DPI configurations are unverified.
- GPU allocation reports owned solver, FFT and rendering buffers; CUDA driver
  context/JIT overhead is not included. Floating-point FFT/libm implementations
  can differ between platforms; cross-platform bitwise identity is not claimed.

Exact build, launch, test, capture and benchmark commands are in
[windows/README.md](../README.md).
