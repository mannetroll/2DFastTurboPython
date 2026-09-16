# Shared CUDA boundary

Windows and Linux compile the same files directly from `reference-windows/src`:
`solver.cu`, `kernels.cuh`, `solver.hpp`, `types.hpp`, `fft.cpp`, `fft.hpp`, and
`cuda_support.cuh`. The reference tree is unchanged. There is no copied Linux
solver, and neither window-system headers nor renderer libraries enter this core.

`bridge_common.hpp` contains the original Windows bridge's JSON configuration,
diagnostics, opaque engine owner and explicit export helpers. Both C ABI bridges
include it. The Windows host/controller/D3D11 renderer remain under
`native/windows`; the Linux shared library and process-local WSL loader fix are
under `native/linux`. Both use `--fmad=false`; Linux host C++ uses
`-ffp-contract=off`, corresponding to the reference's strict MSVC math.

The authoritative solver has persistent VkFFT plans and GPU buffers, native
batches and reduction readbacks. It currently has no CUDA Graph implementation.
There is therefore no Graph/non-Graph mode to preserve or compare.

Only explicit `export(fields=...)` / `export(spectrum=...)` requests read back
fields for validation or files. `export(snapshot=True)` returns scalar frame
metadata; the leased full snapshot remains in GPU memory.
