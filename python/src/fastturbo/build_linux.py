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
from .build_info import vkfft_version


def _configure(source: Path, directory: Path, **definitions: str | Path) -> None:
    # A compiler change makes CMake discard its cache and restart without the
    # other -D arguments. Start fresh ourselves so source roots, Release flags,
    # GPU architectures and rendering settings survive toolchain changes.
    subprocess.run(["cmake", "--fresh", "-S", str(source), "-B", str(directory), "-G", "Ninja",
        *(f"-D{key}={value}" for key, value in definitions.items())], check=True)


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
    # /usr/local/cuda and /usr/bin/c++ commonly alias versioned installations.
    # Use the same identities in the manifest and the CMake command line.
    cuda, nvcc = cuda.resolve(), nvcc.resolve()
    cxx = shutil.which(os.environ.get("CXX", "c++"))
    if not cxx:
        raise RuntimeError("C++ compiler not found; set CXX to the existing compiler executable.")
    cxx = str(Path(cxx).resolve())
    architectures = os.environ.get("FASTTURBO_CUDA_ARCH", os.environ.get("FASTTURBO_CUDA_ARCHITECTURES", "native"))
    if architectures != "native" and not re.fullmatch(r"\d+(?:-(?:real|virtual))?(?:;\d+(?:-(?:real|virtual))?)*", architectures):
        raise RuntimeError("FASTTURBO_CUDA_ARCH must be native or a CUDA architecture, e.g. 86 or 90.")
    renderer = os.environ.get("FASTTURBO_LINUX_RENDERER", "AUTO").upper()
    if renderer not in {"AUTO", "ON", "OFF"}:
        raise RuntimeError("FASTTURBO_LINUX_RENDERER must be AUTO, ON or OFF.")
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
        "vkfft_version": vkfft_version(vkfft / "vkFFT.h"),
        "renderer": renderer, "cmake_prefix_path": os.environ.get("CMAKE_PREFIX_PATH", ""),
        "nvcc": version([str(nvcc), "--version"]),
        "cxx_path": cxx, "cxx": version([cxx, "--version"]),
        "cmake": version(["cmake", "--version"]), "build_type": "Release",
    }
    with (directory / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stamp = native / "build-manifest.json"
        if (not force and (directory / "build.ninja").is_file() and library.is_file()
                and stamp.is_file() and json.loads(stamp.read_text()) == manifest):
            return library
        print("Building native FastTurbo (shared CUDA + VkFFT, Linux)…", file=sys.stderr, flush=True)
        _configure(source, directory, CMAKE_BUILD_TYPE="Release", CMAKE_CUDA_COMPILER=nvcc,
            CMAKE_CXX_COMPILER=cxx, CUDAToolkit_ROOT=cuda, CMAKE_CUDA_ARCHITECTURES=architectures,
            FASTTURBO_RENDERER=renderer, FASTTURBO_WINDOWS=reference, FASTTURBO_VKFFT=vkfft)
        subprocess.run(["cmake", "--build", str(directory), "--parallel", "2"], check=True)
        staging = library.with_suffix(".so.tmp")
        shutil.copy2(directory / library.name, staging)
        os.replace(staging, library)
        render_library = directory / "libfastturbo_gl.so"
        # CMake leaves old outputs on disk when an optional target is disabled.
        cache = (directory / "build.ninja").read_text()
        if renderer != "OFF" and "build libfastturbo_gl.so:" in cache:
            staging = native / "libfastturbo_gl.so.tmp"
            shutil.copy2(render_library, staging)
            os.replace(staging, native / render_library.name)
        else:
            (native / "libfastturbo_gl.so").unlink(missing_ok=True)
        for src, name in [(vkfft.parent / "LICENSE", "VkFFT-LICENSE.txt"),
                          (project / "native/windows/vendor/json-LICENSE.txt", "json-LICENSE.txt")]:
            shutil.copy2(src, native / name)
        stamp.write_text(json.dumps(manifest, indent=2) + "\n")
    return library
