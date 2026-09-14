"""Freeze a self-contained, locally signed arm64 macOS app."""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import sys
from fastturbo.build import NATIVE, PROJECT, REFERENCE, build

build()
output = PROJECT / "dist"
work = PROJECT / "build/pyinstaller"
work.mkdir(parents=True, exist_ok=True)
iconset = work / "FastTurbo.iconset"
iconset.mkdir(exist_ok=True)
for path in (REFERENCE / "Resources/Assets.xcassets/AppIcon.appiconset").glob("*.png"):
    shutil.copy2(path, iconset / path.name.replace("@1x", ""))
icon = work / "FastTurbo.icns"
subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icon)], check=True)
# All ctypes-loaded binaries are declared explicitly so dependency collection sees them.
command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
    "--target-arch", "arm64", "--name", "2DFastTurbo Python", "--osx-bundle-identifier", "com.mannetroll.fastturbo.python",
    "--icon", str(icon), "--distpath", str(output), "--workpath", str(work), "--specpath", str(work),
    "--paths", str(PROJECT / "src"), "--add-binary", f"{NATIVE / 'libfastturbo.dylib'}:fastturbo/_native"]
for path in NATIVE.iterdir():
    if path.suffix != ".dylib":
        command += ["--add-data", f"{path}:fastturbo/_native"]
command += [str(PROJECT / "scripts/bundle_entry.py")]
env = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(work / "cache"), "MACOSX_DEPLOYMENT_TARGET": "15.0"}
subprocess.run(command, check=True, env=env)
app = output / "2DFastTurbo Python.app"
info_path = app / "Contents/Info.plist"
info = plistlib.loads(info_path.read_bytes())
info.update(LSMinimumSystemVersion="15.0", CFBundleShortVersionString="0.1.0", CFBundleVersion="1")
info_path.write_bytes(plistlib.dumps(info))
subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
# PyInstaller signs collected binaries. Verify the full bundle after assembly.
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
print(app)
