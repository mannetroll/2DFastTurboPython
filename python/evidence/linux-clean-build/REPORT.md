# Reproducible native Linux clean build — 2026-09-17

The controller-path failure is fixed in the Linux build driver. Two clean
launches rebuilt all 17 native targets/actions and opened the existing shared
GUI on the physical RTX 3090. The second launch explicitly removed CUDA shell
overrides and retained a staged native library with an identical build manifest;
it still rebuilt from scratch after `.venv` and `build/linux` were removed.

## Root cause

`controller.cmake` expands `${FASTTURBO_WINDOWS}/src/controller.cpp`. The source
files exist, and the original CMake invocation supplied the correct directory.
The failure occurred during **CMake's automatic second configuration pass**:

1. The prior build cached `/usr/local/cuda-13.3/bin/nvcc`. A manual launch without
   `CUDA_HOME` selected `/usr/local/cuda/bin/nvcc`. These paths identify the same
   compiler, but their spellings differ.
2. The original builder canonicalized the toolkit path in its manifest, but
   passed the unresolved compiler/toolkit paths to CMake.
3. CMake detected a changed `CMAKE_CUDA_COMPILER`, discarded its cache and
   restarted configuration. That restart dropped other supplied `-D` values,
   including `FASTTURBO_WINDOWS`, `CUDAToolkit_ROOT`, Release and architecture 86.
4. With `FASTTURBO_WINDOWS` now empty, the controller source became
   `/src/controller.cpp`. The failed cache also showed an empty build type and
   architecture 75. The supplied path was correct on the first pass.

Removing only `.venv` retains this native CMake cache. A genuinely empty native
build directory configured successfully even before the fix; the compiler-path
transition reproduced the exact reported `/src/controller.cpp` and
`/src/controller.hpp` errors. See [clean configure control](reproduce-clean.log),
[compiler-change reproducer](reproduce-compiler-change.log), and
[original failed cache](failed-CMakeCache.txt).

A second defect could conceal this issue during verification: the Python
builder accepted a matching staged library/manifest even when `build/linux`
had been deleted. A successful launch alone therefore did not prove a rebuild.
The earlier environment-upgrade report has been annotated with this limitation.

## Fix

- `python/src/fastturbo/build_linux.py` now canonicalizes toolkit, nvcc and C++
  compiler paths before both manifest generation and CMake invocation.
- Actual configuration uses `cmake --fresh`, which removes the old compiler
  cache **before applying all requested definitions**. CMake no longer starts
  an implicit second pass that loses the source roots or build settings.
- A source build can reuse the staged library only when `build/linux/build.ninja`
  also exists. Deleting the native build directory forces a rebuild.
- `python/native/cuda/controller.cmake` rejects an empty reference path or
  missing controller source before generating anything. Its existing guarded
  substitutions are unchanged. No generated controller files were supplied
  from a previous build.
- Three tests in `python/tests/test_build_linux.py` exercise actual CMake:
  a compiler-path change preserves source/Release/architecture/renderer settings
  and controller hashes; empty and nonexistent source roots fail even after a
  successful generation has left controller files in the build directory.

No numerical solver, GUI, tolerance, GPU capability or CPU fallback was changed.
No toolkit, driver or system package was changed during this fix.

## Clean-launch acceptance

The command tool rejected `rm -rf`. Instead, the existing generated directories
were moved to timestamped directories under `~/.cache/fastturbo-clean-build/`.
The required launch paths were asserted absent before running the exact launch
command. This gives the same empty build inputs while preserving backups.

First run: `.venv`, `build/linux` **and** `src/fastturbo/_native` were absent.
The configured shell then ran, from `python/`:

```sh
QT_QPA_PLATFORM=xcb uv run 2dfastturbo
```

It created `.venv`, compiled the solver, generated and compiled the controller,
linked both native libraries and both test/reference harnesses, and opened the
GUI. [Full 17-step launch log](clean-launch.log),
[verification](first-clean-launch.json), [window](clean-gui.png),
[GPU process listing](nvidia-smi-clean-gui.txt).

For the second run, the default-discovery configuration was built once to leave
a matching staged manifest. Then `.venv` and `build/linux` were removed from
their launch paths while `src/fastturbo/_native` was retained. The launch shell
explicitly unset `CUDA_HOME`, `CUDA_PATH`, `CUDAToolkit_ROOT`, `CUDACXX`,
`CUDAHOSTCXX`, `NVCC_CCBIN`, `CMAKE_PREFIX_PATH`, `LD_LIBRARY_PATH` and
`FASTTURBO_LINUX_RENDERER`, then ran the same exact command. Only the existing
`~/.local/bin` tools were added to PATH.

The retained manifest was identical to the manifest after the run, but all
17 build actions still executed. Native binaries and adapted controller files
had modification times after cleanup. The actual GUI process's environment
was checked to confirm the CUDA overrides were absent.
[Launch environment](second-launch-environment.json),
[clean-state record](second-clean-state.json),
[full launch log](second-clean-launch.log),
[verification](second-clean-launch.json), [window](second-clean-gui.png).

The fresh GUI was PID 49726, identified by its X11 `_NET_WM_PID` and by
`nvidia-smi` as **C+G**, using 748 MiB on the RTX 3090. It mapped CUDA 13.3,
Qt's XCB plugin and `libfastturbo_gl.so`. The screenshot shows the 2048² field
and advancing native simulation statistics.
[GPU process listing](nvidia-smi-second-clean-gui.txt).

