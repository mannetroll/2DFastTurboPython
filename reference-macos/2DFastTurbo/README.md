# 2DFastTurbo

![2DFastTurbo macOS interface](GUI.png)

A native macOS app for unforced, decaying two-dimensional turbulence. It combines
Metal2DTurbo's batched VkFFT solver formulation with MLXAstra's interactive field
presentation. Its main performance readout is **physical simulation time per wall
second**, rather than the number of small timesteps executed.

Substantial optimizations include GPU-resident simulation and rendering, tuned
FFTs, compact half-spectrum storage, and ETDRK4 with full 3/2 dealiasing.

The live view uses HDR on compatible displays, with highlights reaching up to
twice SDR white. PNG exports use standard sRGB. The [M1 Max HDR comparison](HDR_benchmark.md)
found no meaningful simulation slowdown at a 640 × 640-point canvas.

## Live time-stepping comparison

**2DFastTurbo · N1024 / M1536 · k₀ = 4 · Re = 50,000 · seed = 2 · Stress formulation.**
Measured on Apple M1 Max, macOS 15.7.9, on 2026-09-09. Each row is the median of
five interleaved live runs, with a 640 × 640-point Vorticity/Ember canvas
(1280 × 1280 pixels, 60 Hz presentation target).

| Time-stepping method | CFL | Simulated time / wall s | Initial turnovers / wall s | Integration steps / wall s | Mean Δt | Presented FPS | Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| **ETD RK4** | **3.0** | **0.420578** | **0.299345** | 252.95 | 0.001662714 | 56.02 | Pass 5/5 |
| IMEX RK3 | 1.0 | 0.208539 | 0.148427 | 375.97 | 0.000554676 | 56.25 | Pass 5/5 |
| IF Heun RK3 (Astra) | 1.0 | 0.195431 | 0.139098 | 352.33 | 0.000554676 | 56.15 | Pass 5/5 |

These are the **highest tested accuracy-passing CFL values for this fixture**.
Both RK3 methods failed the final enstrophy budget at the next tested CFL, 1.5;
ETD RK4 passed through CFL 3.0. See the earlier [CFL screening records](Benchmarks/full-comparison-2026-09-09/runs.json).
ETD RK4 advances 2.02× as much physical time per wall second as IMEX RK3 and
2.15× as much as IF Heun RK3 at these accepted settings.

Every run imports the identical [serialized Pao seed-2 fixture](Benchmarks/full-comparison-2026-09-09/n1024-k4-re50000-seed2/fixture.json).
Its realization differs from the app's native seed-2 initializer. The endpoint
is `t = 2τ₀ = 2.8099863341033897`, with fixed `τ₀ = 1.4049931670516949`.
Initialization, two-second warmup, reset, and spectrum export are untimed.
Mean Δt is completed physical time divided by completed integration steps;
presented FPS counts displayed frames independently of integration steps.

All 15 endpoints passed the same refined-reference accuracy checks. Relative
error limits are `4.35798e-3` for vorticity L₂, `2.51594e-4` for energy, and
`2.88754e-5` for enstrophy. Three incomplete attempts were preserved and excluded:
two visibility failures before the first step and one stalled run. Every valid
completed timing was retained, including slower repeats.

Local evidence (ignored by Git): [summary](Benchmarks/live-methods-2026-09-09/turbo-only/summary.json),
[manifest](Benchmarks/live-methods-2026-09-09/turbo-only/manifest.json),
[checked runs](Benchmarks/live-methods-2026-09-09/turbo-only/checked-runs.json), and
[source/bundle hashes](Benchmarks/live-methods-2026-09-09/turbo-only/source-manifest.json).
To reproduce from this directory, using the retained local fixture, reference
outputs, and frozen app bundle:

```sh
python3 Scripts/run_live_methods.py \
  --app Benchmarks/live-methods-2026-09-09/executables/2DFastTurbo.app \
  --output Benchmarks/live-methods-reproduction --runs 5
python3 Scripts/summarize_live_comparison.py \
  --directory Benchmarks/live-methods-reproduction
```

## Earlier release comparison

The default is **N1024 / M1536, stress-form ETDRK4, CFL 3**. It was selected from
54 method/formulation/CFL combinations using matched numerical accuracy and five
interleaved live timings on each of four initial conditions.

