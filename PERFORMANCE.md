# Measured performance and parity

Measured 2026-09-14 on this Apple M1 Max, 64 GiB, macOS 15.7.9, Python 3.13.11,
PySide6 6.11.2, Xcode Swift 6.2.4. These are new measurements of the supplied Swift
reference and this port, not the historical numbers in the reference README.

Five interleaved runs per host and mode, alternating execution order. Same native
seed-2 initialization, retained N1024 / padded M1536, k₀=4, fixed Re=50000,
ETDRK4, stress form, adaptive CFL=3, exact physical endpoint t=3.0, 1895 steps.
Headless: batch 8, 16 warmup steps followed by reset. GUI: native adaptive 8ms batch
policy, 2s warmup followed by reset, Vorticity/Ember/Sigma, 640×640-point
(1280×1280-pixel) HDR canvas. Regulation is disabled for matched workloads.
Initialization, export and startup are excluded. Headless final inspection is
included, matching Swift's benchmark. GUI uses the unchanged native active clock.

| Measurement (median of five) | Swift reference | Python port |
|---|---:|---:|
| Headless R_turbo, simulated time / wall s | 0.622551 | 0.620033 |
| Native GPU ms/step, headless | 2.484929 | 2.485180 |
| Live GUI R_turbo | 0.548862 | 0.557193 |
| Presented FPS | 57.244 | 60.230 |

`100 × (R_swift − R_python) / R_swift` gives **0.4045% headless overhead** and
**−1.5179% live overhead** (the port was faster in this sample). Do not interpret
the negative value as a general Python speedup: the native solver is the same,
and Qt versus SwiftUI presentation/scheduling and normal timing variability
influence the live measurement. Headless ranges: Swift 0.621346–0.623148,
Python 0.619133–0.623211. GUI ranges: Swift 0.544724–0.552201,
Python 0.555968–0.559309. All 10 paired endpoint spectra were **byte-identical**,
including matching step counts, simulation time, energy and enstrophy.

The GUI benchmark driver adds 50Hz telemetry polling solely to automate warmup
and endpoint detection. Normal launches use 4Hz Python dashboard polling. The
normal dashboard's measured share was 0.132–0.188% of active wall time in these
unprofiled live runs (median 0.140%). This excludes the benchmark driver's own
polling; that polling is included in the overall R_turbo measurement above.
FPS may slightly exceed 60 when dividing all frame presentations, including the
final frame, by the native active run clock. The display target remains 60Hz.

## Separate overhead measurements

After parity and timing, warmed N1024 native API calls were measured separately:

| Operation | Median wall time | Samples |
|---|---:|---:|
| Eight completed native steps | 20.3373ms | 100 |
| Spectral inspection | 0.2723ms | 100 |
| Vorticity-only snapshot | 0.3646ms | 100 |
| Snapshot including velocity | 0.4815ms | 100 |
| Zero-step native call including JSON/ctypes/dispatch | 0.02617ms | 2000 |

The no-op is an upper bound on bridge overhead, not pure Python cost: it also
reads the native clock and serializes statistics. The headless batch bridge cost
is small compared with about 20ms of native work. GUI integration does not make
these Python batch calls at all.

A separate cProfile run and a five-second macOS `sample` trace inspected the
running GUI. cProfile recorded 36 dashboard polls totaling 22ms over an 11.535s
launch/warmup/run session. There were no Python numerical kernels. The native
`2DFastTurbo.solver` queue had 1539 sampled stacks: 1438 were in the existing
Metal command-buffer wait during advance, with other stacks in native snapshot,
encoding and VkFFT functions. This is a CPU stack observation, not a per-kernel
GPU profiler: it locates the wait at the existing native batch boundary. The
recorded GPU durations show that GPU execution dominates total integration time.

The main remaining costs are native GPU FFT/compute work, then native snapshot,
rendering and scheduling work. No numerical or scheduling optimization was made
after parity because measured Python overhead was already small. Changing the
solver or introducing extra asynchronous buffering would require new numerical
and lifetime validation without an established benefit here.

## Verification results

* 40 Python pytest tests pass (39 numerical/API tests and one subprocess test of
  real Qt controls with the native pump/renderer).
* 18 trajectory comparisons span all three integrators, both nonlinear forms,
  fixed/adaptive dt, N32/M48, N34/M51 and N128/M192.
* Independent FP64 reference tests cover FFT scaling, k=0, Nyquist axes, Poisson
  inversion, curl/divergence, high-mode non-wrapping convolution, classical
  padding/truncation, all RK stages and energy/enstrophy evolution.
* Initialization/reset tests include UInt64 seeds 0, 2 and 2⁶⁴−1. Re/CFL edits
  preserve field, clock and allocations; batching is invariant; endpoint clipping
  reaches t=0.12345 in exactly 18 accepted steps at fixed dt=0.007.
* A 10,000-step N256 run remains finite at t≈20 with decaying E and Z.
* All fields, palettes, both color-range modes and flow overlays render without
  changing the solver. Explicit PNG exports were verified.
* The extended GUI check passed every supported resolution through N4096/M6144,
  a finite step at each size, latest-pause intent during replacement, frozen
  pause, one-step transport, retained throughput, live controls, UInt64 seed wrap,
  square resize/focus, close/reopen and continuing simulation while minimized.
* The reference's 10 native lifecycle/API tests and 18 Python reference/acceptance
  tests pass. The reference source tree was not modified.
* The source distribution and arm64 wheel build successfully. An isolated wheel
  install outside the repository runs Metal/VkFFT without compiling. The locally
  signed 100MiB `.app` runs through macOS Launch Services and exports a real Metal
  field; its headless output matches Swift byte for byte.
* The final packaged GUI also completed a 30-second default N1024 run with live
  palinstrophy regulation: 10,584 finite steps, t=19.04556 (about 12 initial
  turnovers), 1801 presentations at 60.03 FPS. Final E=0.48520 and Z=2.28681.

Summaries, exact benchmark commands, source hashes and the extended GUI checklist
are committed under `python/verification/`. Larger local spectra, PNGs, logs,
cProfile data and native sample traces live under ignored `python/results/`.
The run duration establishes tested stability, not a claim of indefinite uptime.

## Reproduction

From `python/`:

```sh
uv run python scripts/build_reference.py
uv run pytest -q
uv run python scripts/check_gui.py --all-resolutions
uv run python scripts/compare_performance.py
uv run python scripts/profile_overhead.py
uv run python -m cProfile -o results/gui-profile.pstats -m fastturbo \
  --gui-benchmark --resolution 1024 --reynolds 50000 --wall-seconds 6 \
  --output results/gui-profile.json
```

Run comparative benchmarks without other GPU workloads. Do not compare a paused,
hidden, different-sized or differently regulated GUI with the visible reference.
