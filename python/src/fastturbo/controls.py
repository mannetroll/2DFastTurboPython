"""Reference control choices and compact macOS inspector widgets."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

RESOLUTIONS = (128, 256, 512, 1024, 2048, 3072, 4096, 6144, 8192, 9216, 12288, 16384)
WAVENUMBERS = (2, 4, 6, 8, 10, 12, 16, 20)
START_SPECTRA = (("PAO", "PAO"), ("KM3 (k⁻³)", "KM3"))
FIELDS = (("Enstrophy", "enstrophy"), ("Vorticity", "vorticity"), ("Speed", "speed"))
METHODS = (("IMEX RK3", "imexRK3"), ("Integrating-factor RK3", "ifHeunRK3"), ("ETD RK4", "etdrk4"),
           ("CNAB2", "CNAB2"))
PALETTES = {
    "inferno": ("#0f0829", "#ad2947", "#fced8f"),
    "ember": ("#6b2491", "#1f0a1a", "#ffa345"),
    "aurora": ("#3840d1", "#0f172e", "#63f0ba"),
    "glacier": ("#1f4a94", "#081f30", "#a3f0ff"),
}


class FloatSlider(QWidget):
    changed = Signal(float)
    released = Signal()

    def __init__(self, label, low, high, value, formatter, parent=None):
        super().__init__(parent)
        self.low, self.high, self.formatter = low, high, formatter
        self.setAccessibleName(label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.label = QLabel(label)
        self.readout = QLabel()
        self.readout.setObjectName("accent")
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(self.label)
        row.addWidget(self.readout, 1)
        layout.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10000)
        self.slider.setAccessibleName(label)
        self.slider.valueChanged.connect(self._changed)
        self.slider.sliderReleased.connect(self.released)
        layout.addWidget(self.slider)
        self.set_value(value)

    def _changed(self, value):
        real = self.low + value / 10000 * (self.high - self.low)
        self.readout.setText(self.formatter(real))
        self.changed.emit(real)

    def set_value(self, value):
        if self.slider.isSliderDown():
            return
        self.slider.blockSignals(True)
        self.slider.setValue(round((value - self.low) / (self.high - self.low) * 10000))
        self.slider.blockSignals(False)
        self.readout.setText(self.formatter(value))
