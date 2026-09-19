# Native Linux CUDA/VkFFT reference

This executable validates and benchmarks FastTurbo on a headless NVIDIA server.
It requires no Python, Qt, display server, graphics API, or desktop session at
build or runtime. CUDA is mandatory; initialization errors terminate the run.

For the compact time-based architecture comparison, build the native
[`2dbench`](2dbench.md) target. It defaults to 2048² and 4096² for 20 seconds each,
has no Python dependency, and runs independently of this reference validator's
recorded parity fixtures.

The default benchmark first checks the recorded Windows CUDA trajectory, all six
production integrator/operator combinations, and the historical macOS CNAB2
trajectory. A failed check returns a nonzero exit code before benchmarking.
These checks take several seconds and are outside the timed region.

## Build

Requirements: Linux, CMake **3.26+**, a CUDA-compatible C++20 host compiler,
CUDA Toolkit **13.0+** (including NVRTC and its matching builtins), and a compatible
NVIDIA driver. VkFFT **1.3.4** and the JSON header are already vendored in this
repository. No dependency downloads or Python package installation are performed.
The build retains the production `--fmad=false` / `-ffp-contract=off` settings.

On Ubuntu, install `build-essential`, `cmake`, and optionally `ninja-build` using
your normal package manager, plus the CUDA toolkit appropriate to your driver
and host compiler. Check `cmake --version`, `nvcc --version`, and `nvidia-smi`.
Older Ubuntu packages may need a newer CMake. No OpenGL/X11 development packages
are needed for this executable.

From a fresh checkout on an **H100**:

```sh
git clone https://github.com/mannetroll/2DFastTurboPython.git
cd 2DFastTurboPython
cmake -S . -B build-h100 -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=90
cmake --build build-h100 -j2
./build-h100/reference-linux/fastturbo_reference \
  --n 2048 --warmup 100 --steps 1000 --seed 2 --benchmark
```

For an **RTX 3090**:

```sh
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build-linux -j2
ctest --test-dir build-linux --output-on-failure
./build-linux/reference-linux/fastturbo_reference --validate --json
```

The default architecture is 86. A fat binary can use
`-DCMAKE_CUDA_ARCHITECTURES='86;90'`. With multiple toolkits installed, add
`-DCMAKE_CUDA_COMPILER=/path/to/cuda/bin/nvcc` and
`-DCUDAToolkit_ROOT=/path/to/cuda` to the configure command. Use a fresh build
directory when changing toolchains. CMake embeds the toolkit runtime search path;
matching NVRTC builtins are located by the existing Linux loader.

## CLI and benchmarking

```sh
./build-linux/reference-linux/fastturbo_reference --help
./build-linux/reference-linux/fastturbo_reference --n 1024 --reynolds 50000 \
  --warmup 100 --steps 1000 --seed 2 --benchmark --json --output n1024.json
./build-linux/reference-linux/fastturbo_reference --n 2048 --reynolds 50000 \
  --warmup 100 --steps 1000 --seed 2 --benchmark --json --output n2048.json
```

Supported options:

| Option | Meaning / default |
|---|---|
| `--n` | Retained even grid size, 1024; padded size is `3*N/2` |
| `--steps`, `--warmup` | 1000 timed steps, 100 untimed warmup steps |
| `--seed` | Application PAO seed, full uint64 range; default 2 |
| `--device` | CUDA device index after `CUDA_VISIBLE_DEVICES`, default 0 |
| `--benchmark` | Validate, initialize, warm up, then benchmark; default mode |
| `--validate` | Run validation only |
| `--json`, `--output FILE` | JSON stdout and/or save JSON; identity also goes to stderr with `--json` |
| `--batch` | Synchronous native batch size 1..256, default 8 |
| `--reynolds` | Fixed Reynolds number, default 27898.36614974335; regulation off |
| `--k0`, `--cfl` | Initial wavenumber 4, CFL 3 |
| `--integrator` | `imexRK3`, `ifHeunRK3`, or `etdrk4` (default) |
| `--operator` | `stress` (default) or `advective` |
| `--fixed-dt` | Optional positive float32 timestep |
| `--initial FILE` | Optional N×N little-endian float32 physical vorticity |
| `--spectrum FILE` | Export N×N complex64 Fourier-series vorticity |
| `--fields FILE` | Export `[omega,u,v]`, 3×N×N float32 |
| `--compare-spectrum FILE` | Compare the resulting spectrum using the existing trajectory suite's criteria |
| `--reference-root DIR` | Repository containing existing parity data; defaults to the configured checkout |

