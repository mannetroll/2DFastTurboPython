# Native Ubuntu CUDA toolkit upgrade — 2026-09-17

Follow-up: these results did not establish clean-build reproducibility. A later
manual launch exposed a compiler-cache invalidation defect. It was reproduced
and fixed; see the [clean-build verification](../linux-clean-build/REPORT.md).

**Completed:** the existing Linux CUDA/VkFFT backend builds, all applicable tests
pass, and `QT_QPA_PLATFORM=xcb uv run 2dfastturbo` launches the shared GUI on the
physical RTX 3090. The application was left running as PID 45609.
No solver, GUI, test, tolerance or fallback code was changed.

The inspected host is native Ubuntu 26.04.1 LTS, kernel 7.0.0-31-generic,
x86_64, GNOME Wayland with XWayland, RTX 3090 (24 GiB, compute capability 8.6),
and NVIDIA driver **595.91.07**. Branch: `LINUX`; initial commit: `8c7879e`;
initial working tree: clean. [PythonPortLinux.md](../../../docs/PythonPortLinux.md) describes an earlier WSL host;
the user's verified native workstation instructions govern this upgrade.

The project requires CUDAToolkit >=13.0 in `python/native/linux/CMakeLists.txt`.
The shell selected Ubuntu's `/usr/bin/nvcc` 12.4.131; the failed CMake cache also
contained architecture 52 and an empty build type. The prior local 13.2 toolkit
was not configured in shell startup. Both environment discovery and stale build
configuration were corrected.

**Packages and system configuration**

- Installed NVIDIA `cuda-keyring` 1.1-1 for the exact `ubuntu2604/x86_64`
  repository, then `cuda-toolkit-13-3=13.3.1-1`, `libpng-dev` and
  `libopengl-dev` with `apt-get --no-install-recommends --no-remove`.
  The transaction added **70 packages including the keyring**, upgraded zero,
  removed zero, and changed no driver or kernel package. Dependencies include
  zlib development files, libxcb-dri2-0/libxcb-xinerama0 and NVIDIA's compiler,
  libraries, debugger, sanitizer and Nsight tools. The toolkit transaction
  reported 3,174 MB downloaded and 6,369 MB installed.
- [Complete package/version delta](packages-changed.json),
  [APT transaction](apt-plan.txt), [installation log](install.log), and
  [workstation installation script](install.sh) are retained here.
- Repository configuration:
  `/etc/apt/sources.list.d/cuda-ubuntu2604-x86_64.list`,
  `/usr/share/keyrings/cuda-archive-keyring.gpg`, and NVIDIA's
  `/etc/apt/preferences.d/cuda-repository-pin-600`.
- Added `/etc/apt/preferences.d/fastturbo-preserve-ubuntu-driver`
  ([contents](driver-pin)): driver packages from NVIDIA's CUDA repository have
  priority -1. The installer also rejected any simulated transaction changing
  driver/kernel packages. Ubuntu continues to manage the installed driver.
- NVIDIA's package scripts configured `/usr/local/cuda` and `/usr/local/cuda-13`
  alternatives to `/usr/local/cuda-13.3`, and linker configuration
  `/etc/ld.so.conf.d/000_cuda.conf` and `987_cuda-13.conf`. The toolkit also
  installs its documented Nsight and cuFile alternatives.
- Ubuntu CUDA 12.4 remains installed side by side; `/usr/bin/nvcc` is still
  12.4.131. It is bypassed by the new PATH and explicit CMake toolkit/compiler
  selection. Earlier files under `~/.local/opt/cuda-13.2` and
  `~/.local/opt/fastturbo-dev` remain intact and are unused by this build/run.
- Added `~/.config/fastturbo/cuda-env.sh` ([snapshot](cuda-env.sh)), sourced by
  both `~/.bashrc` and `~/.profile`. It sets `CUDA_HOME`, `CUDA_PATH` and
  `CUDAToolkit_ROOT` to `/usr/local/cuda-13.3`, `CUDACXX` to its `bin/nvcc`,
  and `CUDAHOSTCXX`/`NVCC_CCBIN` to `/usr/bin/g++`. It prepends that toolkit's
  `bin` and runtime `lib64`, includes `~/.local/bin`, and selects
  `FASTTURBO_LINUX_RENDERER=ON`. Link-time stub libraries are not on runtime paths.
- `CMAKE_PREFIX_PATH` is unset: all graphics development libraries are now system
  packages. `python/build/local-env.sh` now sources the same configuration.
  Shell/helper backups are in `~/.cache/fastturbo-cuda-upgrade/shell-before/`.
  The old build is preserved as `python/build/linux-before-cuda133-20260917/`;
  [its failed cache](cmake-cache-before.txt) is also retained here.
