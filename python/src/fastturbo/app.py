"""Shared PySide controls around the platform's native GPU canvas."""
from __future__ import annotations
import math
from pathlib import Path
import sys
import time
from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap, QRegion
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget)

from .controls import FIELDS, METHODS, PALETTES, RESOLUTIONS, WAVENUMBERS, FloatSlider
from .engine import Configuration, NativeUI
from .environment import environment_summary
from .platform_ui import fonts, install_lifecycle, prepare_application_name, save_snapshot, sync_canvas_scale

STYLE = """
QMainWindow, QWidget#root { background: #090c13; color: #e6e8ec; }
QWidget { color: #e6e8ec; font-family: '.AppleSystemUIFont'; font-size: 11px; }
QFrame#panel, QFrame#metrics { background: #10141c; border: 1px solid #242832; border-radius: 12px; }
QLabel { background: transparent; border: none; }
QLabel#secondary { color: #7a8ca6; font-family: 'Menlo'; font-size: 9px; }
QLabel#accent { color: #70e8d1; font-family: 'Menlo'; }
QLabel#title { font-size: 16px; font-weight: 600; letter-spacing: 2px; }
QLabel#metricValue { font-family: 'Menlo'; font-size: 16px; font-weight: 300; }
QPushButton { background: #171c26; border: 1px solid #282e3b; border-radius: 6px; padding: 5px 8px; text-align: left; }
QPushButton:hover { background: #222b37; }
QPushButton:checked { background: #1c2930; border: 1px solid #70e8d1; color: #70e8d1; }
QPushButton:disabled { color: #556070; }
QComboBox, QLineEdit { background: #1b212c; color: #e6e8ec; border: 1px solid #303747; border-radius: 4px; padding: 3px; }
QComboBox { padding-right: 15px; }
QComboBox QAbstractItemView { background: #1b212c; color: #e6e8ec; selection-background-color: #304746; }
QSlider::groove:horizontal { background: #354050; height: 3px; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #70e8d1; border-radius: 1px; }
QSlider::handle:horizontal { background: #e0eee9; width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; }
QSlider::handle:horizontal:disabled { background: #64736e; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 5px; }
QScrollBar::handle:vertical { background: #384456; border-radius: 2px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QMenuBar, QMenu { background: #10141c; color: #e6e8ec; }
QMenuBar::item:selected, QMenu::item:selected { background: #304746; }
QCheckBox { spacing: 5px; font-size: 10px; }
QCheckBox::indicator { width: 23px; height: 12px; border-radius: 6px; background: #344051; }
QCheckBox::indicator:checked { background: #70e8d1; }
"""


def label(text, name=None):
    value = QLabel(text)
    if name:
        value.setObjectName(name)
    return value


def blocked(widget, setter, value):
    widget.blockSignals(True)
    setter(value)
    widget.blockSignals(False)


class Sparkline(QWidget):
    def __init__(self):
        super().__init__()
        self.samples = []
        self.setFixedSize(26, 16)
        self.setAccessibleName("Kinetic energy history")

    def paintEvent(self, _):
        if len(self.samples) < 2:
            return
        low, high = min(self.samples), max(self.samples)
        spread = max(high - low, max(abs(high), 1e-12) * 0.02)
        path = QPainterPath()
        for i, value in enumerate(self.samples):
            x = i / (len(self.samples) - 1) * self.width()
            y = self.height() / 2 - (value - (high + low) / 2) / spread * self.height() * 0.85
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#70e8d1"), 1.1))
        painter.drawPath(path)


class EnvironmentLabel(QLabel):
    def __init__(self, info):
        summary, details = environment_summary(info)
        super().__init__(summary)
        self.summary = summary
        self.setObjectName("secondary")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(details)
        self.setAccessibleName("Runtime environment")
        self.setAccessibleDescription(details)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self.summary, Qt.TextElideMode.ElideRight, self.contentsRect().width()))


