from pathlib import Path
import os
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "reference-macos/2DFastTurbo/Scripts"))


@pytest.fixture(scope="session")
def swift_binary():
    path = Path(os.environ.get("FASTTURBO_SWIFT_BINARY", "/private/tmp/FastTurboPythonReference/Build/Products/Release/2DFastTurbo.app/Contents/MacOS/2DFastTurbo"))
    if not path.is_file():
        pytest.fail("Build the reference first: uv run python scripts/build_reference.py")
    return path
