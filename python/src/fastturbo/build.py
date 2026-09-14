"""Reproducible, content-addressed native build; no third-party build tools."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
ROOT = PROJECT.parent
REFERENCE = ROOT / "reference-macos/2DFastTurbo"
if not REFERENCE.exists() and (PROJECT / "native/reference").is_dir():
    REFERENCE = PROJECT / "native/reference"  # Source distribution staging.
NATIVE = PACKAGE / "_native"


def run(args: list[str | Path], **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def build(force: bool = False) -> Path:
    library = NATIVE / "libfastturbo.dylib"
    if not REFERENCE.exists() and library.exists():
        return library  # Installed wheel / frozen .app needs no compiler or sources.
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("FastTurbo requires Apple Silicon and macOS 15 or later.")
    source = REFERENCE / "Sources"
    files = sorted(p for p in source.rglob("*") if p.is_file())
    files += sorted(p for p in (REFERENCE / "ThirdParty").rglob("*") if p.is_file())
    files += sorted((PROJECT / "native").glob("*.swift")) + [Path(__file__)]
    sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
    compiler = subprocess.check_output(["xcrun", "swiftc", "--version"], text=True, stderr=subprocess.STDOUT).strip()
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    manifest = {"files": hashes, "sdk": sdk, "compiler": compiler, "target": "arm64-apple-macosx15.0"}
    NATIVE.mkdir(parents=True, exist_ok=True)
    build_dir = PROJECT / "build/native"
    build_dir.mkdir(parents=True, exist_ok=True)
    with (build_dir / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stamp = NATIVE / "build-manifest.json"
        if not force and library.exists() and (NATIVE / "default.metallib").exists() and stamp.exists():
            if json.loads(stamp.read_text()) == manifest:
                return library
        print("Building native FastTurbo (Swift + Metal + VkFFT)…", file=sys.stderr, flush=True)
        bridge = source / "Bridge"
        includes = [bridge, REFERENCE / "ThirdParty/VkFFTSource/vkFFT", REFERENCE / "ThirdParty/VkFFTSource/metal-cpp"]
        common = ["-target", "arm64-apple-macosx15.0", "-isysroot", sdk, "-O3", "-std=c++20", "-fPIC"]
        for inc in includes:
            common += ["-I", str(inc)]
        objects = []
        for name in ("MTTVkFFTCore.cpp", "MTTVkFFTBackend.mm"):
            obj = build_dir / (name + ".o")
            extra = ["-fobjc-arc"] if name.endswith(".mm") else ["-Wno-deprecated-declarations"]
            run(["xcrun", "clang++", *common, *extra, "-c", bridge / name, "-o", obj])
            objects.append(obj)
        canvas = (source / "FastTurboApp/FastTurboCanvas.swift").read_text()
        old = "device.makeDefaultLibrary()"
        if canvas.count(old) != 1:
            raise RuntimeError("Reference renderer library lookup changed; review the port adaptation.")
        canvas = "// Port adaptation: load the packaged metallib instead of the main app bundle.\n" + canvas.replace(old, "FastTurboResources.library(device: device)")
        adapted = build_dir / "FastTurboCanvas.swift"
        adapted.write_text(canvas)
        swift = sorted((source / "FastTurboCore").glob("*.swift"))
        swift += [source / "FastTurboApp/PresentationTypes.swift", source / "FastTurboApp/SimulationController.swift", adapted]
        swift += sorted((PROJECT / "native").glob("*.swift"))
        candidate = build_dir / "libfastturbo.dylib"
        run(["xcrun", "swiftc", "-swift-version", "5", "-O", "-whole-module-optimization", "-g",
             "-target", "arm64-apple-macosx15.0", "-sdk", sdk,
             "-module-cache-path", build_dir / "modules", "-module-name", "FastTurboPython",
             "-emit-module-path", build_dir / "FastTurboPython.swiftmodule",
             "-import-objc-header", bridge / "FastTurbo-Bridging-Header.h", "-I", bridge,
             "-emit-library", *swift, *objects, "-lc++", "-framework", "Metal", "-framework", "MetalKit",
             "-framework", "AppKit", "-framework", "SwiftUI", "-Xlinker", "-install_name",
             "-Xlinker", "@rpath/libfastturbo.dylib", "-o", candidate])
        air = build_dir / "TurboRendering.air"
        run(["xcrun", "metal", "-target", "air64-apple-macos15.0", "-isysroot", sdk,
             "-fmetal-math-mode=fast", "-fmetal-math-fp32-functions=fast",
             "-c", source / "FastTurboApp/TurboRendering.metal", "-o", air])
        metal_library = build_dir / "default.metallib"
        run(["xcrun", "metallib", air, "-o", metal_library])
        run(["codesign", "--force", "--sign", "-", candidate], capture_output=True)
        # Publish only after all compilation succeeds. Rename preserves loaded dylibs.
        for src, dst in [(candidate, library), (metal_library, NATIVE / "default.metallib")]:
            temp = dst.with_suffix(dst.suffix + ".tmp")
            shutil.copy2(src, temp)
            os.replace(temp, dst)
        shutil.copy2(REFERENCE / "Resources/ThirdPartyNotices.txt", NATIVE / "ThirdPartyNotices.txt")
        shutil.copy2(REFERENCE / "ThirdParty/VkFFTSource/LICENSE", NATIVE / "VkFFT-LICENSE.txt")
        shutil.copy2(REFERENCE / "ThirdParty/VkFFTSource/metal-cpp/LICENSE.txt", NATIVE / "metal-cpp-LICENSE.txt")
        stamp.write_text(json.dumps(manifest, indent=2) + "\n")
    return library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(build(args.force))


if __name__ == "__main__":
    main()
