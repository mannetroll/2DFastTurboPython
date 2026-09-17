# Measured performance and parity

## Native Ubuntu CUDA/VkFFT + OpenGL, 2026-09-17

Ubuntu 26.04.1, RTX 3090, driver 595.91.07, CUDA 13.2.51, GCC 15.2,
Python 3.14.4. Three alternating headless/GUI five-second samples per grid;
seed 2, k0=4, Re=50000, CFL=3, ETDRK4/stress. Headless: batch8 and eight warmup
steps/reset. GUI: native adaptive batches, two-second warmup/reset, 640-pixel
Vorticity/Ember/Sigma canvas through Xwayland/GLX. Medians:

| Retained → padded | Headless ms/step | Headless steps/s | Headless R_turbo | GUI ms/step | GUI steps/s | GUI R_turbo | GUI overhead |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1024² → 1536² | 1.386244 | 721.374 | 1.198584 | 1.482454 | 674.557 | 1.129336 | 5.78% |
| 2048² → 3072² | 5.422594 | 184.414 | 0.164884 | 6.030713 | 165.818 | 0.147771 | 10.38% |

The GUI overhead formula uses R_turbo. Presentation remains approximately 60 Hz
and does not bound solver throughput. Native GPU event intervals occupy
99.66%/99.90% of headless wall time. A separate N1024 profile gives batch8
11.10328 ms, inspection 0.03277 ms, vorticity snapshot 0.08068 ms, velocity snapshot
0.15855 ms and no-op ABI upper bound 0.00866 ms. CUDA/OpenGL map/copy/unmap/wait
intervals totaled 0.41949/0.51091 seconds for 301 uploads in representative GUI
runs; these overlap native simulation and must not be added to GUI overhead.
No per-kernel bottleneck or optimization is claimed.

Historical Windows headless R_turbo is higher by the equivalent of a 4.14%/6.80%
Linux shortfall. These are matched physical configurations across different
OS/driver/toolchain/date, not an isolated OS experiment. Correctness: 48 Python
tests passed, 12 Windows-only skips; native CTest passed; 52 GUI assertions and
24 independent pixel comparisons passed. WSLg and H100 execution remain separate
limitations; native Ubuntu GUI results do not establish WSLg support.

Final raw timing records and comparison: [`python/evidence/linux-native/final`](python/evidence/linux-native/final).
The parent directory retains the first round and validation/profiling evidence.
Build/run details: [LINUX_PORT.md](LINUX_PORT.md).

## Linux/WSL CUDA/VkFFT, 2026-09-16 — headless milestone

Ubuntu 24.04.5/WSL2 on Windows 10 Pro, RTX 3090, CUDA 13.3.73, GCC 13.3,
CPython 3.14.6. Three sequential five-second runs at each resolution, seed 2,
k0=4, Re=50000, CFL=3, ETDRK4/stress, batch 8, eight warmup steps followed by
reset. The same configuration is used by the Windows results below.

| Retained / padded | Median ms/step | Median steps/s | Median R_turbo |
|---|---:|---:|---:|
| 1024 / 1536 | 1.334983 | 749.0734 | 1.239378 |
| 2048 / 3072 | 5.044150 | 198.2495 | 0.178010 |

R_turbo is 0.876% lower and 0.622% higher respectively than the stored Windows
Python medians. Different OS/toolchain/date, matched physical configuration;
these are not interleaved measurements. No major compute regression was found.
Native GPU-event intervals occupy 94.18%/96.14% of representative wall times.

An independent later API profile measured median batch8 11.58094 ms, inspection
0.08709 ms, vorticity snapshot 0.14112 ms, velocity snapshot 0.22489 ms and no-op
ABI upper bound 0.01747 ms. Nsight recorded 67 stream synchronizations taking
657.506 ms in a 512-step capture. Its installed tracing libraries warn about the
newer 13.4 driver and omit GPU kernel/memcpy activity tables, so per-kernel times
and transfer sizes are not claimed. The dominant measured work is native GPU
execution/waiting; no speculative solver optimization was applied.

