import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import queue
import subprocess
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

HERE = Path(__file__).resolve().parent
REF = HERE.parents[1]
VIDEO = HERE / "2DFastTurbo-Windows-GUI-Test-raw.mp4"
FPS = 30
user = C.WinDLL("user32", use_last_error=True)
dwm = C.WinDLL("dwmapi")
user.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
user.SetProcessDpiAwarenessContext(C.c_void_p(-4))
user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
user.GetCursorPos.argtypes = [C.POINTER(W.POINT)]
user.SetCursorPos.argtypes = [C.c_int, C.c_int]
user.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
user.SetForegroundWindow.argtypes = [W.HWND]
user.GetForegroundWindow.restype = W.HWND
user.AttachThreadInput.argtypes = [W.DWORD, W.DWORD, W.BOOL]
user.IsIconic.argtypes = [W.HWND]
user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user.GetDpiForWindow.argtypes = [W.HWND]
user.IsWindowVisible.argtypes = [W.HWND]
user.mouse_event.argtypes = [W.DWORD, W.DWORD, W.DWORD, W.DWORD, C.c_size_t]
dwm.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
ENUM = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user.EnumWindows.argtypes = [ENUM, W.LPARAM]


def window_for(pid):
    found = []
    @ENUM
    def visit(hwnd, _):
        owner = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, C.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True
    user.EnumWindows(visit, 0)
    return found[0] if found else None


def bounds(hwnd):
    rect = W.RECT()
    user.GetWindowRect(hwnd, C.byref(rect))
    return rect


def place(hwnd, x, y):
    assert user.SetWindowPos(hwnd, W.HWND(-1), x, y, 0, 0, 0x41)
    focus(hwnd)


def focus(hwnd):
    current = C.windll.kernel32.GetCurrentThreadId()
    active = user.GetWindowThreadProcessId(user.GetForegroundWindow(), None)
    attached = active != current and user.AttachThreadInput(current, active, True)
    try:
        user.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user.AttachThreadInput(current, active, False)


LABELS = {
    "visible_first_frame": "Live simulation at N = 2048",
    "desktop_menu_transport_and_state": "Simulation menu: pause and resume",
    "about_equation_embedded_and_rendered": "About: embedded Navier–Stokes equation",
    "live_cfl_reynolds_sliders": "Change CFL and Reynolds number",
    "field_12": "Display mode: enstrophy",
    "field_13": "Display mode: vorticity",
    "field_14": "Display mode: speed",
    "palette_15": "Palette: Inferno",
    "palette_16": "Palette: Ember",
    "palette_17": "Palette: Aurora",
    "palette_18": "Palette: Glacier",
    "expanded_resolution_menu": "Resolution menu: select N = 3072",
    "non_power_of_two_resolution": "Verify N = 3072 / padded M = 4608",
    "method_menu_0": "Integrator: IMEX RK3",
    "method_menu_1": "Integrator: integrating-factor RK3",
    "method_menu_2": "Integrator: ETD RK4",
    "native_save_png_dialog": "Save a PNG snapshot",
    "close_to_tray": "Ctrl+W: hide to the notification area",
    "reopen_same_state": "Reopen with the same simulation state",
}