On the tested Apple M1 Max, physical-time throughput is **7.4% higher with the
live view** than the headless Metal predecessor, or **17.9% higher headless**.
Live presentation medians are 56.8–58.0 fps against a 60 Hz target. The live app
is 5.77× the Astra adapter's throughput at its original settings; Astra exceeds
the accepted error budget on two of the four initial conditions, so that ratio
is not an accuracy-matched speedup. See the [complete measured comparison](Benchmarks/full-comparison-2026-09-09/report.md)
for rates, error estimates, frame intervals, provenance, and scope.

The [release verification](Benchmarks/release-2026-09-09.md) records the native
40-turnover run, accuracy checks, and exact output/controls checks of the packaged
app, with a [rendered native flow](Benchmarks/release-bundle-2026-09-09/native-gui/native.png).

## Build and run

Requires Apple silicon, macOS 15 or later, and Xcode with its macOS/Metal tools.
The main app has no Swift package dependencies. The pinned, patched VkFFT 1.3.4
and metal-cpp headers are included under `ThirdParty` with their licenses.

```sh
cd 2DFastTurbo
bash Scripts/build.sh
open /tmp/2DFastTurboDerived/Build/Products/Release/2DFastTurbo.app

# Copy a self-contained, locally signed bundle into this project.
bash Scripts/package.sh
open Build/2DFastTurbo.app
```

Alternatively, open `2DFastTurbo.xcodeproj`, select the `2DFastTurbo` scheme, and
Run. The shared scheme runs Release without the debugger. Build artifacts default
to `/tmp/2DFastTurboDerived`; override `FAST_TURBO_DERIVED_DATA` to change that.
`Scripts/generate_project.py` regenerates the checked-in project without external
tools when adding or removing source files.

The window opens before GPU initialization. The solver then runs on a background
worker, independently of the display clock. Pause freezes physical progress;
single-step advances exactly one complete timestep. Reset reproduces the selected
seed. Resolution, initial spectrum scale, and seed define a new initial-value
problem when applied. CFL (1–3) and manual Reynolds number (1,000–1,000,000,
logarithmic) use sliders and take effect after the current GPU batch without
resetting the field, time, or step count. CFL also updates live while Reynolds
number is regulated by palinstrophy. Turning regulation off holds its current Re
for manual adjustment. Slider readouts update independently of the rest of the
window. Drag events are combined into at most 30 solver updates per second, with
the final value sent immediately on release; paused edits reuse the existing
frame instead of preparing another GPU snapshot.

For Vorticity, the color-range menu selects **Sigma** (±1.5–3.5σ around the mean)
or **Exposure** (0.25–3× soft contrast). Sigma is the default. Each slider retains
its value when switching, and both update the display without resetting the flow.
PNG export uses the selected mapping. Speed and Enstrophy use Exposure.

| Shortcut | Action |
| --- | --- |
| Space | Pause or resume |
| Right arrow | Advance one complete timestep |
| R | Reset the current initial condition |
| Command–Shift–R | Start with the next seed |
| F | Toggle the focused flow view |
| Command–S | Export the displayed field as PNG |

Minimizing, hiding, or closing the window lets the simulation continue while
suppressing display work. Reopen it from the Dock or View → Show Simulation.

## Numerical model

The periodic domain is `[0, 2π)²`. Canonical vorticity is
`ω = ∂x v − ∂y u`, with `û = i ky ω̂/k²`, `v̂ = −i kx ω̂/k²`, and
`∂t ω = −u·∇ω + ν Δω`. Viscosity is `ν = 1/Re`; there is no forcing.

The default retained grid is **1024²**, with every nonlinear product evaluated on
**1536²** points. The supported interface resolutions are 128, 256, 512, 1024,
2048, and 4096. The full retained Fourier band is preserved, excluding the mean
and both Nyquist axes. Increasing padding does not reduce the resolved band.

The native initializer preserves Metal2DTurbo's seeded velocity realization,
with modal velocity power proportional to `exp(−k²/k₀²)`. The selection matrix
uses separate, shared physical fixtures with shell energy proportional to
`k⁴ exp(−2k²/k₀²)` and unit rms velocity. Every compared solver imports the same
file. Release checks additionally exercise the native initializer and its
exported field; matrix seeds and native seeds are not interchangeable fields.

The core uses two real inverse transforms to obtain velocity, forms `u²−v²` and
`uv`, and transforms those products back. Three-stage methods therefore require
twelve padded transforms per timestep; ETDRK4 requires sixteen. FFT plans and
working buffers persist across steps and resets. An optional advective formulation
instead transforms `u`, `v`, `∂xω`, and `∂yω` to physical space and transforms
`−u∂xω−v∂yω` back. It uses fifteen transforms for RK3 and twenty for ETDRK4.
This path avoids amplifying product-transform roundoff by two spectral derivatives
and supplies the full-resolution convergence reference. Both retain the same band.