- `dpkg --audit` returned no issues. `uv sync --locked` succeeded without
  changing Python dependencies or the lockfile.

This follows NVIDIA's [Ubuntu network repository installation method](https://docs.nvidia.com/cuda/archive/13.3.1/cuda-installation-guide-linux/index.html#network-repo-installation-for-ubuntu).
CUDA 13.3 supports Ubuntu 26.04; the 13.2 release predates its qualification.
NVIDIA's [minor-version compatibility rules](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)
allow CUDA 13.x binaries on drivers >=580 subject to feature/PTX restrictions.
This application emits native `sm_86` code; its existing VkFFT NVRTC path also
emits CUBIN for `sm_86`. Actual kernel, FFT, parity and GUI execution passed on
595.91.07. `nvidia-smi` correctly still reports the driver's CUDA capability as
13.2; the installed toolkit is 13.3.1. No driver replacement or compatibility
package was needed.

**Toolchain and exact build**

| Tool | Version |
|---|---|
| CUDA toolkit meta-package | 13.3.1-1 |
| nvcc | 13.3.73 |
| CUDA runtime | 13.3.29 |
| NVRTC and matching builtins | 13.3.33 |
| GCC / G++ | 15.2.0, Ubuntu 15.2.0-16ubuntu1 |
| CMake | 4.4.3 |
| Ninja | 1.13.2.git.kitware.jobserver-pipe-1 |
| uv | 0.12.15 |
| Python / PySide6 | 3.14.4 / 6.11.2 |
| pytest / NumPy | 9.1.1 / 2.5.3 |

The existing build command `uv run fastturbo-build`, run from `python/`, used:

```sh
cmake \
  -S \
  /home/tobbe/github/2DFastTurboPython/python/native/linux \
  -B \
  /home/tobbe/github/2DFastTurboPython/python/build/linux \
  -G \
  Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.3/bin/nvcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/c++ \
  -DCUDAToolkit_ROOT=/usr/local/cuda-13.3 \
  -DCMAKE_CUDA_ARCHITECTURES=86 \
  -DFASTTURBO_RENDERER=ON \
  -DFASTTURBO_WINDOWS=/home/tobbe/github/2DFastTurboPython/reference-windows \
  -DFASTTURBO_VKFFT=/home/tobbe/github/2DFastTurboPython/reference-macos/2DFastTurbo/ThirdParty/VkFFTSource/vkFFT
cmake --build /home/tobbe/github/2DFastTurboPython/python/build/linux --parallel 2
```

The CUDA host compiler is `/usr/bin/g++`. Release uses `-O3 -DNDEBUG`, C++20,
CUDA C++20, `--fmad=false` and `-ffp-contract=off` for numerical sources.
Architecture `86` emits both `compute_86` PTX and native `sm_86` code. The backend
and CUDA/OpenGL renderer were both built and staged in `src/fastturbo/_native`.
[Build log](build.log), [configuration](build-config.json),
[CMake cache](cmake-cache.txt), [source/tool manifest](build-manifest.json), and
[toolchain/environment](toolchain.json) record the exact state.
`cuobjdump --list-elf` confirms `libfastturbo.1.sm_86.cubin`.
The backend RUNPATH is `/usr/local/cuda-13.3/lib64`; driver lookup resolves to
Ubuntu's `/usr/lib/x86_64-linux-gnu/libcuda.so.595.91.07`.

**Tests and parity**

```sh
cd python
source "$HOME/.config/fastturbo/cuda-env.sh"
build/linux/device_test
ctest --test-dir build/linux --output-on-failure
QT_QPA_PLATFORM=xcb FASTTURBO_EVIDENCE_DIR="$PWD/evidence/linux-cuda-upgrade" \
  uv run pytest -q -ra --junitxml=evidence/linux-cuda-upgrade/tests.xml \
  --basetemp=build/cuda133-pytest
```

- Device test: RTX 3090 / capability 8.6 enumerated; allocation, kernel execution,
  synchronization and result readback passed (42).
- Native CTest: **1/1 passed**, zero failures.
- Full pytest: **48 passed, 12 skipped, zero failures**, 47.62 seconds. All skips
  are Windows-only tests. [JUnit results](tests.xml), [pytest output](pytest.log).
- Existing numerical checks cover all integrators/forms, independent FP64
  comparisons, FFT normalization/Nyquist/padding, initialization/reset, long-run
  decay, and N1024/N2048 spectra byte-identical to the independent CUDA CLI
  invocation of the shared Windows reference sources.
- Stored Windows 3,832-step trajectory: maximum relative diagnostic error
  **1.8721444895292742e-6**, within the unchanged 3e-6 tolerance.
  [Parity results](windows-parity.json). No new Windows execution is claimed.
