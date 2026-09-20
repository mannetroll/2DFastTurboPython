# Swift reference contract

Recorded before implementation, 2026-09-14. The authoritative tree is
`reference-macos/2DFastTurbo`. Source wins over README prose and the examples in
`PythonPort.txt`. In particular, the actual default Re is 27898.36614974335,
not the stale 50000 in CLI help. The live GUI enables palinstrophy regulation.

## Source inventory and boundary

* `FastTurboCore.swift`, `FastTurboTypes.swift`, `FastTurboShaders.swift`: complete
  native solver, configuration, statistics, leased frames, embedded compute MSL.
* `MTTFFT2DBackend.h`, `MTTVkFFTBackend.mm`, `MTTVkFFTCore.{hpp,cpp}`: Objective-C
  interface, C++ plan cache, vendored VkFFT 1.3.4 Metal backend (backend 5).
* `SimulationController.swift`: native serial pump, regulator, generations, live
  controls, publication, transport, PNG action. `PresentationTypes.swift`: enums,
  observation objects, whole-run throughput accounting.
* `FastTurboCanvas.swift`, `TurboRendering.metal`: native MTKView, render pipelines,
  frame leases, HDR and SDR PNG output. `FastTurboView.swift`: all visible controls.
* `FastTurboApplication.swift`, `FastTurboWindow.swift`: lifecycle, square layout,
  menus, keyboard handling. `FastTurboGUIDiagnostics.swift`: opt-in live tests and
  timings. `AppMain.swift`, `BenchMain.swift`, `BenchmarkCLI.swift`: entry points.
* Tests cover lifecycle, immutable snapshots, failures, live Re/CFL, reference
  mathematics and benchmark acceptance. `Scripts/validate.py` supplies an
  independent FP64 advective RK4 and direct non-wrapping convolution.
* Xcode generation/build/test/package scripts use macOS 15, arm64, Swift 5 language
  mode, Swift `-O` whole module, C++20 `-O3`. Vendored headers include the required
  Metal/AppKit ownership patch and licenses. Comparison/release scripts and
  `tools/CNAB2*` are offline validation tools, not application solver paths.

## Every GUI control

All quantities are nondimensional unless specified. Changes marked **reset**
replace the initial-value problem while preserving the latest run/pause intent.

| Label/action | Type, allowed values | Default | Native mapping and side effect |
|---|---|---|---|
| Play/Pause; Space | transport button/key | running | `togglePause`; finish current batch; retain throughput |
| Single step; Right | transport button/key | — | pause and advance exactly one complete timestep |
| Reset; R | button/key | — | same seed/configuration, zero clock and histories; reuse plans if config equal |
| New seed; Cmd-Shift-R | dice/menu/key | seed 2 | increment UInt64 with wrap, **reset** |
| k₀ | picker: 2,4,6,8,10,12,16,20 | 4 | `initialWaveNumber`, **reset**; recalibrate Re if regulated |
| Seed | text, UInt64 0…2⁶⁴−1, applied on Return | 2 | `seed`, **reset**; invalid text restores previous value |
| N | picker: 128,256,512,1024,2048,4096 | 1024 | retained `resolution`, **reset**; M=3N/2; recalibrate Re if regulated |
| Re | continuous log10 slider 3…6 | calibrated 27898.36614974335 | viscosity=1/Re; disabled in auto mode; live batch-boundary update |
| Regulate with palinstrophy | switch | on | reset PID history; disabling holds current effective Re for manual use |
| Enstrophy/Vorticity/Speed | exclusive field buttons | Vorticity | `display`; request snapshot; velocity only for Speed/overlay |
| Inferno/Ember/Aurora/Glacier | exclusive palette buttons | Ember | `palette`; recolor completed frame |
| Color range | menu Sigma/Exposure, Vorticity only | Sigma | `vorticityColorRange`; independent slider values retained |
| Sigma | continuous slider 1.5…3.5 | 2.5 | ±sigma×std around mean; recolor only |
| Exposure | continuous slider 0.25…3 | 1.2 | soft contrast; used for Speed/Enstrophy and Exposure mode |
| Velocity direction | switch | off | `showFlowLines`; native instantaneous streamline overlay; request velocity snapshot |
| Method | IMEX RK3 / Integrating-factor RK3 / ETD RK4 | ETD RK4 | `integrator`, **reset**; choosing RK3 caps current CFL at 1.5 |
| Form | Stress/Advective | Stress | `nonlinearFormulation`, **reset** |
| CFL | continuous slider 1…3 | 3 | `cfl`, live batch-boundary update; no reset |
| Focus; F | toolbar/menu/key | off | hide inspector and dashboard, preserve square canvas side |
| Save PNG; Cmd-S | toolbar/menu/key, save dialog | 1800² pixels | native SDR render/export of completed frame; no solver change |
| Dismiss status | close button | — | clear status message |
| Show Simulation | View menu/Dock reopen | — | reopen retained run; visibility gates snapshots, not simulation |
| Close/Minimize/Hide/Quit | macOS menus/window | — | close/hide continues simulation; quit ends application |

