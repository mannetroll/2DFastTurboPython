# Native Linux 2dbench

`2dbench` is a C++ executable using the production CUDA/VkFFT solver. Python, uv,
Qt, a display server, and reference data files are not required at build or
runtime. It produces the same architecture comparison as `uv run 2dbench`:
2048² → 3072² and 4096² → 6144², timed for 20 seconds per case.

## Build and run

Requirements: Linux, CMake 3.26+, a CUDA-compatible C++20 host compiler, CUDA
Toolkit 13.0+ including NVRTC and matching builtins, and a compatible NVIDIA
driver/GPU. VkFFT and the JSON header are vendored; the build downloads nothing.
Ninja is optional. Git is optional and used only to record build metadata.

From the repository root, for RTX 3090:

```sh
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build-linux --target 2dbench -j2
./build-linux/2dbench
./build-linux/2dbench --seconds 2
./build-linux/2dbench --sizes 1024 2048 4096 --json > benchmark.json
```

For H100, use `-DCMAKE_CUDA_ARCHITECTURES=90` in a separate build directory.
For a binary targeting both GPUs, use `-DCMAKE_CUDA_ARCHITECTURES='86;90'`.
The default is `86`. Other GPUs need an appropriate explicit CUDA architecture.
To select a toolkit, pass `-DCMAKE_CUDA_COMPILER=/path/to/cuda/bin/nvcc` and
`-DCUDAToolkit_ROOT=/path/to/cuda`. These are native CMake builds; no Python
packages or Python build scripts are involved.

## Install

```sh
cmake --install build-linux --component benchmark --prefix "$HOME/.local"
"$HOME/.local/bin/2dbench" --seconds 2
```

After installation, the executable runs from any directory without the source
checkout. Only `bin/2dbench` is installed. It links the CUDA runtime, driver,
NVRTC and standard Linux C/C++ libraries; matching NVRTC builtins are loaded
beside NVRTC. The configured CUDA library directory is retained in its runtime
search path. CUDA libraries are **not bundled**, so copying the executable to
another machine requires compatible runtime libraries there. No Python launcher
or Python shared library is used. Use `ldd build-linux/2dbench` to inspect links.

If a Python-installed `2dbench` is also on `PATH`, use the explicit native path
above to select this executable.

## Options and output

| Option | Default | Meaning |
| --- | --- | --- |
| `--seconds S` | `20` | Finite positive timed seconds per size |
| `--sizes N ...` | `2048 4096` | Even retained sizes from 16 through 16384, in supplied order |
| `--device I` | `0` | Nonnegative device index after `CUDA_VISIBLE_DEVICES` filtering |
| `--warmup K` | `8` | 0–4096 untimed warmup steps, followed by reset |
| `--json` | off | One JSON document on stdout |
| `-h`, `--help` | | Help without CUDA initialization |

Text output identifies the host, OS, GPU, memory, CUDA runtime/driver API,
VkFFT, float32 precision, compiled Git revision and workload. The header is
flushed before timing and each row appears as its case completes. Successful
2048 and 4096 cases also produce a 4096/2048 timestep ratio.

JSON keeps the Python command's schema version 1 and its result field names,
including `results`, `ms_per_step`, `steps_per_second`, `native_report`, and
`timestep_ratio_4096_2048`. The nested report contains the exact configuration,
initial/final diagnostics, initialization and GPU timings, and completed steps.
The solver identifies itself as `FastTurbo native Linux`. Git revision and dirty
state are captured at CMake configuration time, identified by
`git_metadata_source: "cmake-configure"`; reconfigure to refresh them. Missing
Git metadata is null. CUDA build architectures and the vendored VkFFT version
are embedded in the executable.

Each size runs in a separate native process. The parent never initializes CUDA;
all device allocations are released when a worker exits, including after an
error. Results travel through a pipe; native diagnostics go to stderr. Cases
that exceed a conservative lower bound on available device memory or fail
native allocation are `skipped`; other errors are `failed`. Later cases still
run, with no automatic size reduction or CPU fallback. Exit status is 0 when
every case succeeds, 1 for any failed/skipped case, or 2 for invalid arguments
or host setup errors.

## Workload and timing

The numerical implementation is the unchanged `reference-windows/src/solver.cu`,
`kernels.cuh` and `fft.cpp`, linked through `python/native/cuda/core.cmake`.
That directory name does not imply a Python dependency. No numerical code is
copied into the benchmark host, and production `--fmad=false` /
`-ffp-contract=off` settings are retained.

The configuration matches the Python architecture benchmark: float32,
ETDRK4/stress, 3/2 padding, seeded PAO initialization, k₀=10, seed=2, CFL=3,
Re=27898.36614974335, adaptive timesteps, regulation off, batch size 8.

The sequence is allocation/initialization → warmup → reset → initial inspection
→ timed batches → final inspection. All initialization, warmup, reset and
initial inspection are outside timing. Each batch waits for completed GPU work.
Synchronized wall time includes batch submission and the final diagnostic
inspection. A case completes at least one batch, and the time limit is checked
after each batch, so it can overshoot by one batch plus final inspection.
Warmup steps are excluded from reported completed steps. GPU event time is
also reported separately. The C++ host removes the Python/ctypes/JSON work
between batches, so measured wall time can differ despite the same workload
and timing boundaries.

`fastturbo_reference` remains available for the recorded Windows/macOS parity
checks and legacy CNAB2 interface. Its fixed-step benchmark advances warmup
without resetting and uses different workload defaults; use `2dbench` when
comparing with the Python architecture benchmark.

## Native tests

```sh
ctest --test-dir build-linux -R '^bench_' --output-on-failure
# CLI checks only, also usable without a visible GPU:
ctest --test-dir build-linux -R '^bench_cli$' --output-on-failure
```

These tests use CMake, with no Python test runner. They check invalid arguments,
missing and invalid devices, JSON/text output, multiple cases, actual GPU
progress, odd padded sizes, fixed workload settings, and identical initial
diagnostics with zero/eight warmup steps. On smaller GPUs, they also check that
a memory skip is followed by a successful case. GPU tests clear `PATH`, display
and loader environment variables. The broader numerical reference validation is available
with `cmake --build build-linux` followed by `ctest --test-dir build-linux`.
