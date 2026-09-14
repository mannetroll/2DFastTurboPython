# Windows native adapter

`bridge.cpp` implements version 1 of `../fastturbo.h` using the existing ctypes
binding. It accepts the same JSON configuration, engine operations, UI commands,
diagnostics and frame metadata as `../PortBridge.swift`.

The CMake build adds `reference-windows` as a subdirectory. The reference EXE and
the Python DLL link **the same `turbo_core` library**: `solver.cu`, `kernels.cuh`,
`fft.cpp`, persistent CUDA allocations, and VkFFT plans compile unchanged. No
reference file is edited. Shader bytecodes are built from the original HLSL and
embedded into both binaries. The reference math flags (`--fmad=false`,
`/fp:strict`) and Release optimization are retained.

Only these generated source adaptations are made in `build/windows/adapted`:

* `resources.hpp` locates resources in the DLL containing the resource function,
  rather than in the Python EXE.
* `controller.hpp/.cpp` adds a locked benchmark-options setter and copies those
  options on the worker at each generation change. This permits the Python GUI's
  warmup/reset/benchmark workflow while preserving the original scheduling,
  regulation, snapshots, plan reuse and latest pause intent.
* `renderer.cpp` is copied unchanged alongside that resource header so its quoted
  include resolves correctly. `renderer.hpp` and all render equations are reused.

Each textual adaptation fails the build if the expected reference text changes.
Small clock/file-export helpers in `bridge.cpp` originate in the reference
`main.cpp`; they avoid linking a second application entry point into the DLL.

The GUI controller runs its existing C++ worker. A separate native rendering
thread owns D3D11/D2D/COM and the CUDA interop stream. A 60 Hz native deadline and
the reference's scoped Windows timer precision prevent duplicate-frame spinning
when a child-HWND DXGI swap chain signals early. The Qt main thread owns an
embedded child HWND, delivers control commands and polls scalar telemetry at
4 Hz. Resize requests use asynchronous HWND positioning. Immutable CUDA frame
leases protect rendering while the solver continues; the reference renderer
copies device-to-device into a CUDA-registered D3D resource. Explicit file/PNG
exports use native readback on request, with render exports dispatched to the
rendering thread. Normal GUI operations never return physical arrays to Python.

`vendor/json.hpp` is nlohmann/json **v3.12.0**, downloaded from the project's
`single_include/nlohmann/json.hpp` release source. Its MIT license is included.
It parses only coarse control and telemetry messages; it is outside timesteps.

The actual sources have no forcing, drag, or vortex-injection operation. They
remain unavailable rather than adding new physics during this platform port.
