from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.gui
def test_actual_gui_controls(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/check_gui.py"
    subprocess.run([sys.executable, str(script), "--output", str(tmp_path / "gui.json")], check=True, timeout=180)