Three methods are available for measured comparisons:

- The predecessor's low-storage IMEX RK3 with Crank–Nicolson diffusion.
- Integrating-factor Heun RK3, adapted from Astra onto the stress formulation.
- ETDRK4 with cancellation-resistant phi-function evaluation near zero.

CFL is reduced from padded physical velocities on the GPU at every step. Its
normalization matches Metal2DTurbo: `dt_target = 2*CFL/(N*max(|u|+|v|))`.
Decreases take effect immediately; growth follows the legacy 0.8/0.2 smoothing
rule. Every stage uses one immutable timestep. Command-buffer batch size does
not change this calculation. Fixed timesteps are available to validation tools.

Spectral updates retain a compensated Float32 remainder, and diffusion is applied
as a cancellation-resistant increment. This prevents small viscous changes from
vanishing when timesteps are refined; it adds 8 MiB at N1024. The GPU also maintains
a compensated physical clock. Interface statistics describe
completed work. Turnover counts and throughput use the **initial** turnover time
`τ₀ = 2π sqrt(E₀/Z₀) / sqrt(2E₀)`, so decay cannot change the normalization.
The two physical-throughput readouts average the entire run: completed automatic
simulation advance divided by active running wall time. Pauses and manual steps
are excluded, resume retains earlier history, and reset clears the average.
Integration steps/s remains a recent-rate measurement.

## Presentation and ownership

`FastTurboCore` owns simulation state and FFT plans; `SimulationController` owns
controls and publication; `TurboRenderer` consumes immutable field snapshots.
Three leased Metal buffers hold contiguous physical `[ω,u,v]` planes. The producer
completes before handing a frame to the renderer, and the renderer retains its
lease until its own command buffer retires. Busy slots are never overwritten.
An obsolete completion cannot change a replacement run's pause or run intent.
Synchronous inspection is safe inside a core completion handler. A failed GPU
update invalidates further state access until a successful reset, so callers
cannot mistake a partially updated field for a completed timestep.

Presentation targets 60 Hz. Simulation batches adapt between 1 and 32 steps toward
an 8 ms GPU budget, capped by the next presentation deadline. Spectral energy and enstrophy are inspected four times per
second. Vorticity/enstrophy display needs one inverse transform; velocity display
or flow lines needs three fields. Recoloring and PNG export reuse completed frames.

## Validation and benchmarking

```sh
# Hostless GPU lifecycle tests; actual GPU access is required.
bash Scripts/test.sh

# Build the independent command-line executable.
FAST_TURBO_SCHEME=2DFastTurboBench bash Scripts/build.sh

# Independent FP64 reference checks (developer tool requires NumPy).
python3 -m unittest discover -s Tests -p 'test_*.py' -v
python3 Scripts/validate.py --help

# Fixed physical endpoint, with warmup followed by a reset to the same field.
/tmp/2DFastTurboDerived/Build/Products/Release/2DFastTurboBench \
  --benchmark --resolution 1024 --turnovers 2 --batch 8 \
  --output /tmp/fast-turbo-result.json
```

The app executable accepts the same headless options. `--help` documents initial
field import, endpoint spectra, fixed timesteps, batch size, and output files.
Raw spectra are little-endian complex64 **normalized Fourier-series coefficients**
in `[ky,kx]` order; initial fields are little-endian Float32 physical `[y,x]` arrays.

The independent reference evaluates advective vorticity transport, rather than
copying the app's stress kernels. Tests include non-wrapping convolution, odd
padding, velocity/curl consistency, analytic diffusion, conservation, batch-size
invariance, exact physical endpoints, and immutable snapshot lifetimes.

See [the benchmark protocol](Benchmarks/README.md) for matched initial fixtures,
reference refinement, original-solver adapters, visible-app measurements, and
five-run comparisons. Numeric error and physical endpoints accompany timing;
steps/s alone is not an accuracy-matched speed comparison.

## Source provenance

The FFT bridge and initial solver formulation are derived from `Metal2DTurbo`.
The interface and field rendering are adapted from `MLXAstra`. Both source apps
remain independent of this project. Benchmark-only captures record their source
hashes and adaptation details; they are not linked into the application.
