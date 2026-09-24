# Publishing FastTurbo to PyPI

The release contains four artifacts built from the same source version:

| Artifact | Native payload | User requirements |
| --- | --- | --- |
| `py3-none-macosx_15_0_arm64.whl` | Prebuilt Metal / VkFFT | macOS 15+, Apple Silicon |
| `py3-none-win_amd64.whl` | Prebuilt CUDA / VkFFT and CUDA runtime | Windows x64, compatible NVIDIA GPU/driver |
| `py3-none-manylinux_2_28_x86_64.whl` | Prebuilt CUDA / VkFFT, renderer and CUDA runtime | Linux x86-64, glibc 2.28+, compatible NVIDIA GPU/driver |
| `.tar.gz` sdist | Python and native source, including vendored headers | Local compiler, CUDA Toolkit and build dependencies on Linux |

Linux x86-64 is the initial selected manylinux platform. ARM64 and other Linux
targets use the source fallback when their toolchain and dependencies support
them; they are not yet validated release targets. A local `linux_x86_64` wheel
is for that machine only and must not be uploaded. Python wheel selection does
not detect the GPU model or driver version. Users with an incompatible GPU/driver
must explicitly request a source build, where the installed toolkit supports it.

The manylinux builder includes native code for SM 80, 86, 89, 90, 100 and 120,
plus SM 80 PTX. It uses CUDA 13.1 and requires a driver capable of running that
runtime and NVRTC-generated PTX. Python 3.13+ and PySide6 have their own OS/runtime
requirements; a manylinux tag describes the native wheel's glibc compatibility,
not a promise that every dependency supports every old distribution.

## Version and source archive

Run commands below from `python/` unless noted. Choose an unused PyPI version in
`pyproject.toml`, run `uv lock`, and build every artifact with that version.
The checkout remains at 0.1.2; do not replace existing uploads. PyPI filenames
cannot be reused. Existing wheels in `dist/` are not refreshed by an sdist build.

```sh
uv build --sdist --out-dir dist/release
python scripts/check_distributions.py dist/release/fastturbo-*.tar.gz
```

Creating the sdist requires no GPU, CUDA Toolkit, Xcode, or native compilation.
It includes the shared Linux/Windows solver, bridge, CMake files, macOS source,
VkFFT, json, metal-cpp and license files. It excludes generated native binaries
and build caches. Build a wheel from an extracted archive outside the checkout
before release to verify that no private-repository paths are needed.

## macOS and Windows wheels

On Apple Silicon with macOS 15+, Xcode and its Metal tools:

```sh
uv build --wheel --out-dir dist/release
```

On Windows x64 with CUDA 13.1, MSVC x64 tools, CMake and Ninja:

```powershell
$env:CUDA_PATH = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1"
uv build --wheel --out-dir dist/release
```

Leave `FASTTURBO_CUDA_ARCHITECTURES` unset for the Windows release defaults:
`86-real;89-real;120-real;86-virtual`. A `native` build is specific to the build
machine. Collect freshly built macOS and Windows wheels into `dist/release/`.

## Linux manylinux wheel

The manual **Prepare Python distributions** GitHub Actions workflow creates and
checks the sdist, then builds the Linux wheel from that archive in a manylinux
container. It stores artifacts for review and does not publish to PyPI. No GPU
is needed during compilation. GPU and desktop validation are separate release
checks on suitable hardware.

To run the same builder locally with Docker, from the repository root:

```sh
docker build -f python/packaging/manylinux.Dockerfile \
  -t fastturbo-manylinux python/packaging
mkdir -p python/dist/release
docker run --rm \
  -v "$PWD/python/dist/release:/input:ro" \
  -v "$PWD/python/dist/release:/output" \
  fastturbo-manylinux /input/fastturbo-0.1.2.tar.gz /output
```

Substitute the chosen release version. The image is based on PyPA's
`manylinux_2_28_x86_64` with NVIDIA's CUDA 13.1 RHEL 8 toolkit packages. Release
builders can pin `MANYLINUX_IMAGE` to a reviewed digest using `--build-arg`.
The current recipe tracks the image and toolkit patch updates; retain its build
log and the wheel's `build-manifest.json` with release evidence.

