from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cat_layer_studio.models.project import Project
from cat_layer_studio.services.component_library_service import import_component, list_components


class ComponentLibraryView(QWidget):
    add_requested = Signal(str)
    library_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.project_directory: Path | None = None
        self.project: Project | None = None
        intro = QLabel(
            "Reusable full-canvas PNG parts. Add one to the assembly without changing or "
            "deleting its source file."
        )
        intro.setWordWrap(True)
        self.list = QListWidget()
        self.list.setIconSize(QSize(72, 72))
        add = QPushButton("Add selected to assembly")
        import_external = QPushButton("Import transparent PNG…")
        rename = QPushButton("Rename component file…")
        reveal = QPushButton("Reveal file location")
        refresh = QPushButton("Refresh library")
        add.clicked.connect(self._add)
        import_external.clicked.connect(self._import)
        rename.clicked.connect(self._rename)
        reveal.clicked.connect(self._reveal)
        refresh.clicked.connect(self.refresh)
        actions = QHBoxLayout()
        for widget in (add, import_external, rename, reveal, refresh):
            actions.addWidget(widget)
        actions.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(actions)
        layout.addWidget(self.list, 1)

    def set_project(self, directory: Path, project: Project) -> None:
        self.project_directory = directory
        self.project = project
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        if not self.project_directory or not self.project:
            self.list.addItem("Open a project to see saved components.")
            return
        used = {layer.texture_path for layer in self.project.assembly_layers}
        components = list_components(self.project_directory, used)
        if not components:
            self.list.addItem("No components yet. Export one from Fit Component or import a PNG.")
        for component in components:
            alpha = "transparent" if component.has_alpha else "no transparency"
            used_text = " • already in assembly" if component.in_assembly else ""
            item = QListWidgetItem(
                QIcon(QPixmap(str(component.path))),
                f"{component.display_name}\n{component.dimensions[0]} × "
                f"{component.dimensions[1]} • {alpha} • suggested: "
                f"{component.suggested_slot}{used_text}",
            )
            item.setData(Qt.ItemDataRole.UserRole, str(component.path))
            self.list.addItem(item)

    def _selected_path(self) -> Path | None:
        item = self.list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return Path(value) if value else None

    def _add(self) -> None:
        if path := self._selected_path():
            self.add_requested.emit(str(path))

    def _import(self) -> None:
        if not self.project_directory:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Import transparent PNG", "", "PNG (*.png)")
        if not filename:
            return
        try:
            path = import_component(self.project_directory, Path(filename))
            self.refresh()
            self.library_changed.emit()
            self.add_requested.emit(str(path))
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Component could not be imported", str(error))

    def _rename(self) -> None:
        path = self._selected_path()
        if not path:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename display file", "New filename", text=path.stem
        )
        if not accepted or not name.strip():
            return
        destination = path.with_name(name.strip() + ".png")
        if destination.exists():
            QMessageBox.warning(self, "Name already used", "Choose a different component name.")
            return
        path.rename(destination)
        metadata = path.with_suffix(".json")
        if metadata.exists():
            metadata.rename(destination.with_suffix(".json"))
        if self.project and self.project_directory:
            old = path.relative_to(self.project_directory).as_posix()
            new = destination.relative_to(self.project_directory).as_posix()
            for layer in self.project.assembly_layers:
                if layer.texture_path == old:
                    layer.texture_path = new
        self.refresh()
        self.library_changed.emit()

    def _reveal(self) -> None:
        if path := self._selected_path():
            os.startfile(path.parent)  # noqa: S606
