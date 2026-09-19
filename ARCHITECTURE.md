# FastTurbo architecture

## Native Linux desktop

`native-linux` adds a standalone C++ Dear ImGui frontend over the same Linux
`turbo_core` target used by `reference-linux` and native `2dbench`. It has no
Python, Qt, PySide6 or GTK runtime. `reference-linux` remains the canonical
headless validation executable; the Python/PySide6 application is preserved.

The GUI compiles the existing shared, guarded native controller adaptation,
with target-local additions for snapshot cadence and readiness with rendering
disabled. There is no new solver or numerical controller implementation. Its
independent worker keeps the existing batching, regulation, persistent VkFFT
plans, CUDA streams and GPU frame ownership. The Python/Windows controller
generation is unchanged.

The existing Linux renderer supplies X11/GLX window/context ownership and its
validated CUDA/OpenGL map/copy/unmap path. A small optional desktop interface
renders the existing field/flow shaders into an OpenGL texture for ImGui.
`native-linux/x11_imgui.cpp` handles X11 input and window-manager close events.
On Wayland this uses XWayland. Snapshot production and field redraw cadence
are configurable separately from the ImGui event loop. Input wakes presentation
immediately; idle drawing follows field updates or 4 Hz telemetry, and active
editing refreshes at up to 60 Hz. Neither drives solver steps. No normal-frame
CPU readback is introduced. See
[native Linux architecture and validation](native-linux/README.md).

## Python/native application

The application selects a native backend locally in `fastturbo.build`:
macOS loads the existing `libfastturbo.dylib` (Metal/VkFFT), Windows loads
`fastturbo.dll` (CUDA/VkFFT), and Linux loads `libfastturbo.so` (CUDA/VkFFT).
All implement version 1 of `python/native/fastturbo.h`. Linux supports the headless
engine and a GPU-resident OpenGL/GLX renderer on verified native Ubuntu.
The common Python `Configuration`, `Engine` / `create_engine`, `NativeUI`, CLI,
controls, palettes and PySide6 dashboard are shared. ctypes releases the GIL for
all native calls. `GUI_PARITY.md` records the control mapping.

Windows and Linux compile the same unchanged CUDA solver/kernel/VkFFT files in
`reference-windows/src`; `python/native/cuda` shares bridge conversions and the
guarded native-controller adaptations. The generated Linux controller matches
the prior Windows controller adaptation byte-for-byte. Both hosts keep native
simulation workers, persistent plans, buffers and frame leases.

The Linux numerical library links no Qt, X11, Wayland, GL, EGL, Win32 or D3D
libraries. Its optional `libfastturbo_gl.so` loads only for UI or PNG requests.
The standalone `reference-linux` CMake executable uses the same
`python/native/cuda/core.cmake` numerical target without linking the bridge or
controller. Its production benchmarks execute the unchanged Windows solver.
The native `2dbench` target links that same core and CUDA loader. Its C++ host
runs each grid size in an isolated child process, performs the Python benchmark's
warmup/reset and synchronized timing sequence, and formats text or JSON reports.
Neither Linux executable needs Python; `2dbench` also needs no source checkout
or recorded reference fixtures at runtime. See [native benchmark](reference-linux/2dbench.md).
The historical PAO/CNAB2 comparison method lives in `python/native/cuda/legacy_*`
and reuses the same VkFFT wrapper; it preserves the distinct historical seed,
curl sign, compact layout, and checkpoint controller. See
[`reference-linux/README.md`](reference-linux/README.md).
A native rendering thread embeds a GLX child under the shared Qt xcb canvas,
using Xwayland on a Wayland desktop. CUDA copies completed immutable snapshots
device-to-device into a persistent OpenGL texture buffer. Mapping/unmapping and
a copy-stream wait protect ownership; solver work runs on a separate stream.
Normal rendering has no CPU field/pixel readback. Explicit PNG exports use a GL
framebuffer and requested pixel readback. Presentation is paced at 60 Hz;
Python's 4 Hz telemetry timer schedules neither timesteps nor rendering.

Native Ubuntu headless, GUI, numerical/pixel parity, measured GUI overhead and
sm_90 compilation pass. Historical WSLg CUDA graphics sharing failures remain
unresolved; native Ubuntu results do not establish WSLg compatibility. The
NVRTC loader preloads matching builtins beside the loaded library; the older
WSL driver-loader fix remains conditional. See `LINUX_PORT.md` for environment,
commands, synchronization measurements and hardware limitations.

The Windows CMake project links the reference solver directly, including its
unchanged CUDA kernels and VkFFT implementation. The original C++ controller owns
the simulation worker; a separate native thread owns the embedded HWND's Direct3D
renderer. CUDA snapshots remain on the device and are copied device-to-device
into registered D3D resources. Python polls only JSON telemetry at 4 Hz. Field and
PNG readbacks occur only for explicit exports. No Python callbacks schedule GPU
steps or frames. See `python/native/windows/README.md` for the small, guarded
controller/resource adaptations and `WINDOWS_PORT.md` for build and validation.