The headless tests pass, and the 3,832-step stored native Windows trajectory
matches to a maximum diagnostic relative error of 1.87215e-6. The full suite
retains two failing GUI/render tests. **No interactive R_turbo or GUI-overhead
measurement exists:** the tested GPU-backed WSLg contexts reject CUDA sharing.
No H100 measurement is claimed. Exact commands, raw timing/diagnostic JSON,
profile summaries and interop evidence are in
[`python/evidence/linux`](python/evidence/linux) and [LINUX_PORT.md](LINUX_PORT.md).

## Windows CUDA/VkFFT, 2026-09-14

Built and tested on Windows 10 Pro build 19045, RTX 3090, NVIDIA driver 616.56,
CUDA 13.0.88, MSVC 19.44, Python 3.13.9 and PySide6 6.11.2. Both hosts link the
same unchanged Release CUDA/VkFFT core, with the reference floating-point flags.
Three interleaved five-second runs per host/mode/resolution, with no concurrent
GPU tests. N is retained, M=3N/2. Seed=2, k0=4, Re=50000 (regulation disabled),
CFL=3, ETDRK4/stress. Headless uses batch 8 and eight warmup steps followed by
reset. GUI uses automatic native batching, a two-second warmup followed by reset,
and a 640-point Vorticity/Ember/Sigma canvas. Startup, plan creation and exports
are excluded; final inspection and normal rendering/telemetry costs are included.

| N / mode | Native steps/s | Python steps/s | Native R_turbo | Python R_turbo | Native ms/step | Python ms/step | Host time overhead |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1024 headless | 760.17 | 756.23 | 1.256196 | 1.250329 | 1.31549 | 1.32235 | +0.52% |
| 1024 GUI | 692.12 | 698.42 | 1.155744 | 1.165038 | 1.44483 | 1.43180 | -0.90% |
| 2048 headless | 197.67 | 197.02 | 0.177490 | 0.176909 | 5.05893 | 5.07554 | +0.33% |
| 2048 GUI | 184.95 | 185.24 | 0.165345 | 0.165615 | 5.40692 | 5.39838 | -0.16% |

Values are medians. R_turbo is simulated time per wall second. Host time overhead
is `100*(Python ms/step / native ms/step - 1)`; the corresponding headless
R_turbo losses are 0.47% and 0.33%. Negative GUI values indicate a small measured
advantage in this sample, not a general Python speedup. In particular, N2048 GUI
R_turbo ranges overlap: native 0.165248–0.167117, Python 0.163042–0.167769.

The initial child-window renderer submitted duplicate frames without a native
deadline and reduced N1024 GUI R_turbo from about 1.153 to 0.830. An isolated
change restored the reference's 60 Hz deadline and scoped timer precision.
Correctness was rerun, then the interleaved comparison repeated. This change was
kept; no numerical kernel, FFT plan or physics parameter was altered. The native
worker test now observes 36 presentations while Python processes no events for
0.6 seconds, while thousands of complete N128 timesteps continue.

Separate N1024 profiling measured median batch-8 work at 10.35585 ms, inspection
at 0.09525 ms, vorticity snapshots at 0.12490 ms, velocity snapshots at 0.20240 ms,
and a coarse no-op ABI call at 0.02170 ms. That last value includes JSON, ctypes,
native dispatch and response allocation; it bounds the control-call cost rather
than isolating Python alone. The GUI timestep and render scheduling are native;
the Python dashboard polls at 4 Hz. The benchmark driver additionally polls at
50 Hz, included in the measured host cost.

Validation: **52 Python tests, 2 native CTest checks, 57 GUI checks**, all passing.
Windows spectra are bitwise equal to the native executable for all six
integrator/form combinations and at N1024/N2048. Independent FP64 checks cover
normalization, zero/Nyquist modes, padding/truncation and evolution. Independent
shader equations validate all 24 field/palette/range combinations. The portable
EXE and wheel also pass without CUDA Toolkit paths; frozen GUI and headless
spectra match the native executable exactly.

Reproduce with `python/scripts/benchmark-windows.ps1`. Compact evidence, raw run
records, binary hashes, GUI captures and runtime audits are in
[`python/evidence/windows`](python/evidence/windows), including
[performance.json](python/evidence/windows/performance.json) and
[validation.json](python/evidence/windows/validation.json).

## Preserved macOS measurements

Measured 2026-09-14 on Apple M1 Max, 64 GiB, macOS 15.7.9, Python 3.13.11,
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
