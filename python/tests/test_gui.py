from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.gui
def test_actual_gui_controls(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/check_gui.py"
    subprocess.run([sys.executable, str(script), "--output", str(tmp_path / "gui.json")], check=True, timeout=180)


@pytest.mark.gui
@pytest.mark.gpu
@pytest.mark.parametrize("close_method", ["window", pytest.param("menu", marks=pytest.mark.skipif(
    sys.platform == "win32", reason="Windows Close Window hides to the notification area"))])
def test_closing_window_exits_running_application(tmp_path, close_method):
    # A subprocess must leave the real event loop and release the live native UI.
    # Checking visibility alone would miss a hidden GPU worker regression.
    script = """
import sys
import time
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtTest import QTest
from fastturbo.app import MainWindow, create_application
from fastturbo.engine import NativeError

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, sys.argv[2])
app = create_application()
window = MainWindow(config={'resolution': 128}, canvas_size=320, restore=False)
app.aboutToQuit.connect(window.shutdown)
window.show()
try:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        QTest.qWait(10)
        state = window.native.call()
        assert not state['error_message'], state['error_message']
        if state['ready'] and state['presentations'] > 3 and state['stats']['steps'] > 20:
            break
    else:
        raise AssertionError('No active GPU simulation before closing')
    close = window.close if sys.argv[1] == 'window' else window.close_action.trigger
    QTimer.singleShot(0, close)
    QTimer.singleShot(5000, lambda: app.exit(1))
    assert app.exec() == 0, 'Closing left the application event loop running'
    assert window._closing
    assert not window.isVisible()
    assert not window.poll_timer.isActive()
    try:
        window.native.call()
    except NativeError as error:
        assert str(error) == 'UI is closed'
    else:
        raise AssertionError('Closing left the native GPU controller alive')
finally:
    window.shutdown()
"""
    subprocess.run([sys.executable, "-c", script, close_method, str(tmp_path / "settings")],
                   check=True, timeout=90)
