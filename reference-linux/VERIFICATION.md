# Linux native reference verification — 2026-09-18

Implemented and executed in this repository on Ubuntu 26.04.1 LTS,
kernel 7.0.0-31-generic, NVIDIA GeForce RTX 3090 (device 0, sm_86).
CUDA reports 25,294,995,456 bytes of total device memory. Toolkit/nvcc
13.3.73, CUDA runtime 13.3, driver package 595.91.07, driver API 13.2,
GCC 15.2.0, CMake 4.4.3, vendored VkFFT 1.3.4. No H100 is installed.

## Implementation

- Root `CMakeLists.txt` and `reference-linux/CMakeLists.txt`: standalone Release
  build with explicit architecture selection, no Python or graphics discovery.
- `reference-linux/main.cpp`, `reference.hpp`, `validation.cpp`: CLI, synchronized
  production benchmark, historical positional output, JSON, and native parity.
- `python/native/cuda/core.cmake`: shared target definitions used by both the
  native reference and `python/native/linux/CMakeLists.txt`.
- `python/native/cuda/legacy_pao.cpp`, `legacy_cnab2.hpp`, `legacy_cnab2.cu`:
  the previously missing native historical PAO/CNAB2 method, sharing the
  production VkFFT wrapper. Its distinct initializer and controller preserve
  the macOS tools/sim contract; production integrators are unchanged.
- Native CTest checks and optional `reference-linux/tests/test_reference.py`:
  device/CLI/failure checks, independent initial arrays, full binding state
  parity, canonical log comparison, loaded-library inspection, and rejection
  of deliberately altered parity evidence.

`reference-windows/` and `reference-macos/` have **no modifications**. Their
production solver, CUDA kernels, spectral operations, constants, initialization,
and integrator/controller behavior are compiled directly from the existing source.
The native reference contains no copied production solver, Python bridge, or UI.

## Results

Release **sm_86 build: PASS**. Native reference CTest **4/4 PASS**.
Optional reference Python checks **15/15 PASS** (8 full-state cases, 7 legacy/
runtime checks). Existing relevant Python tests **46 PASS**, four rendering tests
deselected. Existing native core lifecycle CTest **1/1 PASS**. The independent
CUDA device test allocated GPU memory, executed a kernel, synchronized, and read
back 42 as expected. No CUDA test was skipped or replaced with CPU execution.

### Numerical validation

| Case / reference | max_abs | max_rel | RMS | Status |
|---|---:|---:|---:|---|
| Recorded Windows N1024, 3832 ETDRK4/stress steps; 12 initial/final diagnostics | 0.0247345 | 1.87214e-6 | 0.00714023 | PASS |
| Analytic Fourier diffusion, all six production integrator/form combinations; full spectrum | 4.82921e-7 | 4.26856e-8 | 3.24280e-8 | PASS |
| Python/Linux binding spectra **and** physical fields; N34 across six combinations, N1024/N2048 ETDRK4/stress | 0 | 0 | 0 | PASS |
| Historical N512 seed-1 PAO initial omega, original Python coefficient generator | 6.14391e-8 | 1.19886e-6 | 1.01807e-9 | PASS |
| Historical N512 seed-1 PAO initial velocity coefficients | 7.67988e-9 | 1.21127e-6 | 7.13019e-11 | PASS |
| macOS N512/1001 CNAB2 log; 67 scalar comparisons, full native precision versus printed reference | 9.20156e-5 | 2.21519e-5 | 1.94965e-5 | PASS |

The scalar aggregates mix diagnostic units; individual errors are retained in
[validation.json](evidence/validation.json). Windows's largest absolute error is
palinstrophy (about 13,212); it passes the unchanged `max(1e-9, 3e-6*abs(reference))`
criterion. Scalar max-relative excludes zero reference values; those still use
the absolute bound. Legacy log tests use the existing 1e-5 relative + 5e-10
absolute + printed rounding convention. Coarsely printed DT explains its raw
max-relative value above 1e-5; no tolerance was relaxed. The original log
comparator also passes [all printed checkpoints](evidence/legacy-comparison.txt).

There are no stored Windows/macOS final field fixtures for these historical
runs. Cross-platform full-state parity is **not claimed**. Stronger full-state
checks use the real Python/Linux native binding, and independent analytic
diffusion/initial-coefficient checks. All eight binding cases have exactly zero
error on this host. Bitwise identity is not required of H100 or other GPUs.