Re/CFL drags coalesce to 30 updates/s, flush on release and before transport.
There are **no** presets, drag coefficient, forcing, manual dt slider, batch slider,
separate contrast control, contour settings, Streamfunction field, or mouse vortex
painting in this source. Fixed dt and fixed batches are developer CLI options.
Adding those controls would change the reference contract.

## Equations, storage and normalization

Periodic `[0,2π)²`, ω=∂xv−∂yu; û=i ky ω̂/k², v̂=−i kx ω̂/k².
Thus ψ̂=ω̂/k² for u=∂yψ, v=−∂xψ. Evolution is
∂tω=−u·∇ω+νΔω, ν=1/Re, no drag or forcing.

Actual state: FP32 complex Hermitian half-plane `[N, N/2+1]`, every ky row,
nonnegative kx. It stores **N² times Fourier-series coefficients**. Mean and both
Nyquist axes are zero: retained iff |kx|,|ky|<N/2 and k≠0. Public spectrum export
reconstructs the full `[ky,kx]` array and divides by N².

Classical 3/2 padding uses M=3N/2, hence N1024/M1536. Negative ky maps to M+ky;
coefficients are scattered without amplitude scaling. Padded complex pitch is
ceil((M/2+1)/8)×8, physical pitch twice that. Both FFT directions are
unnormalized (`normalize=0`). Inverse velocities/derivatives divide by N²;
nonlinear forward products gather retained indices and multiply by N²/M².
This is not a 2/3 filter on an M-resolution simulation. Odd M is supported for
even N such as N34/M51 by the core (GUI exposes powers of two).

## Native execution pipeline

One serial execution queue owns all core mutation. One Metal command queue serves
simulation, inspection and snapshots. Per timestep, for each RK stage:

1. `velocityToPadded`: Poisson inversion, velocity derivatives, zero-fill outside
   retained band; advective form also scatters ∂xω and ∂yω.
2. Batched inverse VkFFT: 2 stress fields or 4 advective fields.
3. Stage zero `reduceCFLAndProducts` normalizes velocities, reduces max(|u|+|v|)
   in 64×256 threads, and forms products. `selectTimeStep` selects immutable dt
   for every stage. Other stages use `nonlinearProducts`.
4. Forward VkFFT: 2 stress products (u²−v²,uv) or one advective product.
5. `advanceStage` gathers f̂=−kxky FFT(u²−v²)+(kx²−ky²)FFT(uv), or advective f̂,
   rescales, integrates, checks finite state, and zeros excluded modes.
6. After all stages `commitStep` updates compensated FP32 high/low time and UInt64
   step count. Endpoint clipping commits the requested Double as a high/low pair.

CFL target=2*CFL/(N*max(|u|+|v|)); falls immediately, growth is
min(target,0.8*previous+0.2*target). Zero flow uses previous dt or 1. Fixed dt
overrides CFL. One command buffer contains many full steps (core chunks at 256).
Native `waitUntilCompleted` occurs once per batch, inspection, or snapshot, never
per kernel and never for Python's convenience. GPU errors latch failed state until
a successful reset. Synchronous APIs are safe inside native completion handlers.

IMEX RK3 uses α=(8/15,2/15,1/3), β=(8/15,5/12,3/4), ζ=(—,−17/60,−5/12), with
Crank–Nicolson diffusion. IF Heun RK3 uses integrating factors at 1/3,2/3,1 and
final weights 1/4,3/4. ETDRK4 uses four nonlinear evaluations and phi₁/₂/₃;
16-term series for |z|≤1, stable recurrences otherwise. Exponential increments
and compensated spectral remainders preserve small diffusion. The exact stage
code is reused unchanged; stress RK3 needs 12 transforms, ETDRK4 16;
advective needs 15 and 20 respectively.

