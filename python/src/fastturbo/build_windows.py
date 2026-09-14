"""Release CUDA build using the reference's MSVC/Ninja toolchain and GPU core."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def build(project: Path, native: Path, force=False) -> Path:
    library = native / "fastturbo.dll"
    reference = project.parent / "reference-windows"
    if not reference.is_dir():
        reference = project / "native/reference-windows"
    if not reference.is_dir() and library.is_file():
        return library
    if not reference.is_dir():
        raise RuntimeError("Windows reference sources are missing; install a built wheel or use the full repository.")
    mac = reference.parent / "reference-macos/2DFastTurbo"
    cuda = Path(os.environ.get("CUDA_PATH", "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.0"))
    vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    vs = subprocess.check_output([str(vswhere), "-latest", "-products", "*", "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"], text=True).strip()
    if not vs or not (cuda / "bin/nvcc.exe").is_file():
        raise RuntimeError("Install the reference prerequisites: MSVC x64 C++ tools and CUDA Toolkit 13.0.")
    source = project / "native/windows"
    directory = project / "build/windows"
    directory.mkdir(parents=True, exist_ok=True)
    native.mkdir(parents=True, exist_ok=True)
    files = [Path(__file__), project / "native/fastturbo.h"]
    for folder in [source, reference / "src", reference / "tools", mac / "ThirdParty/VkFFTSource/vkFFT"]:
        files.extend(sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix != ".md"))
    files.append(reference / "CMakeLists.txt")
    distribution = os.environ.get("FASTTURBO_DISTRIBUTION", "OFF")
    architectures = os.environ.get("FASTTURBO_CUDA_ARCHITECTURES", "86")
    manifest = {"files": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                "cuda": str(cuda), "msvc": vs, "distribution": distribution, "architectures": architectures,
                "nvcc": subprocess.check_output([str(cuda / "bin/nvcc.exe"), "--version"], text=True)}
    # MSVC's nested VkFFT includes can exceed MAX_PATH in an extracted sdist.
    # Publish immutable, content-addressed headers at a short temporary path.
    vkfft = mac / "ThirdParty/VkFFTSource/vkFFT"
    header_hash = hashlib.sha256("".join(manifest["files"][str(p)] for p in sorted(vkfft.rglob("*")) if p.is_file()).encode()).hexdigest()[:16]
    cache = Path(tempfile.gettempdir()) / "ft-vkfft"
    headers = cache / header_hash
    if not (headers / "vkFFT.h").is_file():
        cache.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="stage-", dir=cache))
        shutil.copytree(vkfft, staging, dirs_exist_ok=True)
        try:
            staging.rename(headers)
        except FileExistsError:
            pass  # Another source build already published identical headers.
    # Windows byte-range lock serializes editable/wheel/import builds.
    import msvcrt
    with (directory / "build.lock").open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        while True:
            try:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(.1)
        try:
            stamp = native / "build-manifest.json"
            if not force and library.is_file() and stamp.is_file() and json.loads(stamp.read_text()) == manifest:
                return library
            subprocess.run([str(source / "build.cmd"), vs, str(cuda), str(source), str(directory),
                            str(reference), str(mac), architectures, distribution, str(headers)], check=True)
            for name in ["fastturbo.dll"]:
                try:
                    shutil.copy2(directory / name, native / name)
                except PermissionError as error:
                    raise RuntimeError("Close running FastTurbo/Python processes before rebuilding the Windows DLL.") from error
            if distribution != "ON":
                for pattern in ["cudart64_*.dll", "nvrtc64_*.dll", "nvrtc-builtins64_*.dll"]:
                    candidates = list((cuda / "bin").glob(pattern)) + list((cuda / "bin/x64").glob(pattern))
                    if not candidates:
                        raise RuntimeError(f"Missing CUDA runtime: {pattern}")
                    for dll in candidates:
                        if ".alt." not in dll.name:
                            shutil.copy2(dll, native / dll.name)
            for src, name in [(cuda / "EULA.txt", "CUDA-EULA.txt"),
                (mac / "ThirdParty/VkFFTSource/LICENSE", "VkFFT-LICENSE.txt"),
                (source / "vendor/json-LICENSE.txt", "json-LICENSE.txt")]:
                shutil.copy2(src, native / name)
            stamp.write_text(json.dumps(manifest, indent=2) + "\n")
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    return library
