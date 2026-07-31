from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
from cat_layer_studio.services.project_service import (
    create_project,
    import_source,
    load_project,
    save_project,
)
from cat_layer_studio.views.fit_component_view import FitComponentView


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
        self.tabs.addTab(self.project_view, "1. Project")
        self.tabs.addTab(self.fit_view, "2. Fit Component")
        self.tabs.addTab(self.library, "3. Component Library")
        self.tabs.addTab(self.preview_view, "4. Modular Preview")
        self.tabs.addTab(self.export_view, "5. Export")
        self.setCentralWidget(self.tabs)

        self.fit_view.candidate_imported.connect(self._candidate_imported)
        self.fit_view.transform_committed.connect(self._transform_committed)
        self.fit_view.component_exported.connect(self._component_exported)
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
        self.project_status = QLabel("No project is open.")
        self.project_status.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addLayout(actions)
        layout.addWidget(self.project_status)
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
            create_project(Path(directory), project, Path(master))
            self._activate_project(Path(directory), project)
        except (OSError, ValueError) as error:
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
        except (OSError, ValueError) as error:
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
        self._refresh_library()
        self.tabs.setCurrentWidget(self.fit_view)
        self.statusBar().showMessage(f"Opened {project.name}. The master image is locked.")

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
        self.library.clear()
        if not self.project_directory:
            self.library.addItem("Open a project to see saved components.")
            return
        files = sorted((self.project_directory / "components").glob("*.png"))
        if not files:
            self.library.addItem("No components saved yet. Finish a fit and export one.")
        for path in files:
            self.library.addItem(path.name)
