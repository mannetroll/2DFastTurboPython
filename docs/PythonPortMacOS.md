# Python port: macOS

You are GPT-6 Astra running at xhigh reasoning effort inside Codex CLI on macOS.

Work directly on the files in the current directory.

Do not ask clarifying questions.
Do not stop after producing a plan.
Do not merely describe the port.
Inspect the existing Swift + Metal + VkFFT implementation completely, then implement, build, run, test, benchmark, profile, fix failures, and iterate until the Python port works.

## PRIMARY TASK

Port this existing macOS application:

    Swift + Metal + VkFFT

to:

    Python + Metal + VkFFT

on macOS.

The existing Swift application is the AUTHORITATIVE reference implementation.

Do NOT redesign the solver.
Do NOT reinterpret controls.
Do NOT change numerical meaning unless a platform constraint makes it necessary.

The Python version should preserve:

- numerical method
- physical model
- spectral conventions
- retained N semantics
- classical 3/2 zero padding
- integrator behavior
- initial conditions
- presets
- control meanings
- default values
- GUI behavior
- color maps
- visualization normalization
- interaction semantics
- diagnostics
- performance metrics

The goal is not a Python rewrite of the numerical hot path.

The goal is:

    Python orchestration / GUI
        +
    native Metal compute
        +
    native VkFFT Metal backend

The CFD timestep must remain GPU-resident and native.

Python must NOT execute the main numerical kernels.

First inspect and understand the complete macOS implementation.

```text
2DFastTurboPython/
├── reference-macos/
│   └── 2DFastTurbo/
│       ├── Sources...
│       ├── Tests...
│       └── ThirdParty...
│
└── python/
```


## TARGET MACHINE

macOS on Apple Silicon.

Primary target:

    Apple M1 Max MacBook Pro

Use the existing macOS Swift project as the reference for deployment target, GPU assumptions, numerical defaults, and GUI behavior.

## REFERENCE-FIRST WORKFLOW

Before writing the Python version:

1. Inspect the entire Swift project.
2. Identify every GUI control.
3. Record each control's:
   - name
   - type
   - allowed range
   - default
   - units
   - solver variable
   - side effects
4. Document the numerical execution pipeline.
5. Identify:
   - Metal buffers
   - VkFFT plans
   - command queues
   - command buffers
   - compute pipelines
   - render pipeline
   - synchronization points
   - diagnostics
6. Identify the actual retained spectral state.
7. Verify exactly how 3/2 padding/truncation is implemented.
8. Verify FFT normalization conventions.
9. Verify the integrator implementation.
10. Verify the visualization pipeline.

Create:

    PORT_REFERENCE.md

before major implementation.

This document should describe the Swift reference sufficiently well that parity can be checked systematically.

## NUMERICAL MODEL

Preserve the existing Swift solver exactly in mathematical meaning.

Expected model:

    2D incompressible Navier-Stokes
    vorticity-streamfunction formulation
    Fourier pseudo-spectral discretization
    classical 3/2 zero padding
    periodic domain

Preserve the actual equations and sign conventions from the Swift source rather than assuming them from this prompt.

The retained resolution N must keep exactly the same meaning.

For example:

    N = 1024 retained
    nonlinear padded workspace = 1536 x 1536

The Python GUI must report retained and padded sizes exactly as the Swift application does.

Do not reinterpret 1536 as the simulation resolution.

## METAL / VKFFT

Retain Metal and VkFFT as the numerical execution backend.

Do NOT replace them with:

    NumPy FFT
    SciPy FFT
    MLX
    PyTorch
    JAX
    CuPy
    TensorFlow

Do not move the solver to CPU.

Reuse as much of the existing native Metal/VkFFT implementation as practical.

Preferred approach:

    existing Metal shaders
    existing VkFFT bridge / native code
    existing buffer layout
    existing command-buffer strategy

with a thin Python binding layer.

The native core should remain responsible for:

    GPU allocation
    VkFFT plan creation
    FFT execution
    Metal compute kernels
    time integration
    padding/truncation
    reductions
    visualization resources
    synchronization

Python should control the native engine, not reimplement it.