The production solver's existing limit is N=16384. No smaller H100 limit is
introduced. Memory requirements and available VRAM determine practical sizes.

Timing is **initialization → warmup → synchronize → timed native batches →
synchronize → report**. Context creation, allocation, plan compilation,
initialization, validation, inspection, exports, and JSON formatting are outside
the measured interval. Warmup advances the same simulation without resetting;
`completed_total_steps = warmup + steps`. Set warmup to zero when comparing a
trajectory from its initial condition. CUDA events already present in the shared
solver measure completed batch intervals; synchronized CPU wall time is the
primary end-to-end measurement. Event intervals include any device idle gaps
between launches, and are not the sum of isolated kernel durations.

Output includes the actual device, compute capability, VRAM, CUDA runtime and
driver API versions, OS, VkFFT version/configuration, initial/final diagnostics,
actual completed step counts, wall and event timings, and individual parity
metrics. Driver version here is CUDA's driver API compatibility version;
`nvidia-smi` supplies the installed driver package version.

Typical text fields are `Parity: "PASS"`, `N: 2048 Padded: 3072`,
`Solver: ... ms/step`, and `Throughput: ... steps/s`. JSON gives the same values
as numbers. Repeat measurements on an otherwise idle GPU; clock state, power,
other processes, and temperature can affect results. Use matching physical
parameters, batch size, and warmup policy for OS/GPU comparisons.

## Historical positional mode

```sh
SCIPYTURBO_SEED=1 ./build-linux/reference-linux/fastturbo_reference \
  512 10000 10 1001 0.25 cuda 100 PAO CNAB2
# Optional evidence exports:
SCIPYTURBO_SEED=1 ./build-linux/reference-linux/fastturbo_reference \
  512 10000 10 1001 0.25 cuda 100 PAO CNAB2 \
  --output legacy.json --dump legacy-state
```

Arguments retain `N Re K0 Steps CFL backend Update spectrum method` semantics.
This mode supports `cuda PAO CNAB2`, power-of-two N, seed 1..5010 (default 1), and
optional `--device`, `--output`, `--dump`. It prints the historical diagnostics,
decimal precision, and checkpoints at 1, every Update, and the final iteration.
Printed iteration `T` precedes the completed step's time increment; final `T`
includes it. DT/CN update only at those checkpoints, exactly as the macOS runner.
The log can be consumed directly by the existing `tools/compare_sim.py`.

The historical PAO initializer is a native translation of the original Fortran
LCG coefficient generator. It has a different seed sequence and vorticity sign
from the production application's PAO initialization. The CNAB2 method likewise
has its own established compact spectrum, three nonlinear products, and host
timestep controller. It is a compatibility method in the **shared native core**;
it is not used to substitute for production ETDRK4 benchmark steps. Its FFTs
reuse the production CUDA/VkFFT class, with the historical unpruned spectrum and
unaligned pitch. No Python subprocess, CPU FFT, or CPU timestep is used.

`--dump` exports initial coefficients and final `omega.c64`, `history.c64`,
`velocity.f32` using the macOS comparison runner's compact layouts:
omega/history `[N,N/2]`, initial velocity `[2,N,N/2]`, physical velocity `[2,M,M]`.
All arrays are little-endian float32/complex64. These layouts and curl sign differ
from production `--spectrum` / `--fields` exports.

## Shared architecture

