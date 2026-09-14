# Windows distribution

The release configuration keeps VkFFT and statically incorporates NVRTC, its
builtins, the PTX compiler, CUDA runtime and MSVC runtime. The application is a
single executable with embedded resources; it does not extract DLLs or shaders
on first launch. The output is `windows/dist/2DFastTurbo.exe`, with a companion
`README.txt` that is informational, not a runtime dependency.

## Original Release audit

The original executable was preserved as
`windows/build-packaging-baseline/2DFastTurbo.exe` before changing the build.
Its SHA-256 is
`b9861faaafe91f126423655bc7a1df3c444b2762d5d05fc39ccca6b079f4e089`,
and its size is 1,266,176 bytes. The audit combines MSVC `dumpbin /DEPENDENTS`
with `EnumProcessModulesEx` sampling throughout a real N=1024 run, including
initial VkFFT plan compilation. Imports alone would miss both dynamically
loaded NVIDIA driver modules and NVRTC's builtins library.

| Dependency | Classification | Original build | Distribution build |
|---|---|---|---|
| d3d11, dxgi, d2d1, DWrite, dwmapi, COMDLG32, SHELL32, ole32, WINMM, KERNEL32, USER32, GDI32 | Windows system DLLs | Imported | Imported |
| ADVAPI32 | Windows system DLL | Transitive | Imported by the static runtime/compiler |
| D3DCOMPILER_47 | Windows system DLL | Compiled HLSL on application startup | Build-time shader compiler only |
| WindowsCodecs, ntdll, ucrtbase, msvcp_win, OLEAUT32, COMCTL32, WS2_32 and other OS services | Windows system DLLs | Loaded as needed | Loaded as needed from Windows |
| nvcuda.dll, nvapi64.dll and NVIDIA modules in DriverStore | NVIDIA driver DLLs | Loaded dynamically | Loaded dynamically; never bundled |
| MSVCP140, VCRUNTIME140, VCRUNTIME140_1 | MSVC runtime | Direct imports | Removed by /MT |
| api-ms-win-crt-* | Windows UCRT API sets | Direct imports | No direct imports; Windows components still use the OS UCRT |
| CUDA runtime | CUDA runtime | Already contained in the executable; no cudart DLL observed | Explicit CUDA::cudart_static linkage |
| nvrtc64_130_0.dll | VkFFT/NVRTC requirement | Direct import, loaded from Toolkit bin/x64 | Statically incorporated |
| nvrtc-builtins64_130.dll | VkFFT/NVRTC requirement | Loaded dynamically from Toolkit bin/x64 | Statically incorporated |
| nvptxcompiler_static.lib | VkFFT/NVRTC requirement | Compiler implementation resides in NVRTC DLL | Linked into EXE to satisfy static NVRTC |
| cuFFT | cuFFT | Not linked or loaded | Not linked or loaded |
| render.hlsl | Application asset | Required beside EXE | Four precompiled shaders embedded as RCDATA |
| Icon, color maps, defaults | Application assets | Icon/resource and compiled source | Embedded |
| Segoe UI and Consolas | Windows system fonts | Supplied by Windows | Supplied by Windows; no font installation |
| JSON/INI/preset files, other third-party runtime | Other | None required | None required |

`msvcp_win.dll` and `ucrtbase.dll` are Windows components, distinct from the
separately redistributable `MSVCP140.dll`/`VCRUNTIME140*.dll`. NVIDIA's
`nvcudart_hybrid64.dll`, observed inside the driver store, belongs to the
installed NVIDIA driver, not the CUDA Toolkit runtime distribution.

## VkFFT and NVRTC findings

The inspected vendored `vkFFT_CompileKernel.h`, CUDA branch, creates CUDA source,
calls `nvrtcCreateProgram` and `nvrtcCompileProgram`, obtains CUBIN through
`nvrtcGetCUBIN`, then loads it with `cuModuleLoadDataEx`. The present build targets
the detected GPU's real architecture. It does not invoke nvcc or an external
compiler at runtime, and these generated kernels require no Toolkit headers.

