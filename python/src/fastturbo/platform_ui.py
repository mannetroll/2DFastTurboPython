"""Small platform presentation/lifecycle adaptations for the shared window."""
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


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