`python/native/cuda/core.cmake` defines the targets used by this executable and
`python/native/linux`. It compiles the original `reference-windows/src/solver.cu`,
`kernels.cuh`, and `fft.cpp` directly. Those production numerical sources are
unchanged. There is no solver or FFT implementation under `reference-linux/`.

The missing historical method lives once in `python/native/cuda/legacy_*`, next
to the shared build boundary. Both Linux targets compile it into the same core
archive; the Python bridge's public API and its production behavior are unchanged.
The existing Windows and macOS build/source trees are unchanged. The CLI links
only that numerical archive and the existing CUDA loader, with no controller,
Python bridge, renderer, or GUI dependencies.

## Validation

Native validation and CTest require CUDA, but no Python:

```sh
ctest --test-dir build-linux --output-on-failure
./build-linux/reference-linux/fastturbo_reference --validate --json --output parity.json
env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_RUNTIME_DIR -u LD_LIBRARY_PATH \
  ./build-linux/reference-linux/fastturbo_reference --n 32 --steps 7 --warmup 3 --benchmark
CUDA_VISIBLE_DEVICES= ./build-linux/reference-linux/fastturbo_reference --validate
# Last command must fail, with no CPU fallback.
ldd ./build-linux/reference-linux/fastturbo_reference
```

The existing Windows `benchmarks/2026-09-13/headless-1.json` is read in place:
N1024/M1536, 3832 steps, ETDRK4/stress, seed 2, Re=50000, CFL=3. Initial and final
diagnostics use the existing **3 ppm / 1e-9** `pytest.approx` tolerance (the maximum
of the relative and absolute bounds). The macOS `sim_512_1001_metal_macOS.txt` is
also read in place: all checkpoints use the existing **1e-5 relative + 5e-10
absolute + printed rounding** convention. No expected trajectory is hardcoded
in numerical code and no duplicate fixture is created. Missing evidence is a
clear error. Keep the checkout available, or supply `--reference-root`.

Native checks also exercise deterministic initialization/reset and full spectra
for an analytic Fourier diffusion case across all three integrators and both
operators. State evolution, actual step counts, finite states, CLI rejection,
device selection, and absent-device failure are checked. Validation failures
exit 1; configuration/CUDA/I/O errors exit 2.

Optional development tests require the existing Python environment, NumPy and
pytest, but do **not** change the executable's dependencies:

```sh
python/.venv/bin/python -m fastturbo.build --force
FASTTURBO_REFERENCE_EVIDENCE=reference-linux/evidence \
  python/.venv/bin/python -m pytest -q reference-linux/tests/test_reference.py
python/.venv/bin/python -m pytest -q python/tests/test_numerics.py \
  python/tests/test_linux.py python/tests/test_build_linux.py -k 'not rendering'
```

These compare full spectra and physical fields with the Python/Linux native
binding, including N1024/N2048 and the odd padded grid N34/M51. They compare the
native legacy initializer with the original Python coefficient generator across
multiple seeds without running a CPU FFT or CPU timesteps. They also inspect
loaded process libraries and ensure altered parity evidence causes failure.

Max absolute, max relative, RMS, and relative L2 errors are reported where
applicable. Scalar aggregate RMS mixes diagnostic units; per-field differences
are retained. Scalar max-relative excludes zero references, which are tested
with the absolute tolerance. Printed initial DT has coarse precision, so its raw
relative difference can exceed 1e-5 while its rounding-aware check passes.

## Limits and recorded results

See [VERIFICATION.md](VERIFICATION.md) and [evidence](evidence/) for the actual
3090 measurements and commands. Stored Windows/macOS final full-state fixtures
are unavailable: cross-platform checks use their recorded scalar trajectories;
stronger full-state comparisons are against the Linux binding, plus independent
analytic diffusion and historical initializer checks. Equal-source Linux results
do not establish cross-GPU bitwise equality. The same numerical tolerances are
enforced on H100; no H100-specific optimization or weaker tolerance is applied.

`sm_90` compilation can be verified on a 3090 host. H100 execution, numerical
parity, and performance require physical H100 hardware and are **not tested** by
that build alone.
