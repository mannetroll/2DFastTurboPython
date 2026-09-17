# Task: Build `reference-linux/` — Native Headless CUDA/VkFFT Reference for RTX 3090 and H100

Work directly in the existing FastTurbo repository.

Do not merely propose an architecture. Inspect the repository, understand the existing macOS, Windows, Python, CUDA, VkFFT, controller, and parity implementations, then implement and test the Linux reference executable.

## Objective

Create:

```text
reference-linux/
```

as the canonical **native, headless Linux CUDA/VkFFT validation and benchmarking executable**.

Its primary targets are:

1. Native Ubuntu/Linux + RTX 3090 (`sm_86`)
2. Headless Linux server + NVIDIA H100 (`sm_90`)

The executable must answer two questions independently of Python, Qt, X11, Wayland, RDP, or any desktop environment:

1. Does the CUDA/VkFFT FastTurbo solver produce numerically correct results?
2. How fast does the native CUDA/VkFFT solver run on this GPU?

This is deliberately **not another application implementation**.

It is a thin native executable around the existing/shared CUDA/VkFFT numerical backend.

---

# 1. Fundamental Architecture Rule

**DO NOT DUPLICATE THE CUDA/VkFFT SOLVER.**

Do not create another copied implementation of:

- CUDA kernels
- VkFFT integration
- timestep/controller logic
- spectral operations
- dealiasing
- initialization
- numerical constants
- state evolution

Instead, inspect the repository and refactor only as much as necessary so that the same CUDA/VkFFT numerical core can be used by both:

```text
python/native/linux
```

and:

```text
reference-linux
```

The intended architecture is approximately:

```text
                    shared CUDA/VkFFT core
                            |
                 +----------+----------+
                 |                     |
                 v                     v
        python/native/linux      reference-linux
                 |                     |
          Python/native bridge      tiny main()
                 |                     |
              PySide6             headless CLI
```

The exact directory layout should follow the existing repository structure rather than forcing this diagram literally.

Minimize refactoring.

Preserve existing behavior.

---

# 2. Headless Means Headless

`reference-linux` must have **zero runtime dependency** on:

- Python
- CPython
- pybind11
- PySide6
- Qt
- X11
- XWayland
- Wayland
- OpenGL
- Vulkan presentation
- GNOME
- desktop sessions
- RDP

It must run successfully from a plain SSH shell on a Linux GPU server.

For example:

```bash
ssh h100-server
git clone ...
cd ...
cmake ...
cmake --build ...
./reference-linux/fastturbo_reference --benchmark
```

A machine with no graphical environment installed must be a valid target.

Do not initialize any graphics subsystem.

---

# 3. CUDA Execution Is Mandatory

This task requires real CUDA execution.

Do not substitute:

- CPU FFT
- FFTW
- NumPy
- SciPy
- CPU reference execution

for CUDA/VkFFT execution.

The benchmark is specifically intended to measure the native CUDA/VkFFT backend.

At startup, detect and report the CUDA device.

Report at least:

```text
GPU
CUDA device index
compute capability
total VRAM
CUDA runtime version
CUDA driver version
VkFFT version/configuration if available
```

Example:

```text
GPU: NVIDIA H100 PCIe
Compute capability: 9.0
VRAM: 79.1 GiB
CUDA runtime: 13.x
CUDA driver: ...
Backend: CUDA + VkFFT
```

If CUDA initialization fails, fail clearly.

Do not silently fall back to CPU.

---

# 4. GPU Architecture Support

The build must support at least:

```text
sm_86    RTX 3090
sm_90    H100
```

Prefer a clean CMake configuration allowing architectures to be selected explicitly.

For example:

```bash
cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CUDA_ARCHITECTURES=90
```

for H100.

And:

```bash
cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CUDA_ARCHITECTURES=86
```

for RTX 3090.

Do not assume the H100 is present on the development machine.

Compilation support for `sm_90` and actual H100 execution are different claims.

Clearly distinguish them in the final report.

---

# 5. CLI

Provide a simple CLI such as:

```bash
./fastturbo_reference \
    --n 2048 \
    --steps 1000 \
    --warmup 100 \
    --seed 2 \
    --benchmark
```

Support at least:

```text
--n
--steps
--warmup
--seed
--benchmark
--device
--help
```

If the existing solver has other parameters necessary for deterministic reproduction, expose them where appropriate.

Defaults should correspond to an existing known/reference configuration where possible.

---

# 6. Determinism and Numerical Parity

Performance is secondary until numerical parity has been demonstrated.

Use the existing project parity methodology.

Do not invent a weaker correctness criterion merely to make Linux pass.

