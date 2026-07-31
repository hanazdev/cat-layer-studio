from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cat_layer_studio.models.transform import Transform


class TransformControls(QWidget):
    transform_changed = Signal(object)
    reset_requested = Signal()
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._updating = False
        self.x = self._spin(-4096, 4096, 0.25, 2, " px")
        self.y = self._spin(-4096, 4096, 0.25, 2, " px")
        self.width = self._spin(1, 1000, 0.1, 2, "%")
        self.height = self._spin(1, 1000, 0.1, 2, "%")
        self.width.setValue(100)
        self.height.setValue(100)
        self.rotation = self._spin(-360, 360, 0.1, 2, "°")
        self.lock_aspect = QCheckBox("Keep width and height together")
        self.lock_aspect.setChecked(True)
        self.warning = QLabel()
        self.warning.setWordWrap(True)

        values = QGroupBox("Exact fit values")
        form = QFormLayout(values)
        form.addRow("Move left / right", self.x)
        form.addRow("Move up / down", self.y)
        form.addRow("Width", self.width)
        form.addRow("Height", self.height)
        form.addRow("Rotate", self.rotation)
        form.addRow(self.lock_aspect)
        form.addRow(self.warning)

        nudges = QGroupBox("Move by a fixed amount")
        grid = QGridLayout(nudges)
        for row, step in enumerate((0.25, 1.0, 5.0)):
            for column, (label, dx, dy) in enumerate(
                (("←", -step, 0), ("↑", 0, -step), ("↓", 0, step), ("→", step, 0))
            ):
                button = QPushButton(f"{label} {step:g}")
                button.clicked.connect(lambda _=False, a=dx, b=dy: self._nudge(a, b))
                grid.addWidget(button, row, column)

        actions = QGridLayout()
        undo = QPushButton("Undo")
        redo = QPushButton("Redo")
        reset = QPushButton("Reset all")
        undo.clicked.connect(self.undo_requested)
        redo.clicked.connect(self.redo_requested)
        reset.clicked.connect(self.reset_requested)
        actions.addWidget(undo, 0, 0)
        actions.addWidget(redo, 0, 1)
        actions.addWidget(reset, 1, 0, 1, 2)

        layout = QVBoxLayout(self)
        layout.addWidget(values)
        layout.addWidget(nudges)
        layout.addLayout(actions)
        layout.addStretch()
        for spin in (self.x, self.y, self.width, self.height, self.rotation):
            spin.valueChanged.connect(self._value_changed)

    @staticmethod
    def _spin(low: float, high: float, step: float, decimals: int, suffix: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        return spin

    def transform(self) -> Transform:
        return Transform(
            self.x.value(),
            self.y.value(),
            self.width.value() / 100,
            self.height.value() / 100,
            self.rotation.value(),
        )

    def set_transform(self, transform: Transform) -> None:
        self._updating = True
        self.x.setValue(transform.x)
        self.y.setValue(transform.y)
        self.width.setValue(transform.scale_x * 100)
        self.height.setValue(transform.scale_y * 100)
        self.rotation.setValue(transform.rotation_degrees)
        self._updating = False
        self._show_warning(transform)

    def _value_changed(self) -> None:
        if self._updating:
            return
        changed = self.sender()
        if self.lock_aspect.isChecked() and changed in (self.width, self.height):
            other = self.height if changed is self.width else self.width
            self._updating = True
            other.setValue(changed.value())
            self._updating = False
        transform = self.transform()
        self._show_warning(transform)
        self.transform_changed.emit(transform)

    def _show_warning(self, transform: Transform) -> None:
        messages = {
            "ok": "",
            "warning": "Width and height differ by 3–5%. Check the shape carefully.",
            "strong": "Strong warning: width and height differ by more than 5%.",
            "confirmation": "Confirmation required before export: distortion exceeds 10%.",
        }
        self.warning.setText(messages[transform.divergence_level])

    def _nudge(self, dx: float, dy: float) -> None:
        self.x.setValue(self.x.value() + dx)
        self.y.setValue(self.y.value() + dy)