### Canonical legacy case

Exact positional semantics: seed 1, N512, Re10000, K0=10, 1001 steps, CFL0.25,
CUDA, update100, PAO, CNAB2. See the [complete log](evidence/legacy-512.txt) and
[full-precision JSON](evidence/legacy-512.json).

```text
Energy      = 0.9968169
WiWi        = 100
Time scale  = 49.840846
Kolmogorov  = 0.0031622776
Viscosity   = 9.9999997e-05
dx/Kol.     = 3.8806987
PAO seed in = 1
PAO seed out= 149
[NEXTDT INIT] CFLM=   360.4001 DT=  0.0002208 CN=  1.0000000
ITERATION   1000 T=0.2297712721 DT=0.00024181 CN=1.00643372 CFLM=320.879303 TAU_L=1.085964
ITERATION   1001 T=0.2300115412 DT=0.00024305 CN=1.00512795 CFLM=320.857605 TAU_L=1.086023
Final T=0.23025336  CN=1.0051279  DT=0.00024305486
```

Signed deltas below use full Linux precision minus the stored macOS log's
printed value, not the approximate numbers in the task prose. The stored log
ends at T=0.23025338, CN=1.005128, DT=0.00024305488.

| Diagnostic | macOS stored reference | Linux full precision | Linux − reference |
|---|---:|---:|---:|
| Initial Energy | 0.9968169 | 0.996816897712121 | -2.28788e-9 |
| Initial DT | 0.0002208 | 0.000220803105443237 | +3.10544e-9 |
| Iteration 1000 T | 0.2297712941 | 0.229771272141653 | -2.19583e-8 |
| Iteration 1000 DT | 0.00024181 | 0.000241814846876854 | +4.84688e-9 |
| Iteration 1000 CN | 1.00643373 | 1.00643372263366 | -7.36634e-9 |
| Iteration 1000 CFLM | 320.879272 | 320.879302978516 | +3.09785e-5 |
| Iteration 1000 TAU_L | 1.085964 | 1.08596421428085 | +2.14281e-7 |
| Final T | 0.23025338 | 0.230253356011157 | -2.39888e-8 |
| Final DT | 0.00024305488 | 0.000243054860451376 | -1.95486e-11 |
| Final CN | 1.005128 | 1.00512794640419 | -5.35958e-8 |

Every comparison passes the project's rounding-aware tolerance. This run
calculates its own CUDA trajectory; no expected result enters the solver.

### RTX 3090 production benchmark

Three sequential runs per size, **100 warmup + 1000 timed steps**, native batch8,
seed2, Re50000, k0=4, CFL3, ETDRK4/stress, float32. Warmup is not reset.
Validation runs before initialization/timing. Context, plans, allocation,
initialization, inspections, exports, and file I/O are outside the timed loop.
No other solver benchmark ran concurrently. Desktop processes were still
resident, as recorded by [nvidia-smi](evidence/nvidia-smi.txt).

| N | Padded | Median wall ms/step | Median steps/s | Median CUDA-event ms/step |
|---:|---:|---:|---:|---:|
| 1024 | 1536 | 1.258668 | 794.491 | 1.257450 |
| 2048 | 3072 | 4.858169 | 205.839 | 4.856974 |

N1024 wall range: 1.257238–1.259954 ms/step; N2048: 4.854491–4.859927.
Every report confirms exactly 1000 timed and 1100 total CUDA solver steps,
finite final diagnostics, and passing numerical validation. Raw runs:
[1024-1](evidence/benchmark-n1024-1.json), [1024-2](evidence/benchmark-n1024-2.json),
[1024-3](evidence/benchmark-n1024-3.json), [2048-1](evidence/benchmark-n2048-1.json),
[2048-2](evidence/benchmark-n2048-2.json), [2048-3](evidence/benchmark-n2048-3.json).
These are production solver timings, not legacy CNAB2 or synthetic FFT timings.

### H100 readiness

| Claim | Result |
|---|---|
| Explicit architecture 90 build support | PASS |
| Release sm_90 compilation | PASS |
| Native sm_90 cubins for production and legacy CUDA kernels | PASS; [cuobjdump](evidence/sm90-images.txt) |
| H100 runtime execution | NOT TESTED |
| H100 numerical parity | NOT TESTED |
| H100 benchmark | NOT TESTED |

No H100 optimization was introduced. Mac/Windows runtime regression tests were
not executed on those operating systems; their source/build trees are unchanged.

