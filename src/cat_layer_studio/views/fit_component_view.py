from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.alpha_validation_service import validate_alpha
from cat_layer_studio.services.comparison_service import alpha_only, difference, edge_view, overlay
from cat_layer_studio.services.export_service import export_component
from cat_layer_studio.services.image_loader import LoadedImage, load_image
from cat_layer_studio.services.landmark_fit_service import suggest_uniform_transform
from cat_layer_studio.services.transform_service import rasterise_transform
from cat_layer_studio.widgets.image_canvas import CanvasTool, ImageCanvas
from cat_layer_studio.widgets.transform_controls import TransformControls


class FitComponentView(QWidget):
    candidate_imported = Signal(str)
    transform_committed = Signal(object)
    component_exported = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.project_directory: Path | None = None
        self.canvas_size = (512, 512)
        self.master: Image.Image | None = None
        self.candidate: LoadedImage | None = None
        self.candidate_path: Path | None = None
        self.history: list[Transform] = [Transform()]
        self.history_index = 0
        self._mask_edited = False
        self._point_target = "candidate"
        self.candidate_points: list[tuple[float, float]] = []
        self.master_points: list[tuple[float, float]] = []

        intro = QLabel(
            "Fit the imported part to the locked master. Changes stay non-destructive until you "
            "explicitly export a full-canvas layer."
        )
        intro.setWordWrap(True)
        self.canvas = ImageCanvas()
        self.canvas.coordinates_changed.connect(
            lambda x, y: self.coordinates.setText(f"Canvas position: {x}, {y}")
        )
        self.canvas.point_selected.connect(self._point_selected)
        self.canvas.mask_changed.connect(lambda: setattr(self, "_mask_edited", True))
        self.controls = TransformControls()
        self.controls.transform_changed.connect(self._transform_changed)
        self.controls.reset_requested.connect(lambda: self._commit_transform(Transform()))
        self.controls.undo_requested.connect(self.undo)
        self.controls.redo_requested.connect(self.redo)

        import_button = QPushButton("Import part…")
        import_button.clicked.connect(self.choose_candidate)
        self.mode = QComboBox()
        self.mode.addItems(
            ["Overlay", "Master", "Candidate", "Flicker", "Difference", "Alpha", "Edges"]
        )
        self.mode.currentTextChanged.connect(self.refresh)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(50)
        self.opacity.valueChanged.connect(self.refresh)
        fifty = QPushButton("50%")
        fifty.clicked.connect(lambda: self.opacity.setValue(50))
        self.background = QComboBox()
        self.background.addItems(["Checkerboard", "White", "Black", "Mid-grey", "Magenta"])
        self.background.currentTextChanged.connect(self.canvas.set_background)
        fit = QPushButton("Fit to screen")
        fit.clicked.connect(self.canvas.fit_to_view)
        actual = QPushButton("Actual size")
        actual.clicked.connect(self.canvas.actual_size)
        self.coordinates = QLabel("Canvas position: —")

        toolbar = QHBoxLayout()
        for widget in (
            import_button,
            QLabel("Compare:"),
            self.mode,
            QLabel("Part opacity:"),
            self.opacity,
            fifty,
            QLabel("Background:"),
            self.background,
            fit,
            actual,
        ):
            toolbar.addWidget(widget)

        mask_bar = QHBoxLayout()
        keep = QPushButton("Keep area")
        remove = QPushButton("Remove area")
        pan = QPushButton("Pan")
        clear = QPushButton("Clear selection")
        invert = QPushButton("Invert selection")
        brush = QSpinBox()
        brush.setRange(1, 200)
        brush.setValue(20)
        keep.clicked.connect(lambda: self.canvas.set_tool(CanvasTool.KEEP))
        remove.clicked.connect(lambda: self.canvas.set_tool(CanvasTool.REMOVE))
        pan.clicked.connect(lambda: self.canvas.set_tool(CanvasTool.PAN))
        clear.clicked.connect(self.canvas.clear_mask)
        invert.clicked.connect(self.canvas.invert_mask)
        brush.valueChanged.connect(self.canvas.set_brush_size)
        for widget in (
            QLabel("Choose the area to keep:"),
            keep,
            remove,
            QLabel("Brush:"),
            brush,
            clear,
            invert,
            pan,
        ):
            mask_bar.addWidget(widget)
        mask_bar.addStretch()

        landmark_bar = QHBoxLayout()
        candidate_point = QPushButton("Add point on part")
        master_point = QPushButton("Add matching point on master")
        suggest = QPushButton("Preview landmark fit")
        clear_points = QPushButton("Reset points")
        candidate_point.clicked.connect(lambda: self._choose_point_target("candidate"))
        master_point.clicked.connect(lambda: self._choose_point_target("master"))
        suggest.clicked.connect(self._suggest_landmark_fit)
        clear_points.clicked.connect(self._clear_points)
        self.landmark_status = QLabel("No matching points selected")
        for widget in (candidate_point, master_point, suggest, clear_points, self.landmark_status):
            landmark_bar.addWidget(widget)
        landmark_bar.addStretch()

        splitter = QSplitter()
        splitter.addWidget(self.canvas)
        splitter.addWidget(self.controls)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([850, 300])

        self.alpha_status = QLabel("Import a part to inspect transparency.")
        self.alpha_status.setWordWrap(True)
        export = QPushButton("Export approved full-canvas PNG…")
        export.clicked.connect(self.choose_export)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(toolbar)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.coordinates)
        layout.addLayout(landmark_bar)
        layout.addLayout(mask_bar)
        layout.addWidget(self.alpha_status)
        layout.addWidget(export)

        self.flicker = QTimer(self)
        self.flicker.setInterval(400)
        self.flicker.timeout.connect(self._flicker_frame)
        self._show_candidate_frame = False
        self._install_shortcuts()

    def set_project(self, directory: Path, master_path: Path, canvas_size: tuple[int, int]) -> None:
        self.project_directory = directory
        self.canvas_size = canvas_size
        self.master = load_image(master_path).image
        if self.master.size != canvas_size:
            QMessageBox.warning(
                self,
                "Master size differs",
                f"The master is {self.master.width} × {self.master.height}, but the locked project "
                f"canvas is {canvas_size[0]} × {canvas_size[1]}. It will be shown centred without "
                "stretching.",
            )
        self.refresh()

    def load_candidate(self, path: Path, transform: Transform | None = None) -> None:
        self.candidate = load_image(path)
        self.candidate_path = path
        chosen = transform or Transform()
        self.history = [chosen]
        self.history_index = 0
        self.controls.set_transform(chosen)
        report = validate_alpha(self.candidate.image, source_had_alpha=self.candidate.had_alpha)
        size_note = (
            f"Original size: {self.candidate.original_size[0]} × "
            f"{self.candidate.original_size[1]}. "
        )
        self.alpha_status.setText(
            size_note + (" ".join(report.messages) or "Transparency looks plausible.")
        )
        self.candidate_imported.emit(str(path))
        self.refresh()
        QTimer.singleShot(0, self.canvas.fit_to_view)

    def choose_candidate(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import a transparent part", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if filename:
            self.load_candidate(Path(filename))

    def choose_export(self) -> None:
        if not self.candidate:
            QMessageBox.information(
                self, "Import a part first", "Choose a candidate image before exporting."
            )
            return
        if self.controls.transform().divergence_level == "confirmation":
            answer = QMessageBox.question(
                self,
                "Confirm strong distortion",
                "Width and height differ by more than 10%. Export this deliberately distorted "
                "result?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        start = str((self.project_directory or Path.cwd()) / "components" / "component.png")
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export full-canvas layer", start, "PNG (*.png)"
        )
        if not filename:
            return
        destination = Path(filename)
        overwrite = False
        if destination.exists():
            overwrite = (
                QMessageBox.question(
                    self, "Replace existing component?", "This file already exists. Replace it?"
                )
                == QMessageBox.StandardButton.Yes
            )
            if not overwrite:
                return
        result = export_component(
            self.candidate.image,
            self.controls.transform(),
            self.canvas_size,
            destination,
            mask=self.canvas.mask() if self._mask_edited else None,
            overwrite=overwrite,
        )
        self.component_exported.emit(str(result.path))
        QMessageBox.information(
            self,
            "Layer exported",
            f"Saved a {result.width} × {result.height} RGBA PNG. In Godot, use position (0, 0), "
            "scale (1, 1), and rotation 0.",
        )

    def refresh(self) -> None:
        if self.master is None:
            return
        master = self._master_canvas()
        if not self.candidate:
            self.canvas.set_image(master)
            return
        fitted = rasterise_transform(
            self.candidate.image, self.controls.transform(), self.canvas_size
        )
        mode = self.mode.currentText()
        if mode != "Flicker":
            self.flicker.stop()
        if mode == "Master":
            shown = master
        elif mode == "Candidate":
            shown = fitted
        elif mode == "Difference":
            shown = difference(master, fitted, highlighted=True)
        elif mode == "Alpha":
            shown = alpha_only(fitted)
        elif mode == "Edges":
            shown = edge_view(fitted)
        elif mode == "Flicker":
            if not self.flicker.isActive():
                self.flicker.start()
            shown = fitted if self._show_candidate_frame else master
        else:
            shown = overlay(master, fitted, self.opacity.value() / 100)
        self.canvas.set_image(shown)

    def undo(self) -> None:
        if self.history_index > 0:
            self.history_index -= 1
            self.controls.set_transform(self.history[self.history_index])
            self.refresh()
            self.transform_committed.emit(self.history[self.history_index])

    def redo(self) -> None:
        if self.history_index + 1 < len(self.history):
            self.history_index += 1
            self.controls.set_transform(self.history[self.history_index])
            self.refresh()
            self.transform_committed.emit(self.history[self.history_index])

    def _master_canvas(self) -> Image.Image:
        assert self.master is not None
        canvas = Image.new("RGBA", self.canvas_size, (0, 0, 0, 0))
        canvas.alpha_composite(
            self.master,
            (
                (self.canvas_size[0] - self.master.width) // 2,
                (self.canvas_size[1] - self.master.height) // 2,
            ),
        )
        return canvas

    def _transform_changed(self, transform: Transform) -> None:
        self._commit_transform(transform)

    def _commit_transform(self, transform: Transform) -> None:
        if transform == self.history[self.history_index]:
            return
        self.history = self.history[: self.history_index + 1]
        self.history.append(transform)
        self.history_index += 1
        self.controls.set_transform(transform)
        self.transform_committed.emit(transform)
        self.refresh()

    def _flicker_frame(self) -> None:
        self._show_candidate_frame = not self._show_candidate_frame
        self.refresh()

    def _choose_point_target(self, target: str) -> None:
        self._point_target = target
        self.canvas.set_tool(CanvasTool.POINT)
        self.landmark_status.setText(f"Click the matching point on the {target}.")

    def _point_selected(self, x: float, y: float) -> None:
        points = self.candidate_points if self._point_target == "candidate" else self.master_points
        points.append((x, y))
        self.landmark_status.setText(
            f"Part points: {len(self.candidate_points)}; master points: {len(self.master_points)}"
        )
        self.canvas.set_tool(CanvasTool.PAN)

    def _suggest_landmark_fit(self) -> None:
        try:
            suggestion = suggest_uniform_transform(self.candidate_points, self.master_points)
        except ValueError as error:
            QMessageBox.information(self, "More points needed", str(error))
            return
        transform = suggestion.transform
        answer = QMessageBox.question(
            self,
            "Preview suggested fit",
            f"Move X {transform.x:.2f}px, Y {transform.y:.2f}px; resize to "
            f"{transform.scale_x * 100:.2f}%; rotate {transform.rotation_degrees:.2f}°. "
            f"Estimated landmark error: {suggestion.rms_error:.2f}px. Accept this suggestion?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._commit_transform(transform)

    def _clear_points(self) -> None:
        self.candidate_points.clear()
        self.master_points.clear()
        self.landmark_status.setText("No matching points selected")

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
                shortcut = QShortcut(QKeySequence(sequence), self)
                shortcut.activated.connect(
                    lambda a=dx, b=dy, factor=multiplier: self.controls._nudge(
                        a * factor, b * factor
                    )
                )
                self._shortcuts.append(shortcut)
