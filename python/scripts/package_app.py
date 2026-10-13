"""Freeze a self-contained, locally signed arm64 macOS app and installer DMG."""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
from fastturbo.build import NATIVE, PROJECT, REFERENCE, build

build()
version = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]["version"]
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
    "--paths", str(PROJECT / "src"), "--add-binary", f"{NATIVE / 'libfastturbo.dylib'}:fastturbo/_native",
    "--add-data", f"{PROJECT / 'src/fastturbo/assets/fastturbo.png'}:fastturbo/assets",
    "--add-data", f"{PROJECT / 'src/fastturbo/assets/navier-stokes.png'}:fastturbo/assets",
    "--add-data", f"{PROJECT / 'src/fastturbo/assets/header-hurricane.svg'}:fastturbo/assets",
    "--add-data", f"{PROJECT / 'LICENSE'}:licenses",
    "--add-data", f"{PROJECT / 'THIRD_PARTY_NOTICES.md'}:licenses",
    "--exclude-module", "numpy", "--exclude-module", "scipy", "--exclude-module", "pytest"]
for path in NATIVE.iterdir():
    if path.suffix != ".dylib":
        command += ["--add-data", f"{path}:fastturbo/_native"]
command += [str(PROJECT / "scripts/bundle_entry.py")]
env = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(work / "cache"), "MACOSX_DEPLOYMENT_TARGET": "15.0"}
subprocess.run(command, check=True, env=env)
app = output / "2DFastTurbo Python.app"
info_path = app / "Contents/Info.plist"
info = plistlib.loads(info_path.read_bytes())
info.update(LSMinimumSystemVersion="15.0", CFBundleShortVersionString=version, CFBundleVersion=version,
            CFBundleName="2DFastTurbo", CFBundleDisplayName="2DFastTurbo")
info_path.write_bytes(plistlib.dumps(info))
subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
# PyInstaller signs collected binaries. Verify the full bundle after assembly.
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
dmg = output / f"2DFastTurbo-Python-{version}-macOS-arm64.dmg"
with tempfile.TemporaryDirectory(prefix="dmg-", dir=work) as directory:
    staging = Path(directory)
    subprocess.run(["ditto", str(app), str(staging / app.name)], check=True)
    (staging / "Applications").symlink_to("/Applications")
    (staging / "README.txt").write_text(
        f"2DFastTurbo Python {version} for macOS\n\n"
        "Drag 2DFastTurbo Python.app to Applications, then open it.\n"
        "Requires macOS 15 or later and an Apple Silicon Mac.\n"
        "Python, Qt, the Metal engine and shaders are included.\n"
        "The app is ad-hoc signed, not Apple-notarized. For first-launch help:\n"
        "https://support.apple.com/en-us/102445\n", encoding="utf-8")
    subprocess.run(["hdiutil", "create", "-ov", "-format", "UDZO", "-volname",
                    f"2DFastTurbo {version}", "-srcfolder", str(staging), str(dmg)], check=True)
subprocess.run(["hdiutil", "verify", str(dmg)], check=True)
print(dmg)