The remainder of this document describes the preserved macOS backend.

The Python package lives in `python/`; the reference tree is read-only input.
`PORT_REFERENCE.md` is the source-derived numerical and control contract.

```text
Python 3.13 / PySide6 controls and dashboard (4 Hz telemetry)
                 │ opaque C ABI, small JSON commands
                 ▼
libfastturbo.dylib — Swift native controller and completion-driven pump
                 │ native batches, no Python callback
                 ▼
unchanged FastTurboCore + existing Objective-C++/C++ VkFFT bridge
                 │
     persistent Metal buffers / VkFFT plans / compute pipelines
                 │ immutable Metal frame leases, no Python field copies
                 ▼
native SwiftUI canvas / MTKView / HDR Metal renderer
                 │
           PySide6 parent NSView
```

`native/PortBridge.swift` exports the functions declared in `native/fastturbo.h`.
ctypes uses opaque retained object handles and owned UTF-8 result strings; every
result is freed in a `finally` block. Python never sees Swift/C++ layouts or Metal
pointers except the host NSView handle. `Engine` provides synchronous native batch
work for scripts; `NativeUI` controls the original `SimulationController` and
`SimulationPump`. All UI calls are checked for main-thread affinity. Headless
calls are protected against close races by a Python lock and serialized on the
native core's execution queue. ctypes releases the GIL during native execution.

The GUI has **no Python timestep loop**. Native completions schedule subsequent
batches even when Python is idle or the window is hidden. Existing native
palinstrophy regulation, adaptive batching, inspection cadence, generation checks,
and run-average throughput are retained. The canvas observes native frame
publication directly at 60Hz. Its small seed/time/transport overlays remain native
inside the embedded view; Python supplies the inspector, dashboard, menus and
keyboard actions. This avoids crossing the language boundary for each frame.
The embedding uses the macOS NSView handle exposed by Qt; see
[Qt's native window embedding contract](https://doc.qt.io/qt-6/qtdoc-demos-windowembedding-example.html).

The only adaptation to an existing source is a generated copy of
`FastTurboCanvas.swift` with its single main-bundle metallib lookup changed to an
explicit package resource URL. It is guarded by an exact-match check in the build.
`FastTurboCore.swift`, compute MSL, render MSL, FFT bridge, native controller and
presentation types compile directly from the reference tree, without edits.

GPU commands retain the same allocation, synchronization and math semantics as
Swift. The main simulation uses shared Metal buffers as native GPU resources;
"shared" storage does not imply Python reads them. Spectra and physical fields
can only be exported through explicit developer file operations. Normal rendering
samples completed Metal frame buffers directly. PNG export performs the same
one-off rendered-pixel readback as the reference. Frame extrema and diagnostic
reductions return tiny scalars, not field arrays.

## Build and distribution

`src/fastturbo/build.py` compiles with the reference deployment target (macOS 15,
arm64), Swift 5 language mode, `-O` whole module, C++20 `-O3`, safe compute math and
fast render math. It compiles vendored VkFFT and metal-cpp without downloading or
copying their source. The installed Metal toolchain is selected with `xcrun metal`;
the SDK is passed explicitly (on this Xcode, `xcrun --sdk macosx metal` incorrectly
selects the placeholder executable). The native library is locally signed.

Setuptools invokes the builder for both editable installs and wheels. First
launch also checks source/compiler/SDK hashes; a file lock prevents concurrent
builds and atomic replacement publishes only successful builds. A manifest and
third-party licenses accompany the dylib/metallib. Wheels and app bundles use
packaged native resources and need neither Xcode nor the reference tree at run
time. Development builds use this checkout; source distribution archives stage
the necessary reference sources and vendored headers into the archive only.
`uv.lock` pins the resolved Python dependencies.

The PyInstaller packaging script explicitly collects the ctypes dylib and Metal
resources, freezes Python/Qt into an arm64 `.app`, includes the reference icon,
and verifies the local signature. This follows PyInstaller's
[ctypes and macOS packaging guidance](https://pyinstaller.org/en/stable/feature-notes.html).
It does not install the app into Applications or publish/notarize it.

## Deliberate differences and limits

The scientific behavior, field rendering, parameter semantics and default values
are retained. Surrounding widgets are Qt rather than SwiftUI: typography, switches,
spacing, palette previews and native menu styling differ slightly. Qt sliders
quantize the continuous ranges to 10,001 positions while preserving endpoints.
The square canvas, focus mode, flow overlay, seed/time labels and transport remain.
The original UI has no Streamfunction, forcing/drag, vortex painting, contour,
preset or manual-dt/batch controls; these were not invented. Fixed dt/batch are
available in the API and benchmark CLI as in the reference.

Headless `advance` returns cached energy/enstrophy until `inspect` refreshes them,
matching the native API. Automatic Re regulation depends on diagnostic cadence;
comparisons disable it and explicitly use a common Re. Independent NumPy FP64
code exists only under the supplied validation tools and the test dependency group.
