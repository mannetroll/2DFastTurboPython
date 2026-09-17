# 2dbench implementation and verification

Tested on 2026-09-18 (Europe/Stockholm), source base commit
`5be033e30759` with the changes in this working tree (reported as dirty).

## Implementation

`uv run 2dbench` reuses `fastturbo.main.run_benchmark`, extracted from the
existing headless `--benchmark` function. The original command still calls that
same function and emits its existing report, with an added initialization time.
There is one Python timing loop, driving the unchanged native `Engine.advance`
and `Engine.inspect` paths. No solver, FFT, initializer, integrator, padding,
controller scheduling, or reference numerical source was changed.

CUDA `Solver::advance` records native events and synchronizes the stream while
reading its clock. Metal `advanceSync` runs the existing core, whose command
submission waits for command-buffer completion. Both synchronize inspection
before and after the timed region. The displayed metric uses the existing
headless harness's wall time, including submission and final inspection;
native GPU intervals are also archived in JSON. Warmup/reset/initialization
and process startup are outside the measured interval.

Each case is a fresh worker process for resource cleanup after GPU failures.
The wrapper selects a native device before initializing the backend, performs
a conservative buffer-memory preflight, invokes the shared harness, and formats
its result. A failed/skipped case does not stop subsequent cases; any incomplete
run exits with status 1. JSON is transported from workers in temporary files so
native/build stdout cannot contaminate the final JSON document.

Files created:

- `python/src/fastturbo/bench.py`: CLI, process orchestration, metadata, memory
  preflight, table/JSON output and scaling ratio.
- `python/src/fastturbo/build_info.py`: read the compiled VkFFT header version.
- `python/tests/test_bench.py`: 31 unit cases and two short GPU integration cases.
- `python/evidence/2dbench/`: this report and the exact verification artifacts.

Files modified:

- `python/pyproject.toml`: `2dbench` console entry point.
- `python/src/fastturbo/main.py`: expose the existing benchmark as a reusable
  function returning its report; preserve the original CLI wrapper.
- `python/src/fastturbo/build.py`, `build_linux.py`, `build_windows.py`: record
  VkFFT version; CUDA source builds default to CMake native architecture
  detection and retain explicit architecture overrides.
- `python/native/cuda/bridge_common.hpp`: CUDA device selection and metadata.
- `python/native/linux/bridge.cpp`: select the requested compute device and
  report its available memory, device index and precision.
- `python/native/windows/bridge.cpp`: direct compute-device selection without
  creating a DXGI factory for the benchmark; report CUDA metadata.
- `python/native/PortBridge.swift`: pass the selected Metal device to the
  existing core, report memory metadata, and create NSApplication only for UI.
- `README.md`, `python/README.md`, `LINUX_PORT.md`: workflow, benchmark definition,
  options, failure behavior, and current source-build architecture default.

## Tests

Commands below ran from `python/`, except the explicit cross-build command.
Display variables were removed for GPU Python tests and benchmark runs.

```sh
uv run fastturbo-build
uv run pytest tests/test_bench.py -m 'not gpu' -q
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR uv run pytest \
  tests/test_bench.py tests/test_build_linux.py tests/test_linux.py \
  tests/test_numerics.py -m 'not gui' -k 'not rendering_all_choices' -q \
  --junitxml=evidence/2dbench/tests.xml
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR uv run pytest \
  ../reference-linux/tests/test_reference.py -q \
  --junitxml=evidence/2dbench/reference-tests.xml
ctest --test-dir build/linux --output-on-failure
ctest --test-dir ../build-linux --output-on-failure
uv run 2dbench --help
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR uv run 2dfastturbo \
  --benchmark --resolution 32 --steps 17 --warmup 8
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR uv run 2dbench --seconds 2
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR uv run 2dbench
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR uv run 2dbench --json
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR uv run 2dbench \
  --sizes 16384 32 --seconds .03 --json
```

- Build: passed; the installed library contains native `sm_86.cubin`.
- Unit subset: **31 passed**, two GPU tests deselected.
- Main test group: **79 passed**, four GUI/rendering tests deselected, 33.15 s.
- Standalone reference/legacy group: **15 passed**, 70.96 s.
- Binding native CTest: **1/1 passed**.
- Standalone reference CTest: **4/4 passed**.
- Help, two-second cases, default cases, and JSON: passed.
- Original `2dfastturbo --benchmark`: exactly 17 timed steps after warmup/reset,
  finite diagnostics and positive GPU time; [report](existing-benchmark.json).
