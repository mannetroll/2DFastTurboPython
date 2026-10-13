"""Freeze an installed release wheel, preserving CUDA and Qt runtime resources."""
from pathlib import Path
import fastturbo
from PyInstaller.utils.hooks import copy_metadata

project = Path(SPECPATH).parent
package = Path(fastturbo.__file__).parent
native = package / "_native"
binaries = [(str(p), "fastturbo/_native") for p in native.glob("*.so*")]
# NVRTC can also be collected at the bundle root by dependency analysis. Its
# dlopened builtins must remain beside that copy as well as the native engine.
binaries += [(str(p), ".") for p in native.glob("libnvrtc-builtins.so.*")]
binaries += [(str(p), "fastturbo.libs") for p in (package.parent / "fastturbo.libs").glob("*.so*")]
datas = [(str(p), "fastturbo/_native") for p in native.iterdir() if ".so" not in p.name]
datas += [(str(package / "assets"), "fastturbo/assets"), *copy_metadata("fastturbo")]

a = Analysis([str(project / "scripts/bundle_entry.py")], binaries=binaries, datas=datas,
             excludes=["numpy", "scipy", "pytest"])
# CUDA/OpenGL interop must use the host's NVIDIA driver and GL dispatch stack.
host_libraries = ("libcuda.so", "libnvidia-", "libGL.so", "libGLX", "libOpenGL",
                  "libGLdispatch", "libEGL")
a.binaries = [entry for entry in a.binaries
              if not Path(entry[0]).name.startswith(host_libraries)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="2dfastturbo", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="2DFastTurbo-Python")
