"""Content-addressed Linux Release build of the shared Windows/Linux CUDA core."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def build(project: Path, native: Path, force: bool = False) -> Path:
    library = native / "libfastturbo.so"
    reference = project.parent / "reference-windows"
    if not reference.is_dir():
        reference = project / "native/reference-windows"
    if not reference.is_dir():
        if library.is_file():
            return library
        raise RuntimeError("CUDA reference sources missing; use the full repository or a built Linux wheel.")
    mac = reference.parent / "reference-macos/2DFastTurbo"
    vkfft = mac / "ThirdParty/VkFFTSource/vkFFT"
    cuda = Path(os.environ.get("CUDA_HOME", "/usr/local/cuda"))
    nvcc = cuda / "bin/nvcc"
    if not nvcc.is_file():
        found = shutil.which("nvcc")
        if not found:
            raise RuntimeError("CUDA Toolkit not found; set CUDA_HOME to the existing toolkit.")
        nvcc = Path(found).resolve()
        cuda = nvcc.parents[1]
    architectures = os.environ.get("FASTTURBO_CUDA_ARCH", os.environ.get("FASTTURBO_CUDA_ARCHITECTURES", "86"))
    if not re.fullmatch(r"\d+(?:-(?:real|virtual))?(?:;\d+(?:-(?:real|virtual))?)*", architectures):
        raise RuntimeError("FASTTURBO_CUDA_ARCH must be a CUDA architecture, e.g. 86 or 90.")
    source = project / "native/linux"
    directory = project / "build/linux"
    directory.mkdir(parents=True, exist_ok=True)
    native.mkdir(parents=True, exist_ok=True)
    files = [Path(__file__), project / "native/fastturbo.h"]
    for folder in (source, project / "native/cuda", project / "native/windows/vendor", reference / "src", reference / "tests", vkfft):
        files.extend(sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix != ".md"))
    def version(command):
        return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()
    manifest = {
        "files": {str(p.relative_to(project.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "cuda": str(cuda.resolve()), "architectures": architectures,
        "nvcc": version([str(nvcc), "--version"]),
        "cxx": version([os.environ.get("CXX", "c++"), "--version"]),
        "cmake": version(["cmake", "--version"]), "build_type": "Release",
    }
    with (directory / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stamp = native / "build-manifest.json"
        if not force and library.is_file() and stamp.is_file() and json.loads(stamp.read_text()) == manifest:
            return library
        print("Building native FastTurbo (shared CUDA + VkFFT, Linux)…", file=sys.stderr, flush=True)
        subprocess.run(["cmake", "-S", str(source), "-B", str(directory), "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_CUDA_COMPILER={nvcc}",
            f"-DCUDAToolkit_ROOT={cuda}", f"-DCMAKE_CUDA_ARCHITECTURES={architectures}",
            f"-DFASTTURBO_WINDOWS={reference}", f"-DFASTTURBO_VKFFT={vkfft}"], check=True)
        subprocess.run(["cmake", "--build", str(directory), "--parallel", "2"], check=True)
        staging = library.with_suffix(".so.tmp")
        shutil.copy2(directory / library.name, staging)
        os.replace(staging, library)
        for src, name in [(vkfft.parent / "LICENSE", "VkFFT-LICENSE.txt"),
                          (project / "native/windows/vendor/json-LICENSE.txt", "json-LICENSE.txt")]:
            shutil.copy2(src, native / name)
        stamp.write_text(json.dumps(manifest, indent=2) + "\n")
    return library
