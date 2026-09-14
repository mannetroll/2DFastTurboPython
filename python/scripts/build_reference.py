"""Build the authoritative Xcode app without regenerating or modifying its project."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
subprocess.run(["xcodebuild", "-project", str(root / "reference-macos/2DFastTurbo/2DFastTurbo.xcodeproj"),
    "-scheme", "2DFastTurbo", "-configuration", "Release", "-destination", "platform=macOS,arch=arm64",
    "-derivedDataPath", "/private/tmp/FastTurboPythonReference", "ARCHS=arm64", "ONLY_ACTIVE_ARCH=YES", "build"], check=True)