class ColorLegend(QWidget):
    def __init__(self):
        super().__init__()
        self._display = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.low = label("−ω", "secondary")
        self.gradient = QFrame()
        self.gradient.setFixedSize(54, 4)
        self.high = label("+ω", "secondary")
        for widget in (self.low, self.gradient, self.high):
            layout.addWidget(widget)
        self.set_display("vorticity", "ember")

    def set_display(self, field, palette):
        if self._display == (field, palette):
            return
        self._display = (field, palette)
        self.low.setText("−ω" if field == "vorticity" else "0")
        self.high.setText({"vorticity": "+ω", "enstrophy": "ω²", "speed": "|u|"}[field])
        a, b, c = PALETTES[palette]
        self.gradient.setStyleSheet(
            "QFrame { border: none; border-radius: 2px; background: "
            f"qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {a},stop:0.5 {b},stop:1 {c}); }}")
        self.setAccessibleName(f"{palette.title()} palette: {self.low.text()} to {self.high.text()}")


class EquationLabel(QLabel):
    """Draw the transparent equation with the dialog's current text color."""
    def __init__(self, parent=None):
        super().__init__("ρ(∂v/∂t + v·∇v) = −∇p + μ∇²v", parent)
        self.setAccessibleName("Navier–Stokes equation")
        self.setAccessibleDescription(self.text())
        self._equation = QPixmap(str(Path(__file__).with_name("assets") / "navier-stokes.png"))
        if not self._equation.isNull():
            self._equation = self._equation.copy(QRegion(self._equation.mask()).boundingRect())
            self.setFixedSize(320, round(320 * self._equation.height() / self._equation.width()))

    def paintEvent(self, event):
        if self._equation.isNull():
            return super().paintEvent(event)
        ratio = self.devicePixelRatioF()
        tinted = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        tinted.setDevicePixelRatio(ratio)
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self._equation)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.WindowText))
        painter.end()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, tinted)
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self, *, config=None, regulated=False, canvas_size=640, restore=True):
        super().__init__()
        config = {"resolution": 2048, "k0": 10, **(config or {})}
        if regulated or "reynolds" not in config:
            config["reynolds"] = Configuration.calibrated_reynolds(config["resolution"], config["k0"])
        self.native = NativeUI()
        self.state = {}
        self.poll_seconds = 0.0
        self.poll_calls = 0
        self._focused = False
        self._closing = False
        self._adjusting = False
        self._attached = False
        self._canvas_side = canvas_size
        self.setWindowTitle("2DFastTurbo")
        regular_font, self.mono_font = fonts()
        self.setStyleSheet(STYLE.replace(".AppleSystemUIFont", regular_font).replace("Menlo", self.mono_font))
        self.setMinimumSize(834, 761)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        self.layout = QVBoxLayout(root)
        self.layout.setContentsMargins(22, 0, 22, 12)
        self.layout.setSpacing(8)
        self._header()
        body = QHBoxLayout()
        body.setSpacing(18)
        self.layout.addLayout(body, 1)
        self.panel = QFrame()
        self.panel.setObjectName("panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        field_header = QWidget()
        field_header.setFixedHeight(34)
        field_row = QHBoxLayout(field_header)
        field_row.setContentsMargins(14, 0, 14, 0)
        self.field_title = label("●  VORTICITY", "accent")
        self.legend = ColorLegend()
        self.grid_title = label(f'{config["resolution"]}² · {3 * config["resolution"] // 2}² padded', "secondary")
        field_row.addWidget(self.field_title)
        field_row.addStretch()
        field_row.addWidget(self.legend)
        field_row.addSpacing(12)
        field_row.addWidget(self.grid_title)
        panel_layout.addWidget(field_header)
        self.canvas = QWidget()
        self.canvas.setAccessibleName("Decaying turbulence: Vorticity")
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        panel_layout.addWidget(self.canvas, 1)
        body.addWidget(self.panel, 1)
        self.inspector = self._inspector()
        body.addWidget(self.inspector)
        self._metrics()
        self._menus()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.hide()
        self.layout.addWidget(self.status)
        self.resize(canvas_size + 260, canvas_size + 195)
        if restore:
            geometry = QSettings().value("windowGeometry")
            if geometry:
                self.restoreGeometry(geometry)
        self.native.call("set", regulated=regulated)
        self.native.call("configure", **config)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(250)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self.application_state_changed)
        self.poll()
        self.tray = install_lifecycle(self)

    def _header(self):
        header = QWidget()
        header.setFixedHeight(77)
        row = QHBoxLayout(header)
        row.setContentsMargins(2, 0, 2, 0)
        logo = QSvgWidget(str(Path(__file__).with_name("assets") / "header-hurricane.svg"))
        logo.setFixedSize(42, 42)
        logo.setAccessibleName("Hurricane")
        row.addWidget(logo)
        row.addSpacing(7)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(label("2D FAST TURBO", "title"))
        self.environment = EnvironmentLabel(self.native.backend_info)
        titles.addWidget(self.environment)
        row.addLayout(titles, 1)
        self.backend = label("●  " + self.native.backend_info.get("backend", "METAL / VkFFT"), "accent")
        self.backend.setFixedHeight(28)
        self.backend.setStyleSheet("background:#122427; border-radius:12px; padding:8px; font-size:9px")
        row.addWidget(self.backend)
        self.focus_button = QPushButton("⛶")
        self.focus_button.setToolTip("Focus mode · F")
        self.focus_button.setAccessibleName("Focus mode")
        self.focus_button.clicked.connect(self.toggle_focus)
        row.addWidget(self.focus_button)
        self.save_button = QPushButton("↥")
        self.save_button.setToolTip("Save PNG · " + QKeySequence(QKeySequence.StandardKey.Save).toString(QKeySequence.SequenceFormat.NativeText))
        self.save_button.setAccessibleName("Save PNG")
        self.save_button.clicked.connect(lambda: self.send("save"))
        row.addWidget(self.save_button)
        self.layout.addWidget(header)

    def _combo(self, layout, title, choices, callback):
        row = QHBoxLayout()
        row.addWidget(label(title))
        combo = QComboBox()
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(4)
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        combo.setAccessibleName(title)
        for choice in choices:
            text, data = choice if isinstance(choice, tuple) else (str(choice), choice)
            combo.addItem(text, data)
        combo.currentIndexChanged.connect(lambda _: callback(combo.currentData()))
        row.addWidget(combo, 1)
        layout.addLayout(row)
        return combo

    def _inspector(self):
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(196)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 4, 0, 4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        self.k0 = self._combo(layout, "k₀", WAVENUMBERS, lambda v: self.send("configure", k0=v))
        row = QHBoxLayout()
        row.addWidget(label("Seed"))
        self.seed = QLineEdit("2")
        self.seed.setAccessibleName("Seed")
        self.seed.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.seed.returnPressed.connect(self.apply_seed)
        row.addWidget(self.seed, 1)
        dice = QPushButton("↻")
        dice.setFixedWidth(26)
        dice.setToolTip("New seed · " + QKeySequence("Ctrl+Shift+R").toString(QKeySequence.SequenceFormat.NativeText))
        dice.clicked.connect(lambda: self.send("seed"))
        row.addWidget(dice)
        layout.addLayout(row)
        self.resolution = self._combo(layout, "N", RESOLUTIONS, lambda v: self.send("configure", resolution=v))
        self.reynolds = FloatSlider("Re", 3, 6, math.log10(27898.36614974335), lambda v: f"{10**v:.0f}")
        self.reynolds.changed.connect(lambda v: self.send("set", refresh=False, log_reynolds=v))
        self.reynolds.released.connect(lambda: self.send("flush"))
        layout.addWidget(self.reynolds)
        self.regulated = QCheckBox("Regulate with Palinstrophy")
        self.regulated.setToolTip("Automatically adjusts Reynolds number from 10,000·P/(Z·kmax²).")
        self.regulated.toggled.connect(lambda v: self.send("set", regulated=v))
        layout.addWidget(self.regulated)
        self.palinstrophy = label("10⁴P/(Z kmax²) ≈ 0.00", "secondary")
        layout.addWidget(self.palinstrophy)
        self._separator(layout)
        self.fields = self._buttons(layout, FIELDS, "display")
        self.palettes = self._buttons(layout, [(k.title(), k) for k in PALETTES], "palette")
        for key, button in self.palettes.items():
            a, b, c = PALETTES[key]
            button.setStyleSheet(f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {a},stop:0.5 {b},stop:1 {c}); color:white; }}")
        self.color_range_row = QWidget()
        range_layout = QVBoxLayout(self.color_range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        self.color_range = self._combo(range_layout, "Color range", [("Sigma", "sigma"), ("Exposure", "exposure")], lambda v: self.send("set", color_range=v))
        layout.addWidget(self.color_range_row)
        self.sigma = FloatSlider("Sigma", 1.5, 3.5, 2.5, lambda v: f"±{v:.1f}σ")
        self.sigma.changed.connect(lambda v: self.send("set", refresh=False, sigma=v))
        layout.addWidget(self.sigma)
        self.exposure = FloatSlider("Exposure", 0.25, 3, 1.2, lambda v: f"{v:.1f}×")
        self.exposure.changed.connect(lambda v: self.send("set", refresh=False, exposure=v))
        layout.addWidget(self.exposure)
        self.flow = QCheckBox("Velocity direction")
        self.flow.toggled.connect(lambda v: self.send("set", flow=v))
        layout.addWidget(self.flow)
        self._separator(layout)
        layout.addWidget(label("NUMERICS", "secondary"))
        self.method = self._combo(layout, "Method", METHODS, lambda v: self.send("configure", integrator=v, cap_cfl=True))
        self.method.setMinimumWidth(0)
        self.form = self._combo(layout, "Form", [("Stress", "stress"), ("Advective", "advective")], lambda v: self.send("configure", operator=v))
        self.cfl = FloatSlider("CFL", 1, 3, 3, lambda v: f"{v:.2f}")
        self.cfl.setToolTip("Adjust the adaptive timestep without restarting the simulation.")
        self.cfl.changed.connect(lambda v: self.send("set", refresh=False, cfl=v))
        self.cfl.released.connect(lambda: self.send("flush"))
        layout.addWidget(self.cfl)
        layout.addStretch()
        scroll.setWidget(content)
        return panel

    def _separator(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#282d38")
        layout.addWidget(line)

    def _buttons(self, layout, values, key):
        group = QButtonGroup(self)
        group.setExclusive(True)
        result = {}
        for title, value in values:
            button = QPushButton(title)
            button.setCheckable(True)
            button.setAccessibleName(title if key == "display" else title + " palette")
            button.clicked.connect(lambda checked=False, v=value: self.send("set", **{key: v}))
            layout.addWidget(button)
            group.addButton(button)
            result[value] = button
        return result

    def _metrics(self):
        self.metrics = QFrame()
        self.metrics.setObjectName("metrics")
        self.metrics.setFixedHeight(54)
        row = QHBoxLayout(self.metrics)
        row.setContentsMargins(14, 5, 14, 5)
        self.metric_labels = {}
        entries = [("turbo", "SIMULATED TIME", "per wall s"), ("turnovers", "INITIAL TURNOVERS", "per wall s"),
                   ("energy", "KINETIC ENERGY", ""), ("enstrophy", "ENSTROPHY", ""),
                   ("dt", "TIMESTEP", "Δt"), ("integration", "FPS", "steps/s"),
                   ("steps", "STEP", ""), ("memory", "GPU ALLOCATION", "MiB")]
        for key, title, unit in entries:
            stack = QVBoxLayout()
            stack.setSpacing(3)
            heading = label(title, "secondary")
            heading.setStyleSheet("font-size:7px")
            stack.addWidget(heading)
            values = QHBoxLayout()
            values.setSpacing(3)
            value = label("—", "metricValue")
            if key in ("turbo", "turnovers"):
                value.setStyleSheet(f"color:#70e8d1; font-size:16px; font-family:'{self.mono_font}'")
            values.addWidget(value)
            if key == "energy":
                self.sparkline = Sparkline()
                values.addWidget(self.sparkline)
            if unit:
                unit_label = label(unit, "secondary")
                unit_label.setStyleSheet("font-size:7px")
                values.addWidget(unit_label)
                if key == "memory":
                    self.memory_unit_label = unit_label
            values.addStretch()
            stack.addLayout(values)
            row.addLayout(stack, 1)
            self.metric_labels[key] = value
        self.metrics.setToolTip("Completed work. Physical throughput averages the entire active run; pauses and manual steps are excluded.")
        self.layout.addWidget(self.metrics)

    def _menus(self):
        menu = self.menuBar()
        file = menu.addMenu("File")
        self.save_action = file.addAction("Save Snapshot…", lambda: self.send("save"), QKeySequence.StandardKey.Save)
        self.close_action = file.addAction("Close Window", self.hide_window, QKeySequence.StandardKey.Close)
        # Qt's portable Ctrl maps to Command on macOS; StandardKey.Quit is
        # unbound on Windows, so specify the requested shortcut explicitly.
        file.addAction("Quit 2DFastTurbo", QApplication.instance().quit, QKeySequence("Ctrl+Q"))
        about = file.addAction("About 2DFastTurbo", self.show_about)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        edit = menu.addMenu("Edit")
        for title, method, shortcut in [("Undo", "undo", QKeySequence.StandardKey.Undo),
            ("Redo", "redo", QKeySequence.StandardKey.Redo), ("Cut", "cut", QKeySequence.StandardKey.Cut),
            ("Copy", "copy", QKeySequence.StandardKey.Copy), ("Paste", "paste", QKeySequence.StandardKey.Paste),
            ("Select All", "selectAll", QKeySequence.StandardKey.SelectAll)]:
            edit.addAction(title, lambda checked=False, name=method: getattr(QApplication.focusWidget(), name, lambda: None)(), shortcut)
        simulation = menu.addMenu("Simulation")
        self.pause_action = simulation.addAction("Pause", lambda: self.send("toggle"))
        self.step_action = simulation.addAction("Single Step", lambda: self.send("step"), QKeySequence("Right"))
        simulation.addAction("Reset Flow", lambda: self.send("reset"), QKeySequence("R"))
        simulation.addAction("New Initial Condition", lambda: self.send("seed"), QKeySequence("Ctrl+Shift+R"))
        view = menu.addMenu("View")
        view.addAction("Focus on Flow", self.toggle_focus, QKeySequence("F"))
        view.addAction("Show Simulation", self.reopen)
        view.addAction("Dismiss Message", lambda: self.send("dismiss"))
        window = menu.addMenu("Window")
        window.addAction("Minimize", self.showMinimized, QKeySequence("Ctrl+M"))

    def show_about(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("About 2DFastTurbo")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        app = QApplication.instance()
        def apply_palette(palette):
            dialog.setPalette(palette)
            background = palette.color(QPalette.ColorRole.Window).name()
            foreground = palette.color(QPalette.ColorRole.WindowText).name()
            dialog.setStyleSheet(f"QDialog {{ background: {background}; }} "
                                f"QLabel {{ color: {foreground}; font-weight: normal; }}")
        apply_palette(app.palette())
        app.paletteChanged.connect(apply_palette)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 18)
        layout.setSpacing(18)
        row = QHBoxLayout()
        row.setSpacing(18)
        icon = QLabel(dialog)
        icon.setPixmap(app.windowIcon().pixmap(64, 64))
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        content = QVBoxLayout()
        content.setSpacing(16)
        description = QLabel(
            "GPU simulation of decaying, homogeneous 2D turbulence. "
            "Fourier pseudospectral methods with 3/2-rule dealiasing "
            "and VkFFT transforms. ETDRK4 time stepping by default.", dialog)
        description.setWordWrap(True)
        description.setFixedWidth(360)
        content.addWidget(description)
        caption = QLabel("Navier–Stokes equation", dialog)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(caption)
        content.addWidget(EquationLabel(dialog), 0, Qt.AlignmentFlag.AlignHCenter)
        row.addLayout(content)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        try:
            dialog.exec()
        finally:
            app.paletteChanged.disconnect(apply_palette)

    def send(self, op, *, refresh=True, **kwargs):
        try:
            if op != "save" or not save_snapshot(self):
                self.native.call(op, **kwargs)
            if refresh:
                self.poll()
        except Exception as error:
            self.status.setText(str(error))
            self.status.show()

    def apply_seed(self):
        try:
            value = int(self.seed.text())
            if not 0 <= value < 2**64:
                raise ValueError()
        except ValueError:
            self.seed.setText(self.state["config"]["seed"])
            return
        self.send("configure", seed=str(value))

    def poll(self):
        started = time.perf_counter()
        state = self.native.call()
        self.state = state
        config = state["config"]
        for widget, value in [(self.k0, config["k0"]), (self.resolution, config["resolution"]),
                              (self.method, config["integrator"]), (self.form, config["operator"]),
                              (self.color_range, state["color_range"])]:
            blocked(widget, widget.setCurrentIndex, widget.findData(value))
        if not self.seed.hasFocus():
            self.seed.setText(config["seed"])
        self.seed.setToolTip(config["seed"])
        self.reynolds.set_value(math.log10(state["effective_reynolds"]))
        self.reynolds.slider.setEnabled(not state["regulated"])
        self.reynolds.label.setText("Re · AUTO" if state["regulated"] else "Re · MANUAL")
        self.cfl.set_value(config["cfl"])
        self.sigma.set_value(state["sigma"])
        self.exposure.set_value(state["exposure"])
        blocked(self.regulated, self.regulated.setChecked, state["regulated"])
        blocked(self.flow, self.flow.setChecked, state["flow"])
        for key, buttons in [("display", self.fields), ("palette", self.palettes)]:
            buttons[state[key]].setChecked(True)
        sigma = state["display"] == "vorticity" and state["color_range"] == "sigma"
        self.sigma.setVisible(sigma)
        self.exposure.setVisible(not sigma)
        self.color_range_row.setVisible(state["display"] == "vorticity")
        self.palinstrophy.setText(f'10⁴P/(Z kmax²) ≈ {state["palinstrophy_control"]:.2f}')
        self.backend.setToolTip(state["gpu"])
        self.field_title.setText(("●  " if state["running"] else "○  ") + state["display"].upper())
        self.canvas.setAccessibleName("Decaying turbulence: " + state["display"].title())
        self.grid_title.setText(f'{config["resolution"]}² · {config["padded_resolution"]}² padded')
        self.legend.set_display(state["display"], state["palette"])
        self.pause_action.setText("Pause" if state["running"] else "Resume")
        for action in [self.pause_action, self.step_action]:
            action.setEnabled(state["ready"])
        self.save_action.setEnabled(state["ready"] and not state["exporting"])
        self.save_button.setEnabled(self.save_action.isEnabled())
        if state["error_message"]:
            self.status.setText(state["error_message"])
            self.status.show()
        else:
            self.status.hide()
        stats, perf = state.get("stats", {}), state["performance"]
        def number(value):
            if not state["ready"]:
                return "—"
            return f"{value:.2e}" if value and (abs(value) < 0.001 or abs(value) >= 10000) else f"{value:.3f}"
        allocation_mib = state["allocated_bytes"] / 1048576
        allocation_gib = allocation_mib >= 1024
        memory = f"{allocation_mib / 1024:.1f}" if allocation_gib else f"{allocation_mib:.0f}"
        self.memory_unit_label.setText("GiB" if allocation_gib else "MiB")
        values = {"turbo": number(perf["simtime_per_s"]), "turnovers": number(perf["turnovers_per_s"]),
                  "energy": number(stats.get("energy", 0)), "enstrophy": number(stats.get("enstrophy", 0)),
                  "dt": number(stats.get("dt", 0)), "integration": f'{perf["steps_per_s"]:.0f}',
                  "steps": str(stats.get("steps", 0)), "memory": memory}
        for key, value in values.items():
            self.metric_labels[key].setText(value)
        if self.sparkline.samples != state["history"]:
            self.sparkline.samples = state["history"]
            self.sparkline.update()
        self.poll_calls += 1
        self.poll_seconds += time.perf_counter() - started

    def toggle_focus(self):
        side = self.canvas.width()
        self._focused = not self._focused
        self.inspector.setVisible(not self._focused)
        self.metrics.setVisible(not self._focused)
        self.native.call("set", focused=self._focused)
        self.setMinimumSize(620 if self._focused else 834, 699 if self._focused else 761)
        self.resize(side + (46 if self._focused else 260), side + (133 if self._focused else 195))

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space
            and event.modifiers() == Qt.KeyboardModifier.NoModifier and self.isActiveWindow()
            and QApplication.activeModalWidget() is None):
            if not event.isAutoRepeat() and self.state.get("ready"):
                self.send("toggle")
            return True
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._attached:
            self._attached = True
            self.native.attach(int(self.canvas.winId()))
            sync_canvas_scale(self)
            QTimer.singleShot(0, lambda: self.send("start"))
        else:
            self.native.call("visible", value=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._adjusting and hasattr(self, "canvas"):
            QTimer.singleShot(0, self._square_canvas)

    def _square_canvas(self):
        # Resize after Qt has applied the new child layout, avoiding stale geometry.
        if self._adjusting or not self.isVisible():
            return
        self.centralWidget().layout().activate()
        delta = self.canvas.width() - self.canvas.height()
        if abs(delta) > 1:
            self._adjusting = True
            self.resize(self.width(), self.height() + delta)
            self._adjusting = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and self._attached and not self._closing:
            sync_canvas_scale(self)
        if event.type() == QEvent.Type.WindowStateChange and self._attached and not self._closing:
            self.native.call("visible", value=not self.isMinimized())

    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        if sys.platform == "win32":
            QSettings().setValue("windowGeometry", self.saveGeometry())
            self.shutdown()
            event.accept()
            QApplication.instance().quit()
            return
        self.hide_window()
        event.ignore()  # macOS keeps the run alive for Dock reopen.

    def hide_window(self):
        if self._closing:
            return
        QSettings().setValue("windowGeometry", self.saveGeometry())
        self.native.call("visible", value=False)
        self.hide()

    def reopen(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.native.call("visible", value=True)

    def application_state_changed(self, state):
        if self._closing:
            return
        # Qt propagates Dock reopen as ApplicationActive, including when already active.
        if state == Qt.ApplicationState.ApplicationActive and not self.isVisible():
            self.reopen()
        if state == Qt.ApplicationState.ApplicationHidden:
            self.native.call("visible", value=False)

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.poll_timer.stop()
        if self.tray:
            self.tray.hide()
        self.native.close()


def create_application():
    name = "2DFastTurbo"
    prepare_application_name(name)
    app = QApplication.instance() or QApplication([name])
    # Keep the existing QSettings namespace while changing the visible name.
    app.setApplicationName("2DFastTurbo Python")
    app.setApplicationDisplayName(name)
    app.setOrganizationName("FastTurbo")
    icon_path = Path(__file__).with_name("assets") / "fastturbo.png"
    app.setWindowIcon(QIcon(str(icon_path)))
    app.setQuitOnLastWindowClosed(False)
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    return app