The same:

- initial condition
- seed
- dimensions
- timestep/controller
- constants
- padding
- spectral operations
- precision
- number of steps

must produce results consistent with the existing validated CUDA implementation.

Where practical, compare Linux native results against the existing Windows CUDA/VkFFT reference data.

Also compare against existing Python/native Linux parity data if available.

Report metrics such as:

```text
max absolute error
max relative error
RMS error
```

using the project's existing conventions.

Do not require bitwise equality unless the existing project explicitly establishes that requirement.

Document expected floating-point differences between GPUs where relevant.

H100 and RTX 3090 parity should be judged using justified numerical tolerances, not byte-for-byte identity.

---

# 7. Benchmark Methodology

Benchmark the solver independently of startup and initialization.

The timed region must exclude:

- CUDA context creation
- initial memory allocation
- VkFFT plan creation
- initial condition generation
- file I/O
- CLI parsing

Use:

```text
initialization
warmup
synchronize
timed steps
synchronize
report
```

Conceptually:

```cpp
solver.initialize();

for (int i = 0; i < warmup; ++i)
    solver.step();

cudaDeviceSynchronize();

start_timer();

for (int i = 0; i < steps; ++i)
    solver.step();

cudaDeviceSynchronize();

stop_timer();
```

Use CUDA events for GPU timing where appropriate.

A CPU wall-clock measurement of the complete synchronized timestep loop may also be reported.

Do not accidentally benchmark asynchronous kernel launch latency instead of completed GPU work.

---

# 8. Benchmark Output

Produce concise machine-readable-enough output.

For example:

```text
FastTurbo native Linux reference

GPU:             NVIDIA H100 PCIe
Compute:         sm_90
Backend:         CUDA + VkFFT
Precision:       float32

N:               2048
Padded:          3072
Seed:            2
Warmup:          100
Steps:           1000

Initialization:  428.2 ms

Solver:          1.84 ms/step
Throughput:      543.5 steps/s

Parity:
max_abs:         ...
max_rel:         ...
rms:             ...
status:          PASS
```

If the backend already exposes meaningful timing components without perturbing performance excessively, also report:

```text
VkFFT
CUDA kernels
other
```

However:

**Do not redesign or instrument the solver heavily merely to obtain sub-component timings.**

The primary benchmark is end-to-end native solver timestep performance.

---

# 9. Benchmark Sizes

At minimum validate/benchmark:

```text
N=1024
N=2048
```

if memory and runtime permit.

If the existing solver uses 3/2 padding, report both retained and padded dimensions, for example:

```text
1024² -> 1536²
2048² -> 3072²
```

If practical, make larger sizes available for H100 experiments without hardcoding artificial limits.

The H100 has much more memory and bandwidth than the RTX 3090, so the executable should not unnecessarily restrict problem size.

---

# 10. H100-Specific Considerations

Do not assume that code optimized for Ampere is automatically optimal for Hopper.

First establish correctness using the existing implementation unchanged.

Then benchmark it unchanged.

Only after obtaining a clean baseline may you identify potential Hopper-specific opportunities such as:

- memory bandwidth utilization
- occupancy
- kernel launch overhead
- FFT scaling
- VkFFT configuration
- register pressure
- memory traffic
- kernel fusion
- CUDA Graphs

Do not introduce H100-specific optimizations during the initial reference implementation unless required for correctness.

We need a trustworthy baseline before optimization.

---

# 11. No Benchmark Cheating

The native reference must execute the real production numerical path.

Do not:

- remove calculations whose results are required by the solver
- reduce precision
- skip synchronization necessary for correct timing
- cache timestep results
- benchmark an isolated synthetic FFT instead of the solver
- change numerical algorithms merely to improve benchmark numbers

The purpose is comparison:

```text
RTX 3090 Windows native CUDA
RTX 3090 Linux native CUDA
H100 Linux native CUDA
```

using as close to the same numerical implementation as possible.

---

# 12. Preserve Existing Implementations

Existing:

```text
reference-macos/
reference-windows/
python/
```

must continue to work.

Run existing relevant tests after refactoring shared code.

Avoid broad cleanup unrelated to this task.

Do not rewrite working code simply because another organization would look cleaner.

---

# 13. Tests

Add focused tests/checks for the Linux native reference.

At minimum verify:

1. CLI parsing
2. CUDA device detection
3. solver initialization
4. deterministic initialization
5. finite numerical state after stepping
6. known parity case
7. benchmark completes
8. nonzero number of actual CUDA solver steps executed
9. clean failure when no CUDA device is available

