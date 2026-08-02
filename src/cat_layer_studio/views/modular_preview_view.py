from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cat_layer_studio.constants import SLOT_LABELS, STANDARD_SLOTS
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.assembly_service import (
    AssemblyHistory,
    apply_recommended_order,
    make_layer,
    move_layer,
)
from cat_layer_studio.services.composition_service import composite_assembly
from cat_layer_studio.services.layer_validation_service import validate_assembly
from cat_layer_studio.widgets.composite_canvas import CompositeCanvas


class LayerTree(QTreeWidget):
    order_changed = Signal()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().dropEvent(event)
        self.order_changed.emit()


class ModularPreviewView(QWidget):
    project_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.project_directory: Path | None = None
        self.project: Project | None = None
        self.history = AssemblyHistory()
        self._refreshing = False
        self._clipboard: tuple[float, float, int] | None = None
        self._rotation_test = 0.0

        intro = QLabel(
            "Assemble separate full-canvas parts, then give movable parts a joint. Every change "
            "appears immediately and can be undone."
        )
        intro.setWordWrap(True)
        checklist = QLabel(
            "1. Add layers  ✓   2. Order   3. Place   4. Assign types   5. Joints   "
            "6. Preview   7. Export   8. Verify"
        )
        checklist.setStyleSheet("font-weight: 600; color: #8bd5ca")
        self.canvas = CompositeCanvas()
        self.canvas.pivot_selected.connect(self._pivot_selected)
        self.background = QComboBox()
        self.background.addItems(["Checkerboard", "White", "Black", "Mid-grey", "Magenta"])
        self.background.currentTextChanged.connect(self.canvas.set_background)
        fit = QPushButton("Fit to screen")
        actual = QPushButton("Actual size / 100%")
        zoom_in = QPushButton("Zoom in")
        zoom_out = QPushButton("Zoom out")
        fit.clicked.connect(self.canvas.fit_to_view)
        actual.clicked.connect(self.canvas.actual_size)
        zoom_in.clicked.connect(lambda: self.canvas.scale(1.2, 1.2))
        zoom_out.clicked.connect(lambda: self.canvas.scale(1 / 1.2, 1 / 1.2))
        self.master_visible = QCheckBox("Show master reference")
        self.master_opacity = QDoubleSpinBox()
        self.master_opacity.setRange(0, 1)
        self.master_opacity.setSingleStep(0.1)
        self.master_opacity.setValue(0.35)
        self.master_visible.toggled.connect(self.refresh_canvas)
        self.master_opacity.valueChanged.connect(self.refresh_canvas)
        toolbar = QHBoxLayout()
        for widget in (
            fit,
            actual,
            zoom_in,
            zoom_out,
            QLabel("Background:"),
            self.background,
            self.master_visible,
            QLabel("Reference opacity:"),
            self.master_opacity,
        ):
            toolbar.addWidget(widget)
        toolbar.addStretch()

        self.layers = LayerTree()
        self.layers.setIconSize(QSize(48, 48))
        self.layers.setHeaderLabels(
            ["Visible", "Locked", "Layer (front at top)", "Part type", "Draw order"]
        )
        self.layers.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.layers.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.layers.setRootIsDecorated(False)
        self.layers.itemSelectionChanged.connect(self._selection_changed)
        self.layers.itemChanged.connect(self._item_changed)
        self.layers.order_changed.connect(self._drag_reordered)
        recommended = QPushButton("Apply recommended front-sitting order")
        forward = QPushButton("Move forward")
        backward = QPushButton("Move backward")
        front = QPushButton("Move to front")
        back = QPushButton("Move to back")
        duplicate = QPushButton("Duplicate reference")
        remove = QPushButton("Remove from assembly")
        recommended.clicked.connect(self._recommended)
        forward.clicked.connect(lambda: self._move("front"))
        backward.clicked.connect(lambda: self._move("back"))
        front.clicked.connect(lambda: self._move("top"))
        back.clicked.connect(lambda: self._move("bottom"))
        duplicate.clicked.connect(self._duplicate)
        remove.clicked.connect(self._remove)
        layer_actions = QHBoxLayout()
        for widget in (recommended, forward, backward, front, back, duplicate, remove):
            layer_actions.addWidget(widget)

        self.properties = QWidget()
        form = QFormLayout(self.properties)
        self.display_name = QLineEdit()
        self.display_name.editingFinished.connect(self._properties_changed)
        self.slot = QComboBox()
        self.slot.setEditable(True)
        for slot in STANDARD_SLOTS:
            self.slot.addItem(SLOT_LABELS[slot], slot)
        self.x = self._decimal(-8192, 8192, 0.25)
        self.y = self._decimal(-8192, 8192, 0.25)
        self.z = self._decimal(-4096, 4096, 1, decimals=0)
        self.opacity = self._decimal(0, 1, 0.05)
        self.locked = QCheckBox("Prevent accidental movement")
        self.visible = QCheckBox("Show this layer")
        self.joint = QComboBox()
        self.pivot_x = self._decimal(-8192, 8192, 0.25)
        self.pivot_y = self._decimal(-8192, 8192, 0.25)
        for joint in get_rig_template("adult_front_sitting").joints:
            self.joint.addItem(joint.name)
        for widget in (
            self.slot,
            self.x,
            self.y,
            self.z,
            self.opacity,
            self.locked,
            self.visible,
            self.joint,
            self.pivot_x,
            self.pivot_y,
        ):
            if isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._properties_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._properties_changed)
            else:
                widget.valueChanged.connect(self._properties_changed)
        form.addRow("Layer name", self.display_name)
        form.addRow("Part type (stable Godot slot)", self.slot)
        form.addRow("Move left / right (X position)", self.x)
        form.addRow("Move up / down (Y position)", self.y)
        form.addRow("Draw order (Godot z-index)", self.z)
        form.addRow("Opacity", self.opacity)
        form.addRow("Visibility", self.visible)
        form.addRow("Lock", self.locked)
        form.addRow("Attach this part to", self.joint)
        form.addRow("Movement joint X (pivot)", self.pivot_x)
        form.addRow("Movement joint Y (pivot)", self.pivot_y)
        place = QPushButton("Place movement joint on canvas")
        reset_pivot = QPushButton("Reset to suggested joint")
        minus = QPushButton("Test -5°")
        rest = QPushButton("Rest position")
        plus = QPushButton("Test +5°")
        place.clicked.connect(self.canvas.place_pivot)
        reset_pivot.clicked.connect(self._reset_pivot)
        minus.clicked.connect(lambda: self._test_rotation(-5))
        rest.clicked.connect(lambda: self._test_rotation(0))
        plus.clicked.connect(lambda: self._test_rotation(5))
        joint_actions = QHBoxLayout()
        for widget in (place, reset_pivot, minus, rest, plus):
            joint_actions.addWidget(widget)
        form.addRow("Joint preview", joint_actions)
        nudge = QHBoxLayout()
        for label, dx, dy in (
            ("← 0.25", -0.25, 0),
            ("→ 0.25", 0.25, 0),
            ("↑ 1", 0, -1),
            ("↓ 1", 0, 1),
            ("← 5", -5, 0),
            ("→ 5", 5, 0),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, a=dx, b=dy: self.nudge(a, b))
            nudge.addWidget(button)
        form.addRow("Quick movement", nudge)
        placement_actions = QHBoxLayout()
        for label, callback in (
            ("Reset selected", self._reset_selected),
            ("Reset all offsets", self._reset_all),
            ("Copy placement", self._copy),
            ("Paste placement", self._paste),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            placement_actions.addWidget(button)
        form.addRow("Placement", placement_actions)

        self.validation = QLabel("Open a project to validate its assembly.")
        self.validation.setWordWrap(True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.properties)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Selected layer"))
        right_layout.addWidget(scroll, 1)
        right_layout.addWidget(QLabel("Readiness checks"))
        right_layout.addWidget(self.validation)
        splitter = QSplitter()
        splitter.addWidget(self.canvas)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([850, 430])

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(checklist)
        layout.addLayout(toolbar)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.layers)
        layout.addLayout(layer_actions)
        self._install_shortcuts()

    @staticmethod
    def _decimal(
        minimum: float, maximum: float, step: float, *, decimals: int = 2
    ) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSingleStep(step)
        widget.setDecimals(decimals)
        return widget

    def set_project(self, directory: Path, project: Project) -> None:
        self.project_directory = directory
        self.project = project
        self.history.reset(project.assembly_layers)
        self.refresh()

    def add_component(self, texture: Path) -> None:
        if not self.project or not self.project_directory:
            return
        next_z = max((layer.z_index for layer in self.project.assembly_layers), default=0) + 10
        self.project.assembly_layers.append(make_layer(self.project_directory, texture, next_z))
        self._commit()
        self.refresh(select_id=self.project.assembly_layers[-1].id)

    def selected_layer(self) -> AssemblyLayer | None:
        if not self.project:
            return None
        items = self.layers.selectedItems()
        if not items:
            return None
        layer_id = items[0].data(0, Qt.ItemDataRole.UserRole)
        return next((layer for layer in self.project.assembly_layers if layer.id == layer_id), None)

    def refresh(self, *, select_id: str | None = None) -> None:
        self._refreshing = True
        current = select_id or (self.selected_layer().id if self.selected_layer() else None)
        self.layers.clear()
        if self.project:
            for layer in sorted(
                self.project.assembly_layers, key=lambda item: (item.z_index, item.id), reverse=True
            ):
                item = QTreeWidgetItem(
                    [
                        "",
                        "",
                        layer.display_name,
                        SLOT_LABELS.get(layer.slot, layer.slot),
                        str(layer.z_index),
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, layer.id)
                if self.project_directory:
                    texture = self.project.resolve(self.project_directory, layer.texture_path)
                    item.setIcon(2, QIcon(QPixmap(str(texture))))
                item.setCheckState(
                    0, Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked
                )
                item.setCheckState(
                    1, Qt.CheckState.Checked if layer.locked else Qt.CheckState.Unchecked
                )
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
                )
                self.layers.addTopLevelItem(item)
                if layer.id == current:
                    item.setSelected(True)
            self.layers.resizeColumnToContents(0)
            self.layers.resizeColumnToContents(1)
            self.layers.resizeColumnToContents(2)
        self._refreshing = False
        self._selection_changed()
        self.refresh_canvas()
        self.refresh_validation()

    def refresh_canvas(self) -> None:
        if not self.project or not self.project_directory:
            return
        try:
            selected = self.selected_layer()
            image = composite_assembly(
                self.project_directory,
                self.project,
                rotation_layer_id=selected.id if selected else None,
                rotation_degrees=self._rotation_test,
            )
            if self.master_visible.isChecked():
                master_path = self.project.resolve(self.project_directory, self.project.master_path)
                with Image.open(master_path) as opened:
                    master = opened.convert("RGBA")
                reference = Image.new("RGBA", self.project.canvas_size, (0, 0, 0, 0))
                reference.alpha_composite(
                    master,
                    (
                        (self.project.canvas_width - master.width) // 2,
                        (self.project.canvas_height - master.height) // 2,
                    ),
                )
                reference.putalpha(
                    reference.getchannel("A").point(
                        lambda value: round(value * self.master_opacity.value())
                    )
                )
                reference.alpha_composite(image)
                image = reference
            self.canvas.set_image(image)
            layer = self.selected_layer()
            self.canvas.show_pivot(
                layer.pivot_x if layer else None, layer.pivot_y if layer else None
            )
        except (OSError, ValueError) as error:
            self.validation.setText(f"Preview failed: {error}")

    def refresh_validation(self) -> None:
        if not self.project or not self.project_directory:
            return
        self.validation.setText(
            "\n".join(
                f"{item.name}: {item.status}" + (f" — {item.detail}" if item.detail else "")
                for item in validate_assembly(self.project_directory, self.project)
            )
        )

    def _selection_changed(self) -> None:
        layer = self.selected_layer()
        self.properties.setEnabled(layer is not None)
        if not layer:
            self.canvas.show_pivot(None, None)
            return
        self._refreshing = True
        self.display_name.setText(layer.display_name)
        index = self.slot.findData(layer.slot)
        if index >= 0:
            self.slot.setCurrentIndex(index)
        else:
            self.slot.setEditText(layer.slot)
        self.x.setValue(layer.offset_x)
        self.y.setValue(layer.offset_y)
        self.z.setValue(layer.z_index)
        self.opacity.setValue(layer.opacity)
        self.visible.setChecked(layer.visible)
        self.locked.setChecked(layer.locked)
        self.joint.setCurrentText(layer.attachment_joint or "Root")
        self.pivot_x.setValue(layer.pivot_x or 0)
        self.pivot_y.setValue(layer.pivot_y or 0)
        self._refreshing = False
        self.refresh_canvas()

    def _properties_changed(self, *_args) -> None:  # type: ignore[no-untyped-def]
        if self._refreshing or not (layer := self.selected_layer()):
            return
        if layer.locked and (self.x.value() != layer.offset_x or self.y.value() != layer.offset_y):
            self._selection_changed()
            QMessageBox.information(self, "Layer is locked", "Unlock this layer before moving it.")
            return
        layer.display_name = self.display_name.text().strip() or layer.display_name
        layer.slot = (
            self.slot.currentData()
            if self.slot.currentData()
            and self.slot.currentText() == self.slot.itemText(self.slot.currentIndex())
            else self.slot.currentText().strip().lower().replace(" ", "_")
        )
        layer.offset_x = self.x.value()
        layer.offset_y = self.y.value()
        layer.z_index = round(self.z.value())
        layer.opacity = self.opacity.value()
        layer.visible = self.visible.isChecked()
        layer.locked = self.locked.isChecked()
        layer.attachment_joint = self.joint.currentText()
        layer.pivot_x = self.pivot_x.value()
        layer.pivot_y = self.pivot_y.value()
        self._commit()
        self.refresh(select_id=layer.id)

    def _item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._refreshing or not self.project:
            return
        layer = next(
            layer
            for layer in self.project.assembly_layers
            if layer.id == item.data(0, Qt.ItemDataRole.UserRole)
        )
        layer.visible = item.checkState(0) == Qt.CheckState.Checked
        layer.locked = item.checkState(1) == Qt.CheckState.Checked
        self._commit()
        self.refresh(select_id=layer.id)

    def _drag_reordered(self) -> None:
        if self._refreshing or not self.project:
            return
        # The tree is front-first, so assign increasing z from its bottom row upward.
        for index in range(self.layers.topLevelItemCount()):
            item = self.layers.topLevelItem(index)
            layer = next(
                layer
                for layer in self.project.assembly_layers
                if layer.id == item.data(0, Qt.ItemDataRole.UserRole)
            )
            layer.z_index = (self.layers.topLevelItemCount() - index) * 10
        self._commit()
        self.refresh()

    def _commit(self) -> None:
        if not self.project:
            return
        self.history.commit(self.project.assembly_layers)
        self.project.godot_export_status = "Ready to export"
        self.project_changed.emit()

    def undo(self) -> None:
        if self.project and (state := self.history.undo()) is not None:
            self.project.assembly_layers = state
            self.project_changed.emit()
            self.refresh()

    def redo(self) -> None:
        if self.project and (state := self.history.redo()) is not None:
            self.project.assembly_layers = state
            self.project_changed.emit()
            self.refresh()

    def nudge(self, dx: float, dy: float) -> None:
        layer = self.selected_layer()
        if not layer or layer.locked:
            return
        layer.offset_x += dx
        layer.offset_y += dy
        self._commit()
        self.refresh(select_id=layer.id)

    def _move(self, direction: str) -> None:
        if self.project and (layer := self.selected_layer()):
            move_layer(self.project.assembly_layers, layer.id, direction)
            self._commit()
            self.refresh(select_id=layer.id)

    def _recommended(self) -> None:
        if self.project:
            apply_recommended_order(self.project)
            self._commit()
            self.refresh()

    def _duplicate(self) -> None:
        if self.project and (layer := self.selected_layer()):
            clone = deepcopy(layer)
            from uuid import uuid4

            clone.id = uuid4().hex
            clone.display_name += " copy"
            clone.z_index += 1
            self.project.assembly_layers.append(clone)
            self._commit()
            self.refresh(select_id=clone.id)

    def _remove(self) -> None:
        if self.project and (layer := self.selected_layer()):
            self.project.assembly_layers.remove(layer)
            self._commit()
            self.refresh()

    def _reset_selected(self) -> None:
        if layer := self.selected_layer():
            layer.offset_x = layer.offset_y = 0
            layer.opacity = 1
            self._reset_pivot()
            self._commit()
            self.refresh(select_id=layer.id)

    def _reset_all(self) -> None:
        if self.project:
            for layer in self.project.assembly_layers:
                layer.offset_x = layer.offset_y = 0
            self._commit()
            self.refresh()

    def _copy(self) -> None:
        if layer := self.selected_layer():
            self._clipboard = (layer.offset_x, layer.offset_y, layer.z_index)

    def _paste(self) -> None:
        if self._clipboard and (layer := self.selected_layer()) and not layer.locked:
            layer.offset_x, layer.offset_y, layer.z_index = self._clipboard
            self._commit()
            self.refresh(select_id=layer.id)

    def _pivot_selected(self, x: float, y: float) -> None:
        if layer := self.selected_layer():
            layer.pivot_x, layer.pivot_y = x, y
            self._commit()
            self.refresh(select_id=layer.id)

    def _reset_pivot(self) -> None:
        if not (layer := self.selected_layer()) or not self.project:
            return
        template = get_rig_template(self.project.rig_profile)
        joint = next(
            (
                joint
                for joint in template.joints
                if joint.name == (layer.attachment_joint or "Root")
            ),
            None,
        )
        if joint:
            layer.pivot_x, layer.pivot_y = joint.suggested_pivot
            self._commit()
            self.refresh(select_id=layer.id)

    def _test_rotation(self, degrees: float) -> None:
        self._rotation_test = degrees
        self.refresh_canvas()

    def _install_shortcuts(self) -> None:
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in (
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
        ):
            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            for sequence, multiplier in ((key, 1), (f"Shift+{key}", 5), (f"Alt+{key}", 0.25)):
                shortcut = QShortcut(QKeySequence(sequence), self.canvas)
                shortcut.activated.connect(
                    lambda a=dx, b=dy, factor=multiplier: self.nudge(a * factor, b * factor)
                )
                self._shortcuts.append(shortcut)
