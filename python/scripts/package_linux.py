"""Freeze an installed Linux release wheel into a portable desktop tarball."""
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import fastturbo


def main():
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise SystemExit("Run this packager on Linux x86-64 with the release wheel installed.")
    project = Path(__file__).resolve().parents[1]
    native = Path(fastturbo.__file__).parent / "_native"
    manifest = json.loads((native / "build-manifest.json").read_text())
    if manifest["bundle_cuda"] != "ON" or manifest["renderer"] != "ON":
        raise SystemExit("Install the bundled CUDA manylinux desktop wheel before packaging.")
    output = project / "dist"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(output), "--workpath", str(project / "build/pyinstaller-linux"),
                    str(project / "packaging/linux-app.spec")], check=True)
    folder = output / "2DFastTurbo-Python"
    if not (folder / "_internal/PySide6/Qt/plugins/platforms/libqxcb.so").is_file():
        raise RuntimeError("Qt's xcb desktop plugin was not collected; check the builder's Qt dependencies.")
    launcher = folder / "2DFastTurbo.sh"
    launcher.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit\nexec ./2dfastturbo "$@"\n')
    launcher.chmod(0o755)
    release = version("fastturbo")
    (folder / "README.txt").write_text(
        f"2DFastTurbo Python {release} for Linux x86-64\n\n"
        "Extract the entire archive, then open 2DFastTurbo.sh or run it in a terminal.\n"
        "Keep _internal beside the executable. No Python or CUDA Toolkit is needed.\n"
        "Requires glibc 2.36+ (Debian 12 / Ubuntu 24.04 or newer), X11/Xwayland,\n"
        "an NVIDIA GPU (compute capability 8.0+), and a driver supporting CUDA 13.1.\n"
        "Install the NVIDIA OpenGL driver and desktop libraries from your distribution.\n"
        "Python, Qt and CUDA runtime libraries are included.\n"
        "Native licenses: _internal/fastturbo/_native\n"
        "FastTurbo licenses: _internal/fastturbo-*.dist-info/licenses\n", encoding="utf-8")
    archive = shutil.make_archive(str(output / f"2DFastTurbo-Python-{release}-Linux-x86_64"),
                                  "gztar", root_dir=output, base_dir=folder.name)
    print(archive)


if __name__ == "__main__":
    main()
