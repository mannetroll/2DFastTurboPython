"""Small platform presentation/lifecycle adaptations for the shared window."""
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


def prepare_application_name(name):
    """Set macOS's in-memory bundle name before Qt creates the native menus."""
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


def install_lifecycle(window):
    # macOS already has Dock reopen. Windows needs a way back to a hidden run.
    if sys.platform != "win32" or not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#70e8d1"), 2))
    painter.setBrush(QColor("#10141c"))
    painter.drawEllipse(3, 3, 26, 26)
    painter.drawEllipse(8, 8, 16, 16)
    painter.end()
    icon = QIcon(pixmap)
    window.setWindowIcon(icon)
    tray = QSystemTrayIcon(icon, window)
    tray.setToolTip("2DFastTurbo Python — simulation continues while hidden")
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
