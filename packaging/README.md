# Native releases

GitHub Releases contain native applications. PyPI contains the separate Python
application's wheels and source archive; see [Python publishing](../python/PUBLISHING.md).

| Download | Application | Runtime requirements |
| --- | --- | --- |
| `2DFastTurbo-VERSION-macOS-arm64.dmg` | Swift/Metal app; drag to Applications | Apple Silicon, macOS 15+ |
| `2DFastTurbo-VERSION-Windows-x86_64.exe` | Standalone native C++/Direct3D executable | Windows 10/11 x64, NVIDIA GPU and CUDA 13-compatible driver |
| `2DFastTurbo-VERSION-Linux-x86_64.tar.gz` | Standalone native C++/Dear ImGui executable | x86-64, glibc 2.39+, NVIDIA Ampere or newer GPU/driver, X11/Xwayland and NVIDIA OpenGL 3.3+ |

Windows and Linux statically link the CUDA runtime, NVRTC and their C++ runtimes.
The Linux executable also contains libpng and zlib. No Python, Qt, CUDA Toolkit,
runtime extraction, or companion library directory is required. System graphics
libraries and the NVIDIA driver remain operating-system dependencies. Linux
CUDA/OpenGL interop requires a native NVIDIA graphics session; WSLg and XQuartz
are not supported presentation paths.

For Linux, extract the archive and run `./2DFastTurbo-VERSION-Linux-x86_64`.
Both executables expose bundled license notices with `--licenses`.
The macOS app uses an ad-hoc signature and is not Apple-notarized.

## Build

From the repository root on macOS, with Xcode and the Metal toolchain:

```sh
bash packaging/package-macos.sh 0.1.2
```

For Windows, run the **Build native Windows release** GitHub Actions workflow
with the release version. It downloads checksum-verified CUDA 13.4.2 compiler
components and builds with MSVC. For a local build with the Visual Studio C++
tools, CMake, Ninja and `CUDA_PATH` set to a CUDA 13.x toolkit:

```powershell
./reference-windows/scripts/package.ps1
```

For Linux, Docker can build x86-64 artifacts on either x86-64 or Apple Silicon:

```sh
docker build --platform linux/amd64 -t fastturbo-native-linux \
  -f packaging/native-linux.Dockerfile packaging
mkdir -p build-native-release/linux-build dist-native
docker run --rm --platform linux/amd64 \
  -v "$PWD:/source:ro" -v "$PWD/build-native-release/linux-build:/build" \
  -v "$PWD/dist-native:/output" fastturbo-native-linux 0.1.2
```

The Linux build runs CLI checks and rejects unexpected shared runtime dependencies.
GPU and GUI validation require the corresponding hardware and a visible desktop.
Upload versioned native downloads and `SHA256SUMS.txt` to the matching GitHub tag.
Build and publish Python artifacts with the separate PyPI workflow.
