from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cat_layer_studio.constants import APP_NAME, DEFAULT_RIG_PROFILE
from cat_layer_studio.models.project import CandidateState, Project
from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.image_loader import load_image
from cat_layer_studio.services.project_service import (
    create_project,
    import_source,
    load_project,
    replace_master,
    save_project,
)
from cat_layer_studio.services.transform_service import fit_inside_transform
from cat_layer_studio.views.automatic_animations_view import AutomaticAnimationsView
from cat_layer_studio.views.component_library_view import ComponentLibraryView
from cat_layer_studio.views.export_view import ExportView
from cat_layer_studio.views.fit_component_view import FitComponentView
from cat_layer_studio.views.modular_preview_view import ModularPreviewView
from cat_layer_studio.widgets.fit_preview_dialog import FitPreviewDialog


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self.project_directory: Path | None = None
        self.project: Project | None = None

        self.tabs = QTabWidget()
        self.project_view = self._build_project_view()
        self.fit_view = FitComponentView()
        self.library = QListWidget()
        self.preview_view = self._message_view(
            "Modular Preview",
            "Phase 1 prepares one precise reusable layer at a time. Exported components appear "
            "in the library; multi-part preset assembly follows after the fitting acceptance "
            "tests pass.",
        )
        self.export_view = self._message_view(
            "Export",
            "Use “Export approved full-canvas PNG” in Fit Component. Each result is RGBA, uses the "
            "locked project canvas, and has its fitting transform baked in for Godot at (0, 0).",
        )
        self.library = ComponentLibraryView()
        self.preview_view = ModularPreviewView()
        self.animations_view = AutomaticAnimationsView()
        self.export_view = ExportView()
        self.tabs.addTab(self.project_view, "1. Project")
        self.tabs.addTab(self.fit_view, "2. Fit Component")
        self.tabs.addTab(self.library, "3. Component Library")
        self.tabs.addTab(self.preview_view, "4. Modular Preview")
        self.tabs.addTab(self.animations_view, "5. Automatic Animations")
        self.tabs.addTab(self.export_view, "6. Export")
        self.setCentralWidget(self.tabs)

        self.fit_view.candidate_imported.connect(self._candidate_imported)
        self.fit_view.transform_committed.connect(self._transform_committed)
        self.fit_view.component_exported.connect(self._component_exported)
        self.library.add_requested.connect(self._add_component_to_assembly)
        self.library.library_changed.connect(self._save_assembly)
        self.preview_view.project_changed.connect(self._save_assembly)
        self.animations_view.project_changed.connect(self._save_assembly)
        self.export_view.project_changed.connect(self._save_assembly)
        self.statusBar().showMessage("Create a project or open an existing project to begin.")

    def _build_project_view(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Welcome to Cat Layer Studio")
        heading.setStyleSheet("font-size: 24px; font-weight: 600")
        description = QLabel(
            "Start a project with one locked master cat. Then import a part that almost fits, "
            "compare it visually, adjust it precisely, choose the area to keep, and export it."
        )
        description.setWordWrap(True)
        create = QPushButton("Create my first project…")
        open_existing = QPushButton("Open an existing project…")
        create.clicked.connect(self.create_project_dialog)
        open_existing.clicked.connect(self.open_project_dialog)
        actions = QHBoxLayout()
        actions.addWidget(create)
        actions.addWidget(open_existing)
        actions.addStretch()
        self.master_details_button = QPushButton("View master details")
        self.replace_master_button = QPushButton("Replace master image…")
        self.resize_master_button = QPushButton("Resize current master to project canvas")
        self.master_details_button.clicked.connect(self.view_master_details)
        self.replace_master_button.clicked.connect(self.replace_master_dialog)
        self.resize_master_button.clicked.connect(self.resize_current_master)
        master_actions = QHBoxLayout()
        for button in (
            self.master_details_button,
            self.replace_master_button,
            self.resize_master_button,
        ):
            button.setEnabled(False)
            master_actions.addWidget(button)
        master_actions.addStretch()
        self.project_status = QLabel("No project is open.")
        self.project_status.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addLayout(actions)
        layout.addWidget(self.project_status)
        layout.addLayout(master_actions)
        layout.addStretch()
        return page

    @staticmethod
    def _message_view(title: str, message: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 20px; font-weight: 600")
        body = QLabel(message)
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addStretch()
        return page

    def create_project_dialog(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose an empty project directory")
        if not directory:
            return
        master, _ = QFileDialog.getOpenFileName(
            self, "Choose the canonical master cat", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if not master:
            return
        dialog = QWidget()
        name = QLineEdit(Path(directory).name.replace("-", " ").title())
        width = QSpinBox()
        height = QSpinBox()
        for spin in (width, height):
            spin.setRange(1, 8192)
            spin.setValue(512)
        form = QFormLayout(dialog)
        form.addRow("Project name", name)
        form.addRow("Canvas width", width)
        form.addRow("Canvas height", height)
        # Use a compact confirmation dialog while keeping the editable fields plain-language.
        box = QMessageBox(self)
        box.setWindowTitle("Create project")
        box.setText("Confirm the project name and locked canvas size.")
        box.layout().addWidget(dialog, 1, 0, 1, box.layout().columnCount())
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return
        project = Project(name.text().strip() or "Cat project", "", width.value(), height.value())
        try:
            master_path = Path(master)
            while True:
                loaded = load_image(master_path)
                if loaded.original_size == project.canvas_size:
                    normalise = False
                    break
                choice = self._master_size_choice(loaded.image, project.canvas_size)
                if choice == "normalise":
                    normalise = True
                    break
                if choice == "keep":
                    normalise = False
                    break
                if choice == "choose":
                    replacement, _ = QFileDialog.getOpenFileName(
                        self,
                        "Choose another canonical master cat",
                        "",
                        "Images (*.png *.jpg *.jpeg *.webp)",
                    )
                    if not replacement:
                        return
                    master_path = Path(replacement)
                    continue
                return
            create_project(Path(directory), project, master_path, normalise_master=normalise)
            self._activate_project(Path(directory), project)
        except (OSError, ValueError, RuntimeError, MemoryError) as error:
            QMessageBox.critical(
                self,
                "Project could not be created",
                f"The project was not created. {error} Choose another directory or image; "
                "no source file was changed.",
            )

    def open_project_dialog(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open a Cat Layer Studio project", "", "Project (project.json)"
        )
        if not filename:
            return
        try:
            directory, project = load_project(Path(filename))
            self._activate_project(directory, project)
        except (OSError, ValueError, RuntimeError, MemoryError) as error:
            QMessageBox.critical(
                self,
                "Project could not be opened",
                f"The project metadata could not be read safely. {error} No project file was "
                "changed.",
            )

    def _activate_project(self, directory: Path, project: Project) -> None:
        self.project_directory = directory
        self.project = project
        master = project.resolve(directory, project.master_path)
        self.fit_view.set_project(directory, master, project.canvas_size)
        if project.candidate:
            self.fit_view.load_candidate(
                project.resolve(directory, project.candidate.source_path),
                project.candidate.transform,
            )
        self.project_status.setText(
            f"Open project: {project.name}\nDirectory: {directory}\nLocked canvas: "
            f"{project.canvas_width} × {project.canvas_height}\nMaster: {project.master_path}\n"
            f"Rig profile identifier: {project.rig_profile or DEFAULT_RIG_PROFILE}"
        )
        for button in (
            self.master_details_button,
            self.replace_master_button,
            self.resize_master_button,
        ):
            button.setEnabled(True)
        self._refresh_library()
        self.preview_view.set_project(directory, project)
        self.animations_view.set_project(directory, project)
        self.export_view.set_project(directory, project)
        self.tabs.setCurrentWidget(self.fit_view)
        self.statusBar().showMessage(f"Opened {project.name}. The master image is locked.")

    def _master_size_choice(self, source, canvas_size: tuple[int, int]) -> str:
        transform = fit_inside_transform(source.size, canvas_size)
        larger = source.width > canvas_size[0] or source.height > canvas_size[1]
        box = QMessageBox(self)
        box.setWindowTitle("Master size differs from the locked canvas")
        if larger:
            box.setText(
                "The selected master image is larger than the locked project canvas. "
                "Cat Layer Studio can resize it uniformly so the whole image fits without "
                "cropping or stretching."
            )
            preview_text = "Preview resize"
        else:
            box.setText(
                "This master is smaller than the project canvas. Enlarging it may soften the "
                "artwork. Create a canvas-sized working master anyway?"
            )
            preview_text = "Preview enlargement"
        box.setInformativeText(f"Suggested resize: {transform.scale_x * 100:.2f}%")
        preview = box.addButton(preview_text, QMessageBox.ButtonRole.AcceptRole)
        keep = box.addButton("Keep original size", QMessageBox.ButtonRole.DestructiveRole)
        choose = box.addButton("Choose another image", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Cancel project creation", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is preview:
            dialog = FitPreviewDialog(
                source,
                canvas_size,
                transform,
                apply_label="Use resized master",
                keep_label="Keep original size",
                window_title="Preview master resize",
                subject_label="Original master",
                parent=self,
            )
            dialog.exec()
            if dialog.choice == "apply":
                return "normalise"
            if dialog.choice != "keep":
                return "cancel"
            clicked = keep
        if clicked is keep:
            warning = QMessageBox.warning(
                self,
                "Keep mismatched master?",
                "A master that does not match the project canvas may be cropped in the fitting "
                "view and may not provide a reliable alignment reference.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return "keep" if warning == QMessageBox.StandardButton.Yes else "cancel"
        return "choose" if clicked is choose else "cancel"

    def view_master_details(self) -> None:
        if not self.project:
            return
        project = self.project
        original_size = (
            f"{project.master_original_size[0]} × {project.master_original_size[1]}"
            if project.master_original_size
            else "Unknown (legacy project)"
        )
        scale = (
            f"{project.master_resize_scale * 100:.2f}%"
            if project.master_resize_scale is not None
            else "Not recorded"
        )
        QMessageBox.information(
            self,
            "Master details",
            f"Original: {project.master_original_path or project.master_path}\n"
            f"Original size: {original_size}\nWorking master: {project.master_path}\n"
            f"Locked canvas: {project.canvas_width} × {project.canvas_height}\n"
            f"Resize: {scale}",
        )

    def replace_master_dialog(self) -> None:
        if not self.project or not self.project_directory:
            return
        proceed = QMessageBox.warning(
            self,
            "Replace the locked master?",
            "Replacing the master changes the positional reference for every component in this "
            "project. Existing fitted parts may need to be reviewed.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if proceed != QMessageBox.StandardButton.Ok:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose replacement master", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if not filename:
            return
        try:
            loaded = load_image(Path(filename))
            normalise = False
            if loaded.original_size != self.project.canvas_size:
                choice = self._master_size_choice(loaded.image, self.project.canvas_size)
                if choice == "choose":
                    self.replace_master_dialog()
                    return
                if choice == "cancel":
                    return
                normalise = choice == "normalise"
            replace_master(
                self.project_directory,
                self.project,
                Path(filename),
                normalise_master=normalise,
            )
            self._activate_project(self.project_directory, self.project)
        except (OSError, ValueError, RuntimeError, MemoryError) as error:
            QMessageBox.critical(self, "Master could not be replaced", str(error))

    def resize_current_master(self) -> None:
        if not self.project or not self.project_directory:
            return
        current = self.project.resolve(self.project_directory, self.project.master_path)
        try:
            loaded = load_image(current)
            if loaded.original_size == self.project.canvas_size:
                QMessageBox.information(
                    self, "Master already fits", "The active master already matches the canvas."
                )
                return
            dialog = FitPreviewDialog(
                loaded.image,
                self.project.canvas_size,
                fit_inside_transform(loaded.original_size, self.project.canvas_size),
                apply_label="Use resized master",
                window_title="Preview master resize",
                subject_label="Current master",
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            replace_master(self.project_directory, self.project, current, normalise_master=True)
            self._activate_project(self.project_directory, self.project)
        except (OSError, ValueError, RuntimeError, MemoryError) as error:
            QMessageBox.critical(self, "Master could not be resized", str(error))

    def _candidate_imported(self, source_name: str) -> None:
        if not self.project or not self.project_directory:
            return
        source = Path(source_name)
        try:
            # A source already inside this project is being reopened, not imported again.
            source.resolve().relative_to((self.project_directory / "source").resolve())
            relative = source.relative_to(self.project_directory).as_posix()
        except ValueError:
            relative = import_source(self.project_directory, source)
        self.project.candidate = CandidateState(relative, self.fit_view.controls.transform())
        save_project(self.project_directory, self.project)
        self.statusBar().showMessage("Part imported. The original file remains unchanged.")

    def _transform_committed(self, transform: Transform) -> None:
        if not self.project or not self.project_directory or not self.project.candidate:
            return
        self.project.candidate.transform = transform
        save_project(self.project_directory, self.project)

    def _component_exported(self, _path: str) -> None:
        self._refresh_library()
        self.statusBar().showMessage("Component saved to the reusable library.")

    def _refresh_library(self) -> None:
        if self.project_directory and self.project:
            self.library.set_project(self.project_directory, self.project)

    def _add_component_to_assembly(self, path: str) -> None:
        self.preview_view.add_component(Path(path))
        self._refresh_library()
        self.tabs.setCurrentWidget(self.preview_view)
        self.statusBar().showMessage(
            "Layer added. Put it in order, fine-tune placement, then check its movement joint."
        )

    def _save_assembly(self) -> None:
        if not self.project_directory or not self.project:
            return
        save_project(self.project_directory, self.project)
        self.library.refresh()