## PYTHON NATIVE BRIDGE

Choose the thinnest robust binding mechanism.

Good candidates include:

    pybind11
    CPython extension
    CFFI
    ctypes over a clean C ABI

Prefer a small C/C++/Objective-C++ bridge exposing opaque handles and simple functions.

Do NOT expose raw C++ implementation details into Python.

A conceptual API might look like:

    engine = create_engine(config)

    engine.start()
    engine.pause()
    engine.reset()

    engine.set_viscosity(...)
    engine.set_cfl(...)
    engine.set_seed(...)
    engine.set_resolution(...)

    diagnostics = engine.get_diagnostics()

but design the actual interface based on the existing Swift architecture.

Avoid Python callbacks inside the hot simulation path.

Avoid per-kernel Python calls.

One Python call should trigger substantial native GPU work.

## GPU RESIDENCY

The normal simulation timestep must remain GPU-resident.

Do NOT:

- copy full fields to Python each timestep
- convert solver state to NumPy
- read back vorticity for rendering
- synchronize GPU solely for Python convenience

Only tiny diagnostics should normally return to Python.

The visualization path should use GPU-native resources.

If the Python GUI cannot directly consume the native Metal texture without copies, create the thinnest native rendering bridge necessary rather than reading the field into Python.

## GUI

The Python application's GUI should reproduce the Swift application as closely as practical.

The Swift GUI is the behavioral reference.

Do NOT invent new controls.

Do NOT remove controls.

Preserve:

- layout
- control grouping
- defaults
- ranges
- labels
- Play / Pause / Reset
- grid control
- seed
- viscosity
- drag
- CFL
- dt controls
- steps/batch
- field selection
- colormap selection
- exposure
- contrast
- contour options
- interaction instructions
- diagnostic dashboard
- retained/padded resolution display

Preferred Python GUI toolkit:

    PySide6

unless another Python-accessible macOS GUI toolkit gives a clearly better native Metal integration.

The final application should still look like a polished macOS scientific visualization application.

Do not let it look like a generic Python utility.

## NATIVE VISUALIZATION

Preserve the Swift app's visualization as closely as practical.

Prefer to keep the actual Metal render path native.

Python/PySide6 may host the view and surrounding controls, but the high-rate field rendering should remain native GPU work.

Preserve:

- Vorticity
- Enstrophy
- Speed
- Streamfunction
- color maps
- signed-field behavior
- normalization
- exposure
- contrast
- overlays
- simulation-time label

Do not fake turbulence structures.

Do not apply image processing that changes physical topology.

## INTERACTION

Preserve direct flow interaction.

If the Swift app supports:

    left drag  -> positive vortex
    right drag -> negative vortex
    modifiers  -> stronger vortex

reproduce the same behavior.

The actual field modification should remain native/GPU-side.

Do not manipulate a Python-side field array.

## PERFORMANCE OBJECTIVE

After functional parity is established, optimize:

    R_turbo = T_simulated / T_wall

The Python port should lose as little performance as possible relative to the Swift version.

Measure separately:

    R_turbo_swift_reference
    R_turbo_python_port

Also measure:

    native GPU ms/step
    Python/UI overhead
    FPS
    diagnostics overhead

Calculate:

    Python overhead % =
        100 * (R_swift - R_python) / R_swift

where comparable.

The aim is for the Python layer to contribute only small control/UI overhead.

The CFD solver itself should run at essentially the same native Metal/VkFFT speed.

Do not optimize Python microcode before checking native GPU scheduling and synchronization.

## PARITY TESTS

Create tests that compare the Python port against the Swift reference.

Use deterministic configuration:

    same N
    same seed
    same initial spectrum
    same viscosity
    same drag
    same forcing
    same CFL
    same integrator
    same timestep mode

Compare where possible:

    initial spectral coefficients
    early-time kinetic energy
    enstrophy
    max |omega|
    max velocity
    field checksum/norm
    selected Fourier coefficients
    simulation time after fixed step count

Do not require bitwise identity if platform/API details make that unrealistic.

Require numerically tight agreement consistent with FP32 GPU computation.

Specifically test:

