from pathlib import Path
import os
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "reference-macos/2DFastTurbo/Scripts"))


@pytest.fixture(scope="session")
def swift_binary():
    if sys.platform == "win32":
        path = Path(os.environ.get("FASTTURBO_WINDOWS_BINARY", ROOT / "python/build/windows/reference/2DFastTurbo.exe"))
        if not path.is_file():
            pytest.fail("Build the Windows backend and reference first: scripts/build-windows.ps1")
        # The reference executable uses the same locally staged runtime DLLs.
        os.environ["PATH"] = str(ROOT / "python/src/fastturbo/_native") + os.pathsep + os.environ["PATH"]
        return path
    path = Path(os.environ.get("FASTTURBO_SWIFT_BINARY", "/private/tmp/FastTurboPythonReference/Build/Products/Release/2DFastTurbo.app/Contents/MacOS/2DFastTurbo"))
    if not path.is_file():
        pytest.fail("Build the reference first: uv run python scripts/build_reference.py")
    return path