### Dynamic dependencies

[`ldd`](evidence/ldd.txt) reports `libcudart.so.13`, `libcuda.so.1`,
`libnvrtc.so.13`, `libstdc++`, `libm`, `libgcc_s`, `libc`, `libdl`, `libpthread`,
`librt`, and the ELF loader. No Python/CPython, Qt/PySide6, X11/XWayland, Wayland,
OpenGL/EGL, or Vulkan library is linked.

The [loaded-process library check](evidence/runtime-libraries.json) during a real
CUDA/VkFFT run also passes. Dynamic additions are NVIDIA's compiler/JIT modules
and matching NVRTC builtins, with no desktop libraries. DISPLAY, WAYLAND_DISPLAY,
XDG_RUNTIME_DIR and LD_LIBRARY_PATH were removed for all archived legacy,
validation, binding comparisons and production benchmark invocations.

## Commands executed

All paths are relative to the repository root unless explicitly absolute.
Logs were redirected into `reference-linux/evidence`; `.xml`, `.json`, and `.txt`
evidence is retained in the repository.

```sh
cmake -S . -B build-linux -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build-linux -j2
cmake -S . -B build-h100 -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=90
cmake --build build-h100 -j2
ctest --test-dir build-linux --output-on-failure \
  --output-junit "$PWD/reference-linux/evidence/ctest.xml"

python/.venv/bin/python -m fastturbo.build --force
FASTTURBO_REFERENCE_EVIDENCE=reference-linux/evidence \
  python/.venv/bin/python -m pytest -q reference-linux/tests/test_reference.py \
  -k full_state --junitxml=reference-linux/evidence/binding-tests.xml
FASTTURBO_REFERENCE_EVIDENCE=reference-linux/evidence \
  python/.venv/bin/python -m pytest -q reference-linux/tests/test_reference.py \
  -k 'legacy or runtime' --junitxml=reference-linux/evidence/legacy-tests.xml
FASTTURBO_EVIDENCE_DIR=reference-linux/evidence/python \
  python/.venv/bin/python -m pytest -q python/tests/test_numerics.py \
  python/tests/test_linux.py python/tests/test_build_linux.py -k 'not rendering' \
  --junitxml=reference-linux/evidence/python-tests.xml
ctest --test-dir python/build/linux --output-on-failure
./python/build/linux/device_test

SCIPYTURBO_SEED=1 env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR -u LD_LIBRARY_PATH \
  ./build-linux/reference-linux/fastturbo_reference 512 10000 10 1001 0.25 cuda 100 PAO CNAB2 \
  --output reference-linux/evidence/legacy-512.json > reference-linux/evidence/legacy-512.txt
python/.venv/bin/python reference-macos/2DFastTurbo/tools/compare_sim.py \
  reference-macos/2DFastTurbo/tools/sim_512_1001_metal_macOS.txt \
  reference-linux/evidence/legacy-512.txt
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR -u LD_LIBRARY_PATH \
  ./build-linux/reference-linux/fastturbo_reference --validate --json \
  --output reference-linux/evidence/validation.json

for n in 1024 2048; do
  for run in 1 2 3; do
    env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR -u LD_LIBRARY_PATH \
      ./build-linux/reference-linux/fastturbo_reference --n "$n" --reynolds 50000 \
      --k0 4 --cfl 3 --seed 2 --batch 8 --warmup 100 --steps 1000 --benchmark --json \
      --output "reference-linux/evidence/benchmark-n${n}-${run}.json"
  done
done
ldd build-linux/reference-linux/fastturbo_reference
/usr/local/cuda-13.3/bin/cuobjdump --list-elf build-linux/reference-linux/fastturbo_reference
/usr/local/cuda-13.3/bin/cuobjdump --list-elf build-h100/reference-linux/fastturbo_reference
nvidia-smi
git diff --check
```

The final CLI formatting correction was checked by rerunning help/CLI CTest,
the canonical invocation and its original comparator, and a zero-step legacy
initialization with Re=12345.678/K0=7.55 to verify unrounded parameter reporting.

## Remaining limitations

H100 runtime/parity/performance and new macOS/Windows runtime executions remain
unverified. Stored historical cross-platform full-state snapshots are unavailable.
The standalone executable's built-in recorded-data validation requires this
checkout (or `--reference-root` pointing to it). These are the only outstanding
validation/deployment limits identified for this task.