CUDA 13.0 includes the actual Windows static archives used here:
`nvrtc_static.lib`, `nvrtc-builtins_static.lib` and `nvptxcompiler_static.lib`.
NVIDIA documents this [static NVRTC linkage on Windows](https://docs.nvidia.com/cuda/archive/13.0.0/nvrtc/index.html#nvrtc-static-library).
The CMake imported targets are documented in [FindCUDAToolkit](https://cmake.org/cmake/help/latest/module/FindCUDAToolkit.html#nvrtc).
No nvJitLink, cuFFT or other CUDA Toolkit DLL is needed for this VkFFT path.

VkFFT also implements `saveApplicationToString`/`loadApplicationFromString` for
CUDA. Those can cache compiled plans and bypass NVRTC compilation when loading
a matching plan. They are not a general replacement here: embedded plans would
need to cover the complete grid/pitch/batch/device configuration, including
arbitrary even CLI resolutions, and GPU-specific plan selection. Keeping the
same runtime compiler preserves the existing planning behavior and GPU support.

The cuFFT fallback experiment is unnecessary because the stated condition for
it—a single executable being impractical with VkFFT—did not occur. VkFFT remains
the production and development backend. No numerical kernels, FFT parameters,
precision flags, batch policy, integration algorithm or GUI controls changed.

## Build and resource changes

`-Distribution` creates an isolated `windows/build-dist` Release tree:

- MSVC uses `/MT`; CUDA host compilation also uses `-Xcompiler=-MT`.
- CUDA runtime, NVRTC, builtins and PTX compiler use static archives. Windows and
  NVIDIA driver interfaces retain normal dynamic linkage.
- CUDA device code contains native sm_75, sm_80, sm_86, sm_89 and sm_120 images,
  plus compute_120 PTX. This covers existing Turing/Ampere/Ada/Blackwell RTX
  families. Execution was validated on the RTX 3090, not on every architecture.
- A build-only shader compiler calls the same Windows D3DCompile implementation
  with the original O3/IEEE-strict flags. Four bytecodes are PE resources, loaded
  with FindResource/LoadResource; the application no longer reads HLSL files.
- The icon and shader color maps/defaults are embedded. System fonts stay OS
  resources. No font files, presets, JSON, INI, CSO or HLSL files are distributed.
- CUDA's supplied license/third-party notices and VkFFT's license are embedded;
  `--licenses` prints them. NVIDIA components remain subject to the
  [CUDA license terms](https://docs.nvidia.com/cuda/eula/index.html).

The ordinary development configuration retains dynamic NVRTC linkage and the
RTX 3090 target, so packaging does not force the large compiler archive into
every development build. It also benefits from embedded shaders after rebuilding.

The installation directory is never used for required runtime data. Save PNG
uses the chosen destination; CLI output files are explicit requests. The app has
no persistent settings to relocate. Optional NVIDIA caches belong to the driver
or compiler's user cache locations; clean testing disables the CUDA cache.

## Reproduce

From the repository root, with the existing development prerequisites installed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File windows/scripts/package.ps1
ctest --test-dir windows/build-dist --output-on-failure
python windows/scripts/test_distribution.py
powershell -NoProfile -ExecutionPolicy Bypass -File windows/scripts/clean-machine-test.ps1
python windows/scripts/benchmark_distribution.py --runs 3 --seconds 5
```

The benchmark needs the preserved original binary and its original HLSL file in
`windows/build-packaging-baseline` for comparison. The test harnesses use Python,
NumPy and dumpbin on the development machine; those tools are not application
dependencies and are not distributed. The release needs Windows 10/11 and an
RTX-compatible NVIDIA driver providing CUDA 13 support. NVIDIA specifies the
[R580-or-newer driver range for CUDA 13](https://docs.nvidia.com/cuda/archive/13.0.3/cuda-toolkit-release-notes/index.html).

## Final artifact

**True single application EXE achieved, with VkFFT preserved.**

- Path: `windows/dist/2DFastTurbo.exe`
- Size: **97,549,312 bytes (93.03 MiB)**
- SHA-256: `f864e849ece4a6a15fb04e95d3ac7f49a411584c8adc633dc117b9cb6eb33072`
- External runtime: Windows components and the installed NVIDIA display driver.
- No Toolkit installation, NVRTC DLLs, VC++ Redistributable, loose application
  resources or development tools are required.

The size increase from the original 1.21 MiB EXE primarily incorporates the
runtime compiler that formerly lived in the approximately 91 MB NVRTC DLL and
its builtins. This is native static linkage, with no extraction/bootstrap loader.
There is no need for a separate `dist-portable` DLL bundle.

## Clean-environment result

Passed on Windows 10 Pro build 19045, RTX 3090, NVIDIA driver 616.56. The test
copied **only the EXE** into a new `%TEMP%/2DFastTurbo-package-test-.../application`
folder outside the repository and used a separate, empty working directory.
An ACL made the installation directory read/execute-only; a write probe was
explicitly denied. The folder remained unchanged and its permissions were
restored afterward.

Both headless and GUI processes launched with PATH restricted to Windows and
System32, development environment variables removed, and the CUDA cache disabled.
Module enumeration ran during plan compilation and execution. Every observed
DLL was a Windows component or an NVIDIA driver component; **zero** came from
the CUDA Toolkit, and **zero** were MSVCP140/VCRUNTIME140 runtime DLLs.
The original dependency table and exact loaded paths are preserved in
[packaging/evidence](packaging/evidence).

This was a simulated clean environment on the existing development PC, not a
fresh Windows installation or VM. Windows 11 and other RTX models were not
available for execution tests. Native GPU images for all five listed
architectures were verified with `cuobjdump --list-elf`.

## Correctness gate

| Gate on packaged executable | Result |
|---|---|
| Static-build CTest lifecycle/API checks | Passed |
| Independent FP64/analytic suite, N=32,34,128, both formulations/all methods | 114/114 passed with Windows-only PATH |
| Native initialization and nonlinear step, N=128 through 4096 | All six resolutions passed |
| Full spectra compared with original saved validation outputs | **156/156 bitwise identical** |
| Native GUI control/lifecycle/save-dialog checks from read-only outside-tree copy | 59/59 passed |
| Embedded shaders versus independent Metal color equations | 32/32 passed; maximum one 8-bit channel level difference |
| Embedded license retrieval without source/assets | Passed |

The test harness's permission cleanup was repaired to use `icacls /reset`,
avoiding PowerShell 5.1's SACL privilege requirement. The final complete clean
test exited successfully and restored its directory permissions. Native menu
automation requires the test window to remain foreground; a disturbed menu run
was rerun successfully. Application solver and GUI semantics were unchanged.

## Before/after performance

Three interleaved five-second runs per binary and mode, N=1024/M=1536, seed=2,
k₀=4, Re=50000, CFL=3, ETDRK4/stress, on the same RTX 3090. Headless used
eight warmup steps followed by reset and batch 8. The visible GUI used two
seconds of warmup followed by reset, automatic batching and a 640-pixel canvas.
Original/packaged order alternated between repetitions. Packaged runs used the
clean environment. No correctness tests ran concurrently with these measurements.

| Mode | Original R_turbo | Packaged R_turbo | Original wall ms/step | Packaged wall ms/step | R_turbo change |
|---|---:|---:|---:|---:|---:|
| Headless | 1.260749 | 1.260696 | 1.310519 | 1.310504 | -0.0043% |
| Visible GUI | 1.163113 | 1.161608 | 1.434508 | 1.436632 | -0.1294% |

These differences fall within the observed run-to-run variation. The headless
R_turbo ranges were 1.258413–1.263167 original and 1.256623–1.263032 packaged;
the GUI ranges were 1.159354–1.165371 and 1.155870–1.166507. GPU ms/step also
remained essentially unchanged. There is no measurable packaging regression at
this run length. Initialization/compilation and final exports are excluded from
R_turbo; runtime snapshots and GUI contention are included.

[Individual measurements](packaging/evidence/performance.csv),
[aggregate results](packaging/evidence/summary.json), and the executable hashes
make the comparison reproducible. The preserved original remains the development
baseline, and VkFFT remains the backend in both configurations.