The build requires the renderer, sets explicit GPU architectures, and bundles
`libcudart`, `libnvrtc` and NVRTC's matching builtins together by SONAME. It loads
`libcuda.so.1` from the user's driver at runtime. Driver libraries and toolkit
stubs must never appear in the wheel. `auditwheel repair --only-plat` supplies
the selected manylinux tag only after checking symbol versions and bundling
remaining non-policy libraries. NVRTC builtins are staged explicitly because
`dlopen` dependencies are not discovered by auditwheel. The container also
includes the distro license notices for repaired graphics dependencies.
The renderer links the system `libGL.so.1` ABI, which manylinux permits as an
external dependency. Private copies of GLX/OpenGL dispatch libraries break
CUDA/OpenGL context sharing and are rejected by the artifact checker.

See the primary [manylinux documentation](https://github.com/pypa/manylinux) and
[auditwheel documentation](https://github.com/pypa/auditwheel) for the build
baseline and the limits of dependency discovery. Do not rename a locally built
wheel, exclude CUDA driver dependencies from auditing, or suppress ISA checks
to make a failed repair pass.

## Verify the installed artifacts

For each wheel, create an environment outside the checkout, install the exact
wheel, and run `2dbench --help`, `2dfastturbo --help`, a short `2dbench --seconds 2`
run, and the GUI. A help check alone does not exercise CUDA, NVRTC or Metal.
On Windows, use `scripts/check_windows_wheel.py --wheel PATH --gui` as well.

On Linux, run the installed wheel on a machine/container with the NVIDIA driver
but no CUDA Toolkit, no repository and no toolkit `LD_LIBRARY_PATH`. Run
`scripts/check_linux_wheel.py --wheel PATH`, then repeat with `--ptx` to force
embedded PTX execution. Add `--gui` with a PySide6-enabled Python environment to
check desktop rendering and PNG export on X11/Xwayland. Test the oldest supported distribution/dependency
combination as well as a current desktop. Check the compressed wheel size against
the project's PyPI upload limit before release.

Also verify the source fallback outside the checkout:

```sh
python -m venv /tmp/fastturbo-source-check
/tmp/fastturbo-source-check/bin/pip install \
  --no-binary=fastturbo dist/release/fastturbo-0.1.2.tar.gz
/tmp/fastturbo-source-check/bin/2dbench --seconds 2
```

Source builds need CUDA Toolkit 13.x, a compatible C++ compiler, CMake 3.26+ and
Ninja on PATH. `CUDA_HOME` selects the toolkit. `FASTTURBO_CUDA_ARCH=90` selects an
H100 target; the default `native` requires a visible GPU at build time. Set an
explicit architecture for GPU-less builders. Desktop builds need OpenGL/X11/
libpng development files. `FASTTURBO_LINUX_RENDERER=ON` requires rendering;
`OFF` selects a headless build; the default `AUTO` detects available libraries.
Local source builds use system CUDA libraries and do not bundle a toolkit.

## Validate and upload the release set

Keep only the intended four artifacts in `dist/release/`. Run:

```sh
python scripts/check_distributions.py --release-set dist/release/*
uvx twine check --strict dist/release/*
```

The artifact checker rejects generic Linux wheels, missing native resources,
incomplete sdists and mixed versions. With `--release-set`, it also compares every
wheel's shared Python modules with the sdist, catching stale builds that use the
same version number. It complements auditwheel and runtime
validation; it cannot prove binary compatibility from tags alone. Inspect the
native manifests, notices, test results and README before uploading.

After those checks, upload the same reviewed files:

```sh
uvx twine upload dist/release/*
```

Configure an API token or trusted publisher outside source files and command
arguments. After upload, test `pip install fastturbo` in fresh environments:
pip prefers a compatible wheel and otherwise builds the sdist. Use
`pip install --no-binary=fastturbo fastturbo` to exercise the source path even
where a matching wheel exists. `--only-binary=:all:` and uv's `--no-build`
deliberately disable source fallback.

## README and licenses

The PyPI description comes from `README.md` in each distribution. Its screenshot
is hosted in the public, commit-pinned FastTurbo media repository. Rebuild all
artifacts after documentation or metadata changes. FastTurbo is MIT licensed;
the native distribution also carries platform-specific third-party terms in
`THIRD_PARTY_NOTICES.md`, `licenses/` and `fastturbo/_native/`. Preserve the toolkit's
matching CUDA EULA and the notices for any libraries bundled during repair.
