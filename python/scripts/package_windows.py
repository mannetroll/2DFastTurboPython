"""Freeze the validated Windows application with Python, Qt and native runtimes."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
from fastturbo.build import NATIVE, PROJECT, build


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this packaging script on Windows.")
    build()
    work = PROJECT / "build/pyinstaller-windows"
    work.mkdir(parents=True, exist_ok=True)
    folder = (PROJECT / "dist/2DFastTurbo Python").resolve()
    if not folder.is_relative_to(PROJECT.resolve()):
        raise RuntimeError("Package output must stay inside the project.")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
        "--name", "2DFastTurbo Python", "--distpath", str(PROJECT / "dist"), "--workpath", str(work),
        "--specpath", str(work), "--paths", str(PROJECT / "src"),
        "--icon", str(PROJECT.parent / "reference-windows/assets/2DFastTurbo.ico"),
        "--add-data", f"{PROJECT / 'src/fastturbo/assets/fastturbo.png'}:fastturbo/assets",
        "--add-data", f"{PROJECT / 'src/fastturbo/assets/navier-stokes.png'}:fastturbo/assets",
        "--exclude-module", "numpy", "--exclude-module", "pytest"]
    for item in NATIVE.iterdir():
        if item.suffix == ".dll":
            command += ["--add-binary", f"{item}:fastturbo/_native"]
            if item.name.startswith("nvrtc-builtins"):
                # PyInstaller places a transitive NVRTC copy at _internal's
                # root. Its delayed builtins lookup must work beside that copy.
                command += ["--add-binary", f"{item}:."]
        elif item.suffix == ".txt":
            command += ["--add-data", f"{item}:fastturbo/_native"]
    command += [str(PROJECT / "scripts/bundle_entry.py")]
    environment = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(work / "cache"),
                   "PATH": str(NATIVE)+os.pathsep+os.environ["PATH"]}
    subprocess.run(command, check=True, env=environment)
    (folder / "README.txt").write_text(
        "2DFastTurbo Python for Windows\n\n"
        "Run 2DFastTurbo Python.exe. Keep _internal beside the executable.\n"
        "Requires Windows 10/11 x64 and a compatible NVIDIA GPU/driver.\n"
        "Python, Qt and native runtime libraries are included.\n"
        "No Visual Studio, CMake or CUDA Toolkit installation is needed.\n"
        "Licenses are in _internal/fastturbo/_native.\n", encoding="utf-8")
    archive = shutil.make_archive(str(PROJECT / "dist/2DFastTurbo-Python-Windows"), "zip",
                                  root_dir=folder.parent, base_dir=folder.name)
    print(archive)


if __name__ == "__main__":
    main()
