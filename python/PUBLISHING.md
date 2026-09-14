# Publishing FastTurbo to PyPI

Run these commands from `python/`. The PyPI description comes from `README.md`
inside each built distribution, so documentation and metadata changes require
new wheels before uploading.

## Build the two wheels

On an Apple Silicon Mac with macOS 15+, Xcode and its Metal tools:

```sh
uv build --wheel
```

On Windows x64 with CUDA 13.0, MSVC x64 C++ tools, CMake and Ninja:

```powershell
uv build --wheel
```

The build selects the backend for the host platform. macOS produces
`fastturbo-0.1.0-py3-none-macosx_15_0_arm64.whl`; Windows produces
`fastturbo-0.1.0-py3-none-win_amd64.whl`. Build both from the same source version,
then collect them in `dist/`. The wheels contain the Python package, native
resources, screenshot, MIT license and third-party license texts.

Users installing these wheels need Python and the supported GPU environment.
They do not need Xcode, a CUDA Toolkit, or build tools. See `README.md` for the
runtime requirements. See `DEVELOPMENT.md` for native tests and GUI checks.

## Validate and upload

```sh
uvx twine check --strict dist/*.whl
uvx twine upload dist/*.whl
```

Upload the two wheels for this binary release. Use `dist/*.whl`, since `dist/`
can also contain a macOS application, ZIP files and older source archives.
`uvx` runs Twine without rebuilding the local project.

Configure PyPI authentication using a PyPI API token or a trusted publisher.
Keep tokens out of source files and shell command arguments. A successful
Twine check validates metadata and README rendering; it does not exercise the
GPU backend or prove that a project name is available.

PyPI cannot replace an uploaded filename. After publishing 0.1.0, change
`project.version` in `pyproject.toml`, run `uv lock`, and rebuild both wheels
before the next release. Keep the same version on both platforms.

After publishing, test in a fresh Python 3.13 environment on each supported
platform:

```sh
python -m pip install --only-binary=:all: fastturbo
2dfastturbo
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
