from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cat_layer_studio.models.project import Project
from cat_layer_studio.services.godot_export_service import (
    accept_export,
    export_godot_rig,
    rollback_export,
)
from cat_layer_studio.services.godot_verification_service import verify_godot_export
from cat_layer_studio.services.layer_validation_service import validate_assembly


class ExportView(QWidget):
    project_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.project_directory: Path | None = None
        self.project: Project | None = None
        heading = QLabel("Export a reusable Godot 4.6 cutout rig")
        heading.setStyleSheet("font-size: 20px; font-weight: 600")
        intro = QLabel(
            "Choose a Godot project and an output folder below res://. Cat Layer Studio copies "
            "textures, writes the native scene and metadata, then can ask Godot itself to load "
            "and test the rig."
        )
        intro.setWordWrap(True)
        self.godot_project = QLineEdit()
        self.output = QLineEdit("assets/cats/modular/adult_front_sitting")
        self.executable = QLineEdit()
        choose_project = QPushButton("Choose…")
        choose_executable = QPushButton("Choose…")
        choose_project.clicked.connect(self._choose_project)
        choose_executable.clicked.connect(self._choose_executable)
        project_row = QHBoxLayout()
        project_row.addWidget(self.godot_project)
        project_row.addWidget(choose_project)
        executable_row = QHBoxLayout()
        executable_row.addWidget(self.executable)
        executable_row.addWidget(choose_executable)
        form = QFormLayout()
        form.addRow("Godot project directory", project_row)
        form.addRow("Output under res://", self.output)
        form.addRow("Godot 4.6 executable (optional)", executable_row)
        self.status = QLabel("Status: Draft")
        self.status.setWordWrap(True)
        export = QPushButton("Export generic rig")
        export_verify = QPushButton("Export and verify in Godot")
        export.clicked.connect(lambda: self._export(False))
        export_verify.clicked.connect(lambda: self._export(True))
        actions = QHBoxLayout()
        actions.addWidget(export)
        actions.addWidget(export_verify)
        actions.addStretch()
        checklist = QLabel(
            "The export contains: native .tscn • separate PNG textures • preview.png • "
            "reusable AnimationLibrary • animation manifest • part catalog • playback and "
            "part-replacement APIs • verification fixture"
        )
        checklist.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self.status)
        layout.addWidget(checklist)
        layout.addStretch()

    def set_project(self, directory: Path, project: Project) -> None:
        self.project_directory = directory
        self.project = project
        self.godot_project.setText(project.godot_project_directory or "")
        self.output.setText(project.godot_output_directory)
        self.executable.setText(project.godot_executable or "")
        self.status.setText(f"Status: {project.godot_export_status}")

    def _choose_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a Godot project directory")
        if path:
            self.godot_project.setText(path)

    def _choose_executable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Godot 4.6 executable", "", "Godot executable (*.exe);;All files (*)"
        )
        if path:
            self.executable.setText(path)

    def _export(self, verify: bool) -> None:
        if not self.project or not self.project_directory:
            QMessageBox.information(
                self, "Open a project first", "Open a Cat Layer Studio project before exporting."
            )
            return
        failures = [
            result
            for result in validate_assembly(self.project_directory, self.project)
            if result.status == "Failed"
        ]
        if failures:
            QMessageBox.warning(
                self,
                "Assembly is not ready",
                "Fix these checks first: " + ", ".join(result.name for result in failures),
            )
            return
        godot_directory = Path(self.godot_project.text().strip())
        output = self.output.text().strip().replace("\\", "/").strip("/")
        if not output:
            QMessageBox.warning(self, "Choose an output folder", "Enter a folder below res://.")
            return
        result = None
        try:
            result = export_godot_rig(self.project_directory, self.project, godot_directory, output)
            self.project.godot_project_directory = str(godot_directory)
            self.project.godot_output_directory = output
            self.project.godot_executable = self.executable.text().strip() or None
            if verify:
                if not self.project.godot_executable:
                    raise ValueError(
                        "Choose the Godot 4.6 executable before requesting verification."
                    )
                script_path = result.res_scene_path.rsplit("/", 1)[0] + "/verify_rig.gd"
                verification = verify_godot_export(
                    Path(self.project.godot_executable), godot_directory, script_path
                )
                if not verification.passed:
                    rollback_export(result)
                    self.project.godot_export_status = "Godot validation failed"
                    self.status.setText(
                        f"Status: Godot validation failed\n{verification.output[-2000:]}"
                    )
                    self.project_changed.emit()
                    QMessageBox.critical(
                        self,
                        "Godot validation failed",
                        "The previous valid export was restored.\n\n" + verification.output[-2000:],
                    )
                    return
                self.project.godot_export_status = verification.message
                accept_export(result)
                if self.project.animation_set:
                    self.project.animation_set.last_successful_export = (
                        result.animation_library_path.relative_to(godot_directory).as_posix()
                    )
                message = (
                    "Godot loaded the rig and animation library, played every generated "
                    "animation, and replaced a texture during playback successfully. "
                    + (
                        "Rendered parity also passed."
                        if verification.visual_parity_verified
                        else "This dummy-renderer run checked structure and transforms only; "
                        "a rendering-capable Godot run is still required for visual parity."
                    )
                )
            else:
                self.project.godot_export_status = "Exported — unverified"
                accept_export(result)
                if self.project.animation_set:
                    self.project.animation_set.last_successful_export = (
                        result.animation_library_path.relative_to(godot_directory).as_posix()
                    )
                message = (
                    "The rig was exported. Run Export and verify in Godot before calling it "
                    "engine-verified."
                )
            self.status.setText(
                f"Status: {self.project.godot_export_status}\nScene: {result.scene_path}"
            )
            self.project_changed.emit()
            QMessageBox.information(self, self.project.godot_export_status, message)
        except (OSError, ValueError, RuntimeError) as error:
            if result is not None:
                rollback_export(result)
            self.status.setText(f"Status: export failed\n{error}")
            QMessageBox.critical(self, "Rig could not be exported", str(error))
