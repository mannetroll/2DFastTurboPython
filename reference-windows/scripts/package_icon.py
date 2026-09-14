"""Wrap the unchanged reference PNG in a Windows icon container."""
from pathlib import Path
import struct
root = Path(__file__).resolve().parents[2]
source = root/'reference-macos/2DFastTurbo/Resources/Assets.xcassets/AppIcon.appiconset/icon_128x128@1x.png'
image = source.read_bytes()
target = root/'windows/assets/2DFastTurbo.ico'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(struct.pack('<HHH', 0, 1, 1) +
                   struct.pack('<BBBBHHII', 128, 128, 0, 0, 1, 32, len(image), 22) + image)
