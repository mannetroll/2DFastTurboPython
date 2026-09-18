# Publishing FastTurbo to PyPI

Run these commands from `python/`. The PyPI description comes from `README.md`
inside each built distribution, so documentation and metadata changes require
updated wheel metadata before uploading.

## Build the 0.1.2 wheels

On an Apple Silicon Mac with macOS 15+, Xcode and its Metal tools:

```sh
git pull --ff-only
uv build --wheel
```

On Windows x64 with CUDA 13.1, MSVC x64 C++ tools, CMake and Ninja:

```powershell
$env:CUDA_PATH = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1"
uv build --wheel
```

Windows builds include RTX 30/40/50-series machine code and baseline PTX by
default. Leave `FASTTURBO_CUDA_ARCHITECTURES` unset for portable releases;
an override of `native` restricts the result to the build host's GPU architecture.
This also applies to `scripts/package-windows.ps1` USB bundles.

The build selects the backend for the host platform. macOS produces
`fastturbo-0.1.2-py3-none-macosx_15_0_arm64.whl`; Windows produces
`fastturbo-0.1.2-py3-none-win_amd64.whl`. Build both from the same source version,
then collect them in `dist/`. The wheels contain the Python package, native
resources, screenshot, MIT license and third-party license texts. Both
`2dfastturbo` and the new `2dbench` command are installed by the wheel.
Older 0.1.1 wheels must be rebuilt; renaming them does not add the benchmark.

The local `fastturbo-0.1.2-py3-none-linux_x86_64.whl` is **not a PyPI upload
artifact**. It depends on system CUDA/graphics libraries and has a generic Linux
tag. Publishing Linux support requires a compatible manylinux build, dependency
audit/repair and validation on Linux; renaming the file is not sufficient.
See [PyPA's binary packaging guide](https://packaging.python.org/en/latest/guides/packaging-binary-extensions/).

Users installing these wheels need Python and the supported GPU environment.
They do not need Xcode, a CUDA Toolkit, or build tools. See `README.md` for the
runtime requirements. See `DEVELOPMENT.md` for native tests and GUI checks.

## Test the installed wheel

On macOS, run the wheel directly in an isolated environment before publishing:

```sh
uvx --isolated --python 3.13 --no-build --from ./dist/fastturbo-0.1.2-py3-none-macosx_15_0_arm64.whl 2dbench --help
uvx --isolated --python 3.13 --no-build --from ./dist/fastturbo-0.1.2-py3-none-macosx_15_0_arm64.whl 2dfastturbo --help
```

On Windows (PowerShell):

```powershell
uvx --isolated --python 3.13 --no-build --from .\dist\fastturbo-0.1.2-py3-none-win_amd64.whl 2dbench --help
uvx --isolated --python 3.13 --no-build --from .\dist\fastturbo-0.1.2-py3-none-win_amd64.whl 2dfastturbo --help
```

These help commands check the installed entry points without starting the GPU,
benchmark or GUI. To check runtime behavior, omit `--help` from `2dfastturbo` to
open the GUI, or replace it with `--seconds 2` for a short `2dbench` run.
See [CLI.md](CLI.md) for local-wheel commands on each platform.

## Validate and upload

```sh
uvx twine check --strict dist/fastturbo-0.1.2-py3-none-macosx_15_0_arm64.whl dist/fastturbo-0.1.2-py3-none-win_amd64.whl
uvx twine upload dist/fastturbo-0.1.2-py3-none-macosx_15_0_arm64.whl dist/fastturbo-0.1.2-py3-none-win_amd64.whl
```

Upload these two 0.1.2 wheels for this binary release. The explicit filenames
exclude older wheels, application bundles, ZIP files and source archives.
`uvx` runs Twine without rebuilding the local project.
Do not use `dist/*` or `dist/*.whl`: those also select the local Linux wheel.

Configure PyPI authentication using a PyPI API token or a trusted publisher.
Keep tokens out of source files and shell command arguments. A successful
Twine check validates metadata and README rendering; it does not exercise the
GPU backend or prove that a project name is available.

PyPI cannot replace an uploaded filename. For each subsequent release, change
`project.version` in `pyproject.toml`, run `uv lock`, and rebuild both wheels
before the next release. Keep the same version on both platforms.

After publishing, test in a fresh Python 3.13 environment on each supported
platform:

```sh
python -m pip install --only-binary=:all: fastturbo
2dfastturbo
2dbench --seconds 2
```

## README image

The README uses a public, commit-pinned image in
[mannetroll/fastturbo-media](https://github.com/mannetroll/fastturbo-media).
The application source repository can remain private. PyPI cannot serve an
image from inside a wheel or read a private GitHub image URL.

The identical image is also bundled at
`src/fastturbo/assets/macos-5000-iterations.png`. To change the screenshot, update
the bundled copy, publish it to the media repository, and update the README's
public URL before rebuilding both wheels.

## Licensing

FastTurbo's own code and screenshot use `LICENSE` (MIT).
`THIRD_PARTY_NOTICES.md` documents the bundled components. `setup.py` supplies a
platform-specific SPDX license expression; `pyproject.toml` declares the license
files so they are included in wheels and source archives. Preserve the original
CUDA agreement when rebuilding on Windows and review it when updating CUDA.
