"""Small platform presentation/lifecycle adaptations for the shared window."""
import os
import sys
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon


def prepare_application_name(name):
    """Set macOS's in-memory bundle name before Qt creates the native menus."""
    if sys.platform == "linux":
        import ctypes
        # Initialize Xlib threading before Qt or the native rendering worker
        # opens a connection. This module is never imported by headless runs.
        global _x11
        _x11 = ctypes.CDLL("libX11.so.6")
        if not _x11.XInitThreads():
            raise RuntimeError("X11 thread initialization failed.")
        # The native GLX canvas embeds an X11 child. Xwayland also provides this
        # contract on Wayland desktops; a wl_surface is not an X11 Window ID.
        if os.environ.get("QT_QPA_PLATFORM", "xcb") != "xcb":
            raise RuntimeError("The Linux GPU canvas requires QT_QPA_PLATFORM=xcb and an X11/Xwayland display.")
        existing = QApplication.instance()
        if existing is not None and existing.platformName() != "xcb":
            raise RuntimeError("Create the Linux QApplication with QT_QPA_PLATFORM=xcb before attaching the GPU canvas.")
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    if sys.platform != "darwin":
        return
    import ctypes as C
    cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    signatures = {
        "CFBundleGetMainBundle": (C.c_void_p, []),
        "CFBundleGetInfoDictionary": (C.c_void_p, [C.c_void_p]),
        "CFStringCreateWithCString": (C.c_void_p, [C.c_void_p, C.c_char_p, C.c_uint32]),
        "CFDictionarySetValue": (None, [C.c_void_p, C.c_void_p, C.c_void_p]),
        "CFRelease": (None, [C.c_void_p]),
    }
    for symbol, (result, arguments) in signatures.items():
        function = getattr(cf, symbol)
        function.restype = result
        function.argtypes = arguments
    info = cf.CFBundleGetInfoDictionary(cf.CFBundleGetMainBundle())
    if not info:
        return

    def string(value):
        result = cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100)
        if not result:
            raise MemoryError("Could not create the macOS application name")
        return result

    title = string(name)
    try:
        # Only the current process's cached dictionary changes; no plist is written.
        for key in ("CFBundleName", "CFBundleDisplayName"):
            property_name = string(key)
            try:
                cf.CFDictionarySetValue(info, property_name, title)
            finally:
                cf.CFRelease(property_name)
    finally:
        cf.CFRelease(title)


def fonts():
    if sys.platform == "darwin":
        return ".AppleSystemUIFont", "Menlo"
    available = set(QFontDatabase.families())
    regular = "Segoe UI" if "Segoe UI" in available else QApplication.font().family()
    mono = "Consolas" if "Consolas" in available else QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return regular, mono


def save_snapshot(window):
    """Linux uses the existing shared Save action with Qt's native file dialog."""
    if sys.platform != "linux":
        return False
    filename, _ = QFileDialog.getSaveFileName(window, "Save Snapshot", "2DFastTurbo.png", "PNG image (*.png)")
    if filename:
        if not filename.lower().endswith(".png"):
            filename += ".png"
        window.native.call("export", png=filename)
    return True


def sync_canvas_scale(window):
    if sys.platform == "linux":
        window.native.call("set", canvas_scale=window.canvas.devicePixelRatioF())


def install_lifecycle(window):
    # macOS already has Dock reopen. Windows needs a way back to a hidden run.
    if sys.platform != "win32" or not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    icon = QApplication.instance().windowIcon()
    window.setWindowIcon(icon)
    tray = QSystemTrayIcon(icon, window)
    tray.setToolTip("2DFastTurbo — simulation continues while hidden")
    menu = QMenu(window)
    menu.addAction("Show Simulation", window.reopen)
    menu.addAction("Pause / Resume", lambda: window.send("toggle"))
    menu.addSeparator()
    menu.addAction("Quit", QApplication.instance().quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: window.reopen() if reason in
                           (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
    tray.show()
    return tray