1. FFT normalization
2. k=0 handling
3. spectral derivatives
4. Poisson inversion
5. 3/2 padding
6. 3/2 truncation
7. Nyquist handling
8. integrator stages
9. deterministic initialization
10. long-run finite state
11. energy/enstrophy evolution
12. GUI controls update the same engine parameters
13. rendering does not alter solver state

## BUILD SYSTEM

Create a clean Python project using:

    Python 3.13 or later
    uv
    pyproject.toml

Launch should be simple, ideally:

    uv run 2dfastturbo

or:

    uv run python -m fastturbo

Choose a clean package name.

Native extension build integration must be automated.

Do not require the user to manually run several undocumented compiler commands.

The project should configure and build the native Metal/VkFFT bridge reproducibly.

Reuse existing Xcode/clang tooling where appropriate.

Keep Metal shader compilation automated.

## PROJECT STRUCTURE

A possible structure:

    FastTurboPython/
        pyproject.toml
        README.md

        fastturbo/
            __init__.py
            main.py
            app.py
            controls.py
            diagnostics.py

        native/
            include/
            src/
            metal/
            vkfft/
            bindings/

        tests/
            ...

        reference-swift/
            <existing Swift project>

        PORT_REFERENCE.md
        ARCHITECTURE.md
        PERFORMANCE.md

Use another structure if it better fits the existing project.

Do not duplicate large native assets unnecessarily.

## PACKAGING

Once the Python version is working, make it easy to run.

Primary development mode:

    uv run ...

Also investigate packaging into a macOS .app bundle if practical.

A packaged application should not require the user to manually launch a terminal.

However, do not compromise solver performance or delay the core port merely for packaging.

## WORKFLOW

Use this sequence:

1. inspect Swift reference
2. document reference
3. design minimal Python/native boundary
4. expose native engine
5. reproduce GUI
6. reproduce controls exactly
7. reproduce visualization
8. run numerical parity tests
9. fix discrepancies
10. establish Swift performance baseline
11. establish Python performance baseline
12. profile Python/native overhead
13. optimize only measured bottlenecks
14. package

Do not optimize before parity.

Do not rewrite working native Metal/VkFFT code just because Python is now the host language.

## KEY DESIGN PRINCIPLE

The desired architecture is:

    Python / PySide6
        |
        | coarse control calls
        v
    thin native bridge
        |
        v
    existing native Metal/VkFFT engine
        |
        +-- Metal buffers
        +-- VkFFT
        +-- compute kernels
        +-- time integration
        +-- diagnostics reductions
        +-- render resources
        |
        v
    native Metal visualization

NOT:

    Python
      -> NumPy arrays
      -> Python FFT
      -> Python timestep
      -> CPU/GPU copies

The Python layer should behave like a control plane.

The native Metal/VkFFT engine should remain the data plane.

## FINAL SELF-REVIEW

Before completion verify:

1. Is the solver still actually Metal/VkFFT?
2. Is the hot timestep free of Python execution?
3. Are full fields kept out of Python?
4. Are FFT plans reused?
5. Are buffers reused?
6. Is 3/2 padding identical in meaning to the Swift version?
7. Do all GUI controls map to the same parameters?
8. Does the Python GUI reproduce Swift defaults/ranges?
9. Does rendering remain GPU-native?
10. Does Python introduce synchronization stalls?
11. What percentage performance was lost versus Swift?
12. Can that overhead be reduced?
13. Are parity tests passing?
14. Is the app stable under long runs?
15. Does changing N safely rebuild GPU resources?

## FINAL RESPONSE

At completion report:

1. Python project architecture
2. native bridge architecture
3. how Swift/Metal code was reused
4. VkFFT integration
5. GUI parity status
6. numerical parity results
7. retained/padded resolution behavior
8. tests and results
9. Swift baseline R_turbo
10. Python-port R_turbo
11. Python overhead percentage
12. dominant remaining overhead
13. exact uv commands to build/run
14. macOS .app packaging status if implemented
15. any remaining differences from the Swift reference

The objective is a Python-hosted macOS application with the convenience of Python while retaining essentially native Swift/Metal/VkFFT-class CFD performance.