- Shared GUI: **52 assertions passed**. [GUI controls](gui-controls.json).
- Independent GPU rendering comparisons: **24/24 passed**, maximum pixel error
  **1/255**, unchanged tolerance **2/255**. [Pixel validation](colors-validation.json).
- Native workers advanced **6,258 steps and 36 presentations** during 0.6 seconds
  without Python/Qt event processing. Actual X11 transport buttons, Save dialog
  cancellation, recovery after export failure, and fatal renderer shutdown passed.
  [Worker results](workers.json).
- The headless test passed with display variables and `LD_LIBRARY_PATH` removed,
  without importing Qt or loading X11/GL/EGL/Wayland libraries. Computation
  remained CUDA/VkFFT.

The earlier optional all-resolutions experiment and its 24 GiB VRAM limit at
N12288 remain documented in `LINUX_PORT.md`; this environment upgrade did not
repeat that experiment or change available resolutions/tests to hide the limit.

**Benchmarks**

```sh
QT_QPA_PLATFORM=xcb uv run python scripts/benchmark_linux.py \
  --mode both --runs 3 --seconds 5 --resolutions 1024 2048 \
  --output evidence/linux-cuda-upgrade
uv run python scripts/profile_overhead.py
```

Three alternating headless/GUI runs per grid, five measured seconds each,
seed 2, k0=4, Re=50000, CFL=3, ETDRK4/stress. Headless: batch8, eight warmup
steps and reset. GUI: native adaptive batches, two-second warmup and reset,
640-pixel canvas. Medians:

| Retained → padded | Headless ms/step | Headless steps/s | Headless R_turbo | GUI ms/step | GUI steps/s | GUI R_turbo | GUI overhead |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1024² → 1536² | 1.388466 | 720.219 | 1.196666 | 1.494758 | 669.005 | 1.120788 | 6.34% |
| 2048² → 3072² | 5.452248 | 183.411 | 0.163919 | 6.057447 | 165.086 | 0.147112 | 10.25% |

All 12 timed runs passed their finite-state/positive-step checks.
[Comparison](performance-comparison.json), [headless](performance-headless.json),
[GUI](performance-gui.json) and individual raw reports are retained here.
Overhead is `100 * (R_headless - R_gui) / R_headless`; steps/s is solver throughput,
not display refresh rate. These are short desktop benchmarks, not an isolated
measurement of compiler-version performance.

Existing overhead benchmark medians: batch8 **11.070269 ms**, inspection
**0.033518 ms**, vorticity snapshot **0.081627 ms**, velocity snapshot
**0.158781 ms**, no-op ABI upper bound **0.008696 ms**.
[Full timing](profile-overhead.json).

**Actual GUI and physical GPU verification**

Launched exactly `QT_QPA_PLATFORM=xcb uv run 2dfastturbo` from `python/` using the
configured login-shell environment. The normal application, with no benchmark
arguments, displayed a changing 2048² vorticity field and advancing statistics.
[Window capture](gui-live.png), [later capture](gui-live-later.png),
[launch verification](gui-live.json).

The X11 window `0xc00007` has `_NET_WM_PID=45609` and the expected
2DFastTurbo WM_CLASS. Its process loads Qt's `libqxcb.so`, the native OpenGL
renderer, CUDA runtime 13.3.29, NVRTC/builtins 13.3.33, and NVIDIA's 595.91.07
CUDA/GLX libraries. No earlier user-local CUDA or graphics libraries are mapped.
[Loaded libraries](gui-loaded-libraries.txt), [X11 properties](gui-x11-properties.txt).

`nvidia-smi` shows this same PID as **C+G**, with **740 MiB** GPU memory on GPU 0,
RTX 3090, UUID `GPU-4278efe6-c03c-f6c9-69fc-9945957934ee`.
Per-process monitoring sampled **99%, 99%, 59% SM utilization**.
[GPU process listing](nvidia-smi-gui.txt), [process utilization](nvidia-smi-pmon.txt).
The driver remained 595.91.07 throughout. The native XWayland/XCB renderer design
is preserved.

**Using the configured environment**

New Bash terminals load CUDA automatically. In an already-open terminal, load it
once before launching:

```sh
source "$HOME/.config/fastturbo/cuda-env.sh"
cd /home/tobbe/github/2DFastTurboPython/python
QT_QPA_PLATFORM=xcb uv run 2dfastturbo
```

**Git state**

Only documentation and this evidence directory were added/updated:
`LINUX_PORT.md`, `PERFORMANCE.md`, `python/evidence/linux-cuda-upgrade/`.
Application, native solver, GUI, tests, lockfile and tolerances are unchanged.
No commit or push was made. Native binaries and preserved build caches remain
ignored build outputs. `git diff --check` passed.