Reuse existing parity fixtures/reference data where possible.

Do not create parallel copies of reference data without need.

---

# 14. Build Documentation

Add:

```text
reference-linux/README.md
```

Document:

- purpose
- architecture
- dependencies
- Ubuntu build requirements
- CUDA requirements
- VkFFT requirements
- RTX 3090 build
- H100 build
- CLI
- parity test
- benchmark procedure
- expected output
- limitations

Include copy/paste commands.

Example H100 flow:

```bash
git clone <repository>
cd <repository>

cmake -S . -B build-h100 \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CUDA_ARCHITECTURES=90

cmake --build build-h100 -j

./build-h100/reference-linux/fastturbo_reference \
    --n 2048 \
    --warmup 100 \
    --steps 1000 \
    --seed 2 \
    --benchmark
```

Adapt these commands to the actual repository layout.

---

# 15. Optional Structured Output

If straightforward, add:

```text
--json
```

so benchmark results can be archived and compared automatically.

For example:

```json
{
  "gpu": "NVIDIA H100 PCIe",
  "compute_capability": "9.0",
  "n": 2048,
  "padded_n": 3072,
  "warmup": 100,
  "steps": 1000,
  "ms_per_step": 1.84,
  "steps_per_second": 543.5,
  "max_relative_error": 1.2e-6,
  "parity": "PASS"
}
```

Do not add a large framework just for this.

---

# 16. Expected Repository Role

The final conceptual structure should be:

```text
reference-macos/
    native Metal/VkFFT reference

reference-windows/
    native CUDA/VkFFT reference

reference-linux/
    native CUDA/VkFFT
    headless
    canonical GPU-server validation/benchmark

python/
    common Python/PySide6 application
    native/
        macos
        windows
        linux

shared/native CUDA code
    used rather than duplicated where architecture permits
```

`reference-linux` should remain intentionally small.

Think of it as:

```text
CUDA/VkFFT numerical core
        +
thin CLI
        +
parity validation
        +
benchmark harness
```

not a fourth application.

---

# 17. Execution Policy

Work directly on the files.

Inspect first.

Implement incrementally.

Build frequently.

Run tests frequently.

Do not stop after writing code if the available machine permits compilation or execution.

If the environment/sandbox prevents access to:

```text
/dev/nvidia*
CUDA
the RTX 3090
GPU profiling
```

but execution outside the sandbox is available, request/obtain the required execution permission rather than treating CUDA as unavailable.

Never replace CUDA/VkFFT with CPU code because GPU access is restricted.

If H100 hardware is not physically available, do not claim H100 runtime validation.

Instead report separately:

```text
sm_90 compilation: PASS/FAIL
H100 execution: NOT TESTED
```

---

# 18. Final Verification

Before finishing:

1. Build Release for the available Linux CUDA target.
2. Run existing relevant tests.
3. Run native Linux parity validation.
4. Run the native benchmark.
5. Verify no Python/Qt/display libraries are linked into the executable.
6. Inspect dynamic dependencies, e.g. with `ldd`.
7. Verify CUDA/VkFFT is actually being used.
8. Verify existing Python Linux CUDA functionality was not broken.
9. Verify `sm_90` compilation if the installed CUDA toolchain supports it.
10. Record exact commands and results.

If RTX 3090 hardware is available, obtain a real baseline that can later be compared directly with H100.

---

# 19. Final Report

Finish with a concise report containing:

## Implementation

Files created/modified and the resulting architecture.

## Numerical validation

```text
case
reference
max_abs
max_rel
RMS
PASS/FAIL
```

## RTX 3090 benchmark

If actually executed:

```text
N        padded       ms/step       steps/s
1024     1536         ...           ...
2048     3072         ...           ...
```

## H100 readiness

Explicitly distinguish:

```text
sm_90 build support
H100 runtime execution
H100 numerical parity
H100 benchmark
```

Never mark something as verified unless it was actually executed.

## Dynamic dependencies

Show enough `ldd` information to demonstrate that the executable is genuinely headless and independent of Python/Qt/X11/Wayland.

## Remaining issues

Only actual remaining issues.

---

# Success Criterion

The end state should allow me to take the repository to a completely headless H100 Linux server and make the first meaningful experiment essentially:

```bash
git clone ...
cmake ...
cmake --build ...
./fastturbo_reference --n 2048 --warmup 100 --steps 1000 --seed 2 --benchmark
```

and immediately determine:

```text
CUDA works
VkFFT works
numerical parity passes
H100 performance = X ms/step
```

without first solving Python packaging, PySide6, Qt, X11, Wayland, RDP, or any other desktop integration problem.