Native initialization is SplitMix64(seed+golden-ratio constant), 24-bit Float
uniform phases, velocity power exp(−k²/k₀²)/(πk₀²), legacy velocity realization,
canonical curl and conjugate kx=0 coefficients. No normalization to unit rms speed.
The independent Pao benchmark fixture is a different realization and must be
serialized/shared for comparisons. Imports project finite physical Float32 data.

## Resources, diagnostics, regulator and scheduling

Reused allocations: omega, compensation remainder, padded workspace (2/4 planes),
base, first, second, auxiliary, ETD coefficients (0/3/6 scalars per mode), 40-byte
clock, CFL/statistic partials, 3 snapshot spectra, snapshot partials, 3 immutable
physical snapshot slots of `[ω,u,v]` Float32 N² planes. Parameters are 52 bytes.
No per-step allocations of field arrays. Padded and retained snapshot FFT backends
persist; optional input C2C plan is lazy. Each backend caches plans by batch count,
uses LUTs for real sizes≥768 and frequency guards for the padded retained band.

Energy E=½Σ|ω̂|²/k², enstrophy Z=½Σ|ω̂|², palinstrophy P=½Σk²|ω̂|² use conjugate
weights and GPU partial reductions. Initial τ₀=2π/√(2Z₀) is fixed for the run.
Frame reductions return max|ω|, mean/std ω and max Euclidean speed.

Native pump independently drives GPU completions, adaptive batches 1…32 aiming
at 8ms and clipped by next 60Hz snapshot deadline. Inspection is 4Hz. Presentation
uses a separate native queue, two in-flight renders, and retains each snapshot
lease through GPU completion; busy slots cannot be overwritten. Hidden windows
continue integration without snapshots. Generations prevent stale completions
from altering replacement runs. Paused recoloring/scalar updates reuse frames.

PID: metric=10000P/[Z·2(N/2−1)²], target20, two EMA filters α=1/6, relative error
deadband .001, Kp=.25, Ki=0, Kd=25, output clipped ±.01, Re*=exp(output), bounds
1000…1e6 intersect calibration/1000…calibration*1000. Calibration is
10^(1.066240 log10N−.083911 log10k₀+1.286396). It runs on diagnostic samples,
including initial inspection, so matched deterministic timing disables regulation.

Dashboard: whole-run simulated time/active wall second, initial turnovers/second,
recent integration steps/s, E with 120-sample sparkline, Z, dt, step count and
device allocation MiB. Pauses/manual steps are excluded from whole-run throughput;
resume keeps history, reset clears it. GPU milliseconds measure advance only.

## Visualization and interaction

The square canvas uses bilinear periodic sampling of the leased Metal buffer.
Vorticity Sigma: clamp((ω−mean)/(sigma*std),−1,1). Exposure:
tanh(ω/max|ω|*exposure*2.4). Speed: 1−exp(−|u|/maxSpeed*exposure*2.1).
Enstrophy: 1−exp(−(ω/max|ω|)²*exposure*8), without a ½ in the displayed quantity.
Actual palette MSL is reused. Velocity lines: 32² jittered seeds, 18 midpoint
segments, instantaneous physical velocity, no synthetic field manipulation.
HDR uses RGBA16Float, extended linear sRGB, up to 2× SDR white. PNG is BGRA8 sRGB
and explicitly reads back rendered pixels only for export. No field reaches Python
in normal use. Source overlays show unforced/seed, physical time and transport.
Window minimum canvas is 576 points; chrome 22px horizontal margin, 196px inspector,
18px gap, 77px header, 34px field header and 54px dashboard.

## Port decision

Compile the unchanged solver, native controller/pump and presentation types into
a dylib with an opaque C ABI and ctypes. Host the existing native SwiftUI/MTKView
canvas inside a PySide6 NSView and implement surrounding controls in Python.
The one necessary generated adaptation is renderer library lookup: a Python
executable cannot use the reference app's main-bundle `default.metallib`, so load
the compiled, packaged metallib explicitly. No mathematical/shader changes.
Headless API exposes batches and explicit file exports for validation only.
Python GUI polls small published telemetry at 4Hz; native frame delivery remains
60Hz with no Python callback or frame conversion.