app = QApplication([])
screen = app.primaryScreen()
dpr = screen.devicePixelRatio()
available = screen.availableGeometry()
stage = QWidget(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
stage.setAttribute(Qt.WA_ShowWithoutActivating)
stage.setStyleSheet("background: #101722;")
stage.setGeometry(available)
stage.show()
app.processEvents()
messages = queue.Queue()
process = encoder = None
hwnd = None
log_file = (HERE / "recording.log").open("w", encoding="utf-8")
encoder_log = (HERE / "encoder.log").open("w", encoding="utf-8")
events = []
try:
    process = subprocess.Popen([
        str(HERE / "build/2DFastTurbo-video.exe"), "--live-check", "--resolution", "2048",
        "--canvas-size", "680", "--output", str(HERE / "gui-check.json"),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")

    def read_log():
        for line in process.stdout:
            log_file.write(line)
            log_file.flush()
            messages.put(line.strip())
    thread = threading.Thread(target=read_log, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not hwnd:
        app.processEvents()
        hwnd = window_for(process.pid)
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("GUI did not open")
        time.sleep(.02)
    # Wait for initialize() to size the client area.
    until = time.monotonic() + .6
    while time.monotonic() < until:
        app.processEvents()
        time.sleep(.02)
    rect = bounds(hwnd)
    sw, sh = int(available.width() * dpr), int(available.height() * dpr)
    ww, wh = rect.right - rect.left, rect.bottom - rect.top
    x = int(available.x() * dpr) + max(12, (sw - ww) // 2)
    y = int(available.y() * dpr) + max(12, (sh - wh) // 2)
    place(hwnd, x, y)
    rect = bounds(hwnd)
    left, top = rect.left - 12, rect.top - 12
    capture_w, capture_h = ww + 24, wh + 24
    width = (capture_w + 1) // 2 * 2
    footer = 92
    height = (capture_h + footer + 1) // 2 * 2
    ffmpeg = next((HERE / "tools/imageio_ffmpeg/binaries").glob("ffmpeg*.exe"))
    encoder = subprocess.Popen([
        str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{width}x{height}",
        "-framerate", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
        "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(VIDEO),
    ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encoder_log)
    print(f"Recording {width}x{height} at {FPS} fps", flush=True)
    started = time.monotonic()
    frames = passed = 0
    pointer = None
    phase = "Live simulation at N = 2048"
    action_at = started - 10
    last_progress = started

    def capture(label, progress, pulse=False):
        image = QImage(width, height, QImage.Format_RGBA8888)
        image.fill(QColor("#101722"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        desktop = screen.grabWindow(0).toImage()
        screen_origin = screen.geometry().topLeft()
        crop = desktop.copy(left - round(screen_origin.x() * dpr),
                            top - round(screen_origin.y() * dpr), capture_w, capture_h)
        painter.drawImage(0, 0, crop)
        px, py = (pointer[0] - left, pointer[1] - top) if pointer else (-100, -100)
        if 0 <= px < capture_w and 0 <= py < capture_h:
            if pulse:
                painter.setPen(QPen(QColor(95, 230, 194, 220), 3))
                painter.setBrush(QColor(95, 230, 194, 45))
                painter.drawEllipse(QPointF(px, py), 18, 18)
            painter.setPen(QPen(QColor("#17212a"), 1.3))
            painter.setBrush(QColor("#ffffff"))
            painter.drawPolygon(QPolygonF([QPointF(px, py), QPointF(px + 3, py + 21),
                QPointF(px + 8, py + 15), QPointF(px + 15, py + 14)]))
        painter.fillRect(0, capture_h, width, footer + 2, QColor("#101722"))
        painter.setPen(QColor("#6be8c5"))
        painter.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        painter.drawText(QRectF(24, capture_h + 10, width - 48, 24), "2DFastTurbo  |  Windows GUI test sequence")
        painter.setFont(QFont("Segoe UI", 14))
        painter.setPen(QColor("#f1f4f8"))
        painter.drawText(QRectF(24, capture_h + 37, width - 150, 30), label)
        painter.setFont(QFont("Consolas", 11))
        painter.setPen(QColor("#9aaaba"))
        painter.drawText(QRectF(width - 124, capture_h + 39, 100, 24), Qt.AlignRight, progress)
        painter.end()
        return bytes(image.constBits())

    def write_frame(label, progress, pulse=False):
        global frames
        now = time.monotonic()
        if now < started + frames / FPS:
            time.sleep(max(0, started + frames / FPS - now))
        app.processEvents()
        data = capture(label, progress, pulse)
        encoder.stdin.write(data)
        frames += 1
        # Keep the movie's clock aligned if capture briefly misses a deadline.
        due = int((time.monotonic() - started) * FPS)
        while frames < due:
            encoder.stdin.write(data)
            frames += 1

    while process.poll() is None:
        now = time.monotonic()
        owner = W.DWORD()
        user.GetWindowThreadProcessId(user.GetForegroundWindow(), C.byref(owner))
        if owner.value != process.pid and user.IsWindowVisible(hwnd) and not user.IsIconic(hwnd):
            focus(hwnd)
        if now - started > 180:
            raise RuntimeError("GUI check timed out")
        while not messages.empty():
            line = messages.get_nowait()
            if line.startswith("POINTER "):
                _, px, py = line.split()
                pointer = (int(px), int(py))
            elif line.startswith("CHECK "):
                name = line.removeprefix("CHECK ")
                phase = LABELS.get(name, name.replace("_", " ").capitalize())
                action_at = now
                events.append({"seconds": now - started, "check": name, "caption": phase})
            elif line.startswith("PASS GUI "):
                passed += 1
            elif "GUI:" in line or "FAIL" in line:
                raise RuntimeError(line)
        write_frame(phase, f"{passed}/67 passed", now - action_at < .5)
        if now - last_progress > 12:
            print(f"{passed}/67 checks: {phase}", flush=True)
            last_progress = now
    assert process.wait() == 0, "GUI test failed"
    thread.join(timeout=2)
    report = json.loads((HERE / "gui-check.json").read_text())
    assert report["passed"] and len(report["checks"]) == 67, report
    print("67/67 checks passed; recording title-bar close", flush=True)

    # Show the GiB allocation display, then the shipped build's real X-button behavior.
    process = subprocess.Popen([str(REF / "dist/2DFastTurbo.exe"), "--resolution", "4096", "--canvas-size", "680"])
    hwnd = None
    until = time.monotonic() + 20
    while not hwnd:
        write_frame("All 67 GUI checks passed", "PASS")
        hwnd = window_for(process.pid)
        if time.monotonic() > until:
            raise RuntimeError("Final app did not open")
    place(hwnd, x, y)
    until = time.monotonic() + 2.5
    while time.monotonic() < until:
        write_frame("GPU allocation in GiB; X quits the application", "67/67 passed")
    r = W.RECT()
    dwm.DwmGetWindowAttribute(hwnd, 9, C.byref(r), C.sizeof(r))
    dpi = user.GetDpiForWindow(hwnd) / 96
    pointer = (r.right - int(23 * dpi), r.top + int(15 * dpi))
    until = time.monotonic() + .4
    while time.monotonic() < until:
        write_frame("Title-bar X: quit", "67/67 passed", True)
    assert user.PostMessageW(hwnd, 0x112, 0xF060, 0)
    until = time.monotonic() + 10
    while process.poll() is None:
        write_frame("Title-bar X: quit", "67/67 passed", True)
        if time.monotonic() > until:
            raise RuntimeError("The close-button click did not exit")
    assert process.wait() == 0
    until = time.monotonic() + 1.2
    while time.monotonic() < until:
        write_frame("All checks passed — application exited cleanly", "PASS")
    encoder.stdin.close()
    assert encoder.wait(timeout=60) == 0, "Video encoder failed"
    info = {"video": str(VIDEO), "width": width, "height": height, "fps": FPS,
            "frames": frames, "seconds": frames / FPS, "gui_checks_passed": 67,
            "title_bar_x_quit_passed": True,
            "recording": "Existing GUI checks paced at 0.14 seconds; original solver and rendering; shipped EXE for final X-button check",
            "events": events}
    (HERE / "recording.json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps({k: v for k, v in info.items() if k != "events"}), flush=True)
finally:
    if process is not None and process.poll() is None:
        if hwnd:
            user.PostMessageW(hwnd, 0x112, 0xF060, 0)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
    if encoder is not None and encoder.poll() is None:
        encoder.stdin.close()
        encoder.wait(timeout=30)
    stage.close()
    log_file.close()
    encoder_log.close()