## Build and validation

CUDA toolkit 13.3.1, nvcc 13.3.73, GCC/G++ 15.2.0, CMake 4.4.3, Ninja 1.13.2,
Python 3.14.4, driver 595.91.07. The final clean build uses:

```text
cmake --fresh -S python/native/linux -B python/build/linux -G Ninja
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.3/bin/nvcc
  -DCMAKE_CXX_COMPILER=/usr/bin/x86_64-linux-gnu-g++-15
  -DCUDAToolkit_ROOT=/usr/local/cuda-13.3
  -DCMAKE_CUDA_ARCHITECTURES=86
  -DFASTTURBO_RENDERER=AUTO
  -DFASTTURBO_WINDOWS=/home/tobbe/github/2DFastTurboPython/reference-windows
  -DFASTTURBO_VKFFT=/home/tobbe/github/2DFastTurboPython/reference-macos/2DFastTurbo/ThirdParty/VkFFTSource/vkFFT
cmake --build python/build/linux --parallel 2
```

`AUTO` found all graphics dependencies and built the renderer, with
`FASTTURBO_HAS_RENDERER=1`. C++20, CUDA C++20, `--fmad=false` and
`-ffp-contract=off` are unchanged. `cuobjdump` confirms native `sm_86.cubin`;
CUDA runtime/NVRTC resolve to the new toolkit.
[CMake cache](cmake-cache.txt), [manifest](build-manifest.json),
[GPU binary](cuda-binaries.txt), [linked libraries](linked-libraries.txt).

After the second clean launch, its GUI was stopped to isolate tests and timing:

```sh
ctest --test-dir build/linux --output-on-failure
QT_QPA_PLATFORM=xcb FASTTURBO_EVIDENCE_DIR="$PWD/evidence/linux-clean-build" \
  uv run pytest -q -ra --junitxml=evidence/linux-clean-build/final-tests.xml \
  --basetemp=build/final-clean-pytest
QT_QPA_PLATFORM=xcb uv run python scripts/benchmark_linux.py \
  --mode both --runs 3 --seconds 5 --resolutions 1024 2048 \
  --output evidence/linux-clean-build
```

- Native CTest: **1/1 passed**.
- Final pytest: **51 passed, 12 Windows-only skips, zero failures** (41.31 s).
  The first clean build also passed all 51 tests. [Final log](final-pytest.log),
  [JUnit report](final-tests.xml), [CTest](final-ctest.log).
- Fresh controller hashes are exactly the previously recorded Windows-adaptation
  hashes: [controller parity](controller-parity.json). No adaptation check was
  removed, bypassed or relaxed.
- Existing FP64, FFT/padding/Nyquist, integrator/form and long-run tests passed.
  N1024/N2048 spectra match the independent CLI invocation byte-for-byte.
- The stored 3,832-step Windows trajectory's maximum relative diagnostic error
  remains **1.8721444895292742e-6**, within the unchanged **3e-6** tolerance.
  [Windows parity](windows-parity.json).
- **52 GUI assertions** and **24 pixel comparisons** passed. Maximum pixel error
  remains **1/255**, within the unchanged **2/255** tolerance. Autonomous native
  worker, headless operation, actual X11 transport and failure-recovery checks
  also passed. [GUI](gui-controls.json), [pixels](colors-validation.json),
  [workers](workers.json).


## Benchmark results

Three alternating headless/GUI five-second runs per grid, with the existing
benchmark unchanged: seed 2, k0=4, Re=50000, CFL=3, ETDRK4/stress. Headless uses
batch 8 and eight warmup steps/reset; GUI uses native adaptive batches,
two-second warmup/reset and a 640-pixel canvas. Medians:

| Retained → padded | Headless ms/step | Headless steps/s | Headless R_turbo | GUI ms/step | GUI steps/s | GUI R_turbo | GUI overhead |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1024² → 1536² | 1.389214 | 719.831 | 1.196344 | 1.505517 | 664.224 | 1.113358 | 6.94% |
| 2048² → 3072² | 5.434372 | 184.014 | 0.164527 | 6.018185 | 166.163 | 0.148081 | 10.00% |

All 12 runs passed the existing finite-state and positive-step checks.
[Comparison](performance-comparison.json), [headless](performance-headless.json),
[GUI](performance-gui.json), [full benchmark log](benchmark.log).
GUI overhead uses `100*(R_headless-R_gui)/R_headless`. These short desktop
measurements are not an isolated compiler or OS performance comparison.

## Git state

This fix changes `python/src/fastturbo/build_linux.py` and
`python/native/cuda/controller.cmake`, adds `python/tests/test_build_linux.py`,
and updates the Linux documentation/evidence. The previous environment-upgrade
documentation and evidence changes have been preserved. The numerical reference
sources, application GUI, pre-existing tests and tolerances are unchanged.
No commit or push was made. `git diff --check` passed.

[Git status](git-status.txt), [tracked diff statistics](git-diff-stat.txt).
Generated build products and archived directories remain outside tracked source.
The two acceptance GUI instances were stopped after verification to keep tests
and benchmarks free of competing simulation workloads.