- Actual memory preflight: N=16384 skipped for insufficient memory, then N=32
  completed; command returned the expected status 1. See
  [memory-skip.json](memory-skip.json) and [formatted result](memory-skip.txt).
- Full JSON archive parsed successfully; both durations exceed 20 seconds,
  counts are positive multiples of eight, throughput fields match elapsed
  time/count, GPU timing is positive and final diagnostics are finite.

The new GPU smoke tests use N=32 and 0.03 seconds. They verify completed native
steps, positive GPU timing, reset after warmup, valid JSON, invalid-device
reporting, and no Qt imports. The worker test removes DISPLAY, WAYLAND_DISPLAY,
XDG_RUNTIME_DIR and LD_LIBRARY_PATH and inspects Linux process mappings to
exclude Qt, X11, Wayland, GL, EGL and the native renderer module.

Existing checks include the unchanged generated controller hashes, numerical
tests for all three integrators and both nonlinear forms, full-state reference
comparisons, recorded Windows scalar trajectory, and historical PAO/CNAB2
initializer, deterministic trajectory and canonical 512/1001-step log checks.
The four omitted tests exercise graphics and are outside this headless change.

## Available-machine benchmark

Exact default terminal run: [default.txt](default.txt). A separate full-duration
JSON run is archived in [default.json](default.json); its measurements are an
independent run, not the underlying data for the table below. The quick run is
in [quick.txt](quick.txt).

Host `ubuntu`, Ubuntu 26.04.1 LTS, x86_64, NVIDIA GeForce RTX 3090 (compute 8.6),
23.6 GiB exposed device memory, CUDA 13.3 runtime / 13.2 driver API, VkFFT 1.3.4,
float32. ETDRK4/stress, k0=10, seed=2, CFL=3, Re=27898.36614974335, batch=8,
eight warmup steps followed by reset, 20 seconds per case.

```text
 Retained      Padded      Steps    Seconds    ms/step    steps/s
----------------------------------------------------------------
 2048²        3072²          4128      20.04      4.854      206.0
 4096²        6144²           928      20.13     21.689       46.1
4096/2048 timestep ratio: 4.47x
```

No GPU tests or compilation ran concurrently with either full-duration benchmark.

## Architecture support

The table describes this change's verification, not the prior validation of the
underlying production implementations.

| Platform | Implemented | Compiled here | Executed here | Numerically verified here |
| --- | --- | --- | --- | --- |
| Linux CUDA, RTX 3090 / sm_86 | Yes | Yes | Yes, both sizes | Yes, existing numerical/reference/parity suites |
| macOS Metal | Yes | No | No | No |
| Windows CUDA | Yes | No | No | No; stored Windows trajectory compared on Linux only |
| Linux H100 / sm_90 | Yes, same CUDA path | Yes, separate native library | No H100 available | No |

H100 cross-build, from the repository root:

```sh
cmake --fresh -S python/native/linux -B python/build/linux-sm90 -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=/usr/bin/c++ \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.3/bin/nvcc \
  -DCUDAToolkit_ROOT=/usr/local/cuda-13.3 -DCMAKE_CUDA_ARCHITECTURES=90 \
  -DFASTTURBO_WINDOWS=/home/tobbe/github/2DFastTurboPython/reference-windows \
  -DFASTTURBO_VKFFT=/home/tobbe/github/2DFastTurboPython/reference-macos/2DFastTurbo/ThirdParty/VkFFTSource/vkFFT \
  -DFASTTURBO_RENDERER=OFF
cmake --build python/build/linux-sm90 --parallel 2
/usr/local/cuda/bin/cuobjdump --list-elf python/build/linux-sm90/libfastturbo.so
```

Result: native `libfastturbo.1.sm_90.cubin`. See [build log](sm90-build.txt) and
[image listing](sm90-images.txt). The installed RTX 3090 library was preserved.
The separate existing `build-h100` reference executable was also checked and
contains two sm_90 images. No H100 execution is inferred from these builds.
