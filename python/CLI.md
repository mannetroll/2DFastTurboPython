# FastTurbo CLI

## Run a local wheel with uv

Run from the folder containing the matching **0.1.2 wheel** (for example,
`python/dist/`). `2dbench` runs the benchmark; `2dfastturbo` opens the GUI.
The commands use an isolated Python 3.13 environment and prebuilt packages.

### macOS — Apple Silicon / Metal

```sh
uvx --isolated --python 3.13 --no-build --from ./fastturbo-0.1.2-py3-none-macosx_15_0_arm64.whl 2dbench
uvx --isolated --python 3.13 --no-build --from ./fastturbo-0.1.2-py3-none-macosx_15_0_arm64.whl 2dfastturbo
```

### Windows — x64 / NVIDIA CUDA (PowerShell)

```powershell
uvx --isolated --python 3.13 --no-build --from .\fastturbo-0.1.2-py3-none-win_amd64.whl 2dbench
uvx --isolated --python 3.13 --no-build --from .\fastturbo-0.1.2-py3-none-win_amd64.whl 2dfastturbo
```

### Linux — x86_64 / NVIDIA CUDA

Use a locally built Linux wheel on a compatible system with the required NVIDIA
driver and CUDA runtime libraries. The GUI also needs a graphical desktop and
Qt/OpenGL runtime libraries; `2dbench` works over SSH without a display.

```sh
uvx --isolated --python 3.13 --no-build --from ./fastturbo-0.1.2-py3-none-linux_x86_64.whl 2dbench
uvx --isolated --python 3.13 --no-build --from ./fastturbo-0.1.2-py3-none-linux_x86_64.whl 2dfastturbo
```

`2dbench` prints the hardware header first, then benchmarks 2048² and 4096² for
at least 20 seconds each. Append `--seconds 2` for a quick check, or `--json`
for JSON output. Append `--help` to either command to see options without
starting the benchmark or GUI.

## Build a wheel

If the matching 0.1.2 wheel is missing, run this from `python/` on that platform:

```sh
uv build --wheel
```

The wheel is written to `dist/`. Building requires the platform's native build
tools; see [development setup](DEVELOPMENT.md). Renaming a 0.1.1 wheel does not
add `2dbench`.

## Run from the source checkout

From `python/` on macOS, Windows or Linux:

```sh
uv run 2dfastturbo
uv run 2dbench
```
