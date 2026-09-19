# Shared CUDA boundary

Windows and Linux compile the same files directly from `reference-windows/src`:
`solver.cu`, `kernels.cuh`, `solver.hpp`, `types.hpp`, `fft.cpp`, `fft.hpp`, and
`cuda_support.cuh`, and the shared KM3 initializer `km3.hpp`. There is no copied Linux
solver, and neither window-system headers nor renderer libraries enter this core.

`bridge_common.hpp` contains the original Windows bridge's JSON configuration,
diagnostics, opaque engine owner and explicit export helpers. Both C ABI bridges
include it. `controller.cmake` shares the guarded controller adaptations previously used by
Windows; generated Linux controller output matches those adaptations byte-for-byte.
The D3D11 host remains under `native/windows`; Linux has the matching native
controller host and a lazily loaded OpenGL module under `native/linux`. The Linux
headless library has no desktop dependencies. Its loader fixes NVRTC builtins
lookup for local toolkits and retains the conditional WSL JIT-loader adaptation. Both use `--fmad=false`; Linux host C++ uses
`-ffp-contract=off`, corresponding to the reference's strict MSVC math.

The authoritative solver has persistent VkFFT plans and GPU buffers, native
batches and reduction readbacks. It currently has no CUDA Graph implementation.
There is therefore no Graph/non-Graph mode to preserve or compare.

`core.cmake` now defines the shared Linux numerical build for both the Python
backend and the standalone `reference-linux` validation/benchmark executable.
The latter links no bridge, controller or renderer. The additional `legacy_*`
files implement the historical PAO/CNAB2 compatibility method, with the
historical LCG, compact spectrum and update cadence. The Linux build defines
`FASTTURBO_ENABLE_CNAB2` to expose that same implementation through the common
`Solver` API and GUI. Continuous batches preserve history and the fixed
checkpoint cadence. A device-to-device conversion supplies the existing
statistics/snapshot path with its positive-curl, unnormalized half spectrum;
integration continues in the historical compact buffers. The N-grid snapshot
takes the real part of each self-conjugate cutoff component after reconstructing
velocity. No fields are transferred to Python during stepping or rendering.
They reuse the existing `FFT` class. Production and historical solvers both
support PAO (the unchanged default) and KM3 via `km3.hpp`. The Python API exposes
`Configuration(start_spectrum="KM3")`; the shared JSON bridge carries the same
choice to the GUI controller and reports it back. Each solver retains its PAO
seed sequence, curl convention, and spectrum layout. See
[the native reference documentation](../../../reference-linux/README.md).

Only explicit `export(fields=...)` / `export(spectrum=...)` requests read back
fields for validation or files. `export(snapshot=True)` returns scalar frame
metadata; the leased full snapshot remains in GPU memory.
