# Shared CUDA boundary

Windows and Linux compile the same files directly from `reference-windows/src`:
`solver.cu`, `kernels.cuh`, `solver.hpp`, `types.hpp`, `fft.cpp`, `fft.hpp`, and
`cuda_support.cuh`. The reference tree is unchanged. There is no copied Linux
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

Only explicit `export(fields=...)` / `export(spectrum=...)` requests read back
fields for validation or files. `export(snapshot=True)` returns scalar frame
metadata; the leased full snapshot remains in GPU memory.
