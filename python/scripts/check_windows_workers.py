"""Verify autonomous native work while the Python/Qt main thread is idle."""
import argparse
import ctypes
import json
from pathlib import Path
import threading
import time
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from fastturbo.app import MainWindow, create_application

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
app = create_application()
window = MainWindow(config={"resolution": 128}, regulated=False, restore=False)
window.show()
try:
    deadline = time.monotonic()+60
    while True:
        app.processEvents()
        state = window.native.call()
        assert not state["error_message"], state["error_message"]
        if state["ready"] and state["presentations"] > 3 and state["stats"]["steps"] > 0:
            break
        assert time.monotonic() < deadline
        QTest.qWait(10)
    before = window.native.call()
    time.sleep(.6)  # Intentionally no Qt events and no native/Python calls.
    after = window.native.call()
    assert after["stats"]["steps"] > before["stats"]["steps"]
    assert after["presentations"] > before["presentations"]+5
    # Polling transmits only scalar metadata/history; physical frames stay native.
    assert len(json.dumps(after)) < 20000
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Capture the composited desktop: HWND BitBlt cannot capture a DXGI flip
    # surface reliably. This also checks actual on-screen output, not just PNG.
    window.raise_()
    window.activateWindow()
    user32 = ctypes.WinDLL("user32")
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_bool
    # Keep automation captures above editor/terminal windows without recreating
    # Qt's HWND (changing Qt window flags here would invalidate the native host).
    assert user32.SetWindowPos(int(window.winId()), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x13)
    QTest.qWait(150)
    origin = window.canvas.mapToGlobal(QPoint(0, 0))
    capture = window.screen().grabWindow(0, origin.x(), origin.y(), window.canvas.width(), window.canvas.height())
    capture.save(str(args.output.with_suffix(".png")))
    pixels = capture.toImage()
    colors = {pixels.pixelColor(x, y).rgb() for y in range(60, pixels.height()-60, 11)
              for x in range(60, pixels.width()-60, 11)}
    assert len(colors) > 100, f"Visible native canvas has only {len(colors)} colors"
    # Exercise the embedded native transport buttons through their Win32 input
    # path as well as the shared Qt menu/keyboard tests.
    user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowExW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    child = user32.FindWindowExW(int(window.canvas.winId()), None, "FastTurboPythonCanvas", None)
    assert child

    def wait_for(predicate):
        until = time.monotonic()+10
        while time.monotonic()<until:
            app.processEvents()
            state = window.native.call()
            assert not state["error_message"], state["error_message"]
            if predicate(state):
                return state
            QTest.qWait(10)
        raise AssertionError("Native transport did not settle")

    def click_transport(index):
        scale = window.devicePixelRatioF()
        x, y = int((33+38*index)*scale), int((window.canvas.height()-31)*scale)
        user32.SendMessageW(child, 0x202, 0, (y<<16)|x)  # WM_LBUTTONUP

    click_transport(0)
    paused = wait_for(lambda s: s["ready"] and s["settled"] and not s["running"])
    sequence = paused["stats"]["steps"]
    assert window.native.call("configure", **paused["config"])["generation"] == paused["generation"]
    click_transport(1)
    wait_for(lambda s: s["settled"] and s["stats"]["steps"] == sequence+1)
    click_transport(2)
    wait_for(lambda s: s["ready"] and s["settled"] and s["stats"]["steps"] == 0)
    if window.tray:
        window.close()
        assert not window.isVisible() and window.tray.isVisible()
        window.tray.contextMenu().actions()[0].trigger()
        assert window.isVisible()
    # Cancel only this process's main-thread Save dialog; exports themselves are
    # validated against the shader equations in the GPU tests.
    main_thread = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
    user32.EnumThreadWindows.argtypes = [ctypes.c_uint, callback_type, ctypes.c_ssize_t]
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    cancelled = threading.Event()

    @callback_type
    def cancel_save_dialog(hwnd, unused):
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, name, len(name))
        if name.value == "#32770":
            user32.PostMessageW(hwnd, 0x10, 0, 0)  # WM_CLOSE
            cancelled.set()
        return True

    def dismiss_dialog():
        deadline = time.monotonic()+10
        while not cancelled.is_set() and time.monotonic()<deadline:
            user32.EnumThreadWindows(main_thread, cancel_save_dialog, 0)
            time.sleep(.02)

    closer = threading.Thread(target=dismiss_dialog, daemon=True)
    closer.start()
    window.native.call("save")
    closer.join(timeout=1)
    assert cancelled.is_set(), "Native Save dialog was not observed"
    args.output.write_text(json.dumps({"passed": True,
        "native_save_dialog_cancel": True,
        "native_transport_buttons": True,
        "unchanged_configuration_retains_generation": True,
        "tray_reopen": bool(window.tray),
        "visible_canvas_sample_colors": len(colors),
        "steps_without_python_events": after["stats"]["steps"]-before["stats"]["steps"],
        "presentations_without_python_events": after["presentations"]-before["presentations"]}, indent=2)+"\n")
finally:
    window.shutdown()
