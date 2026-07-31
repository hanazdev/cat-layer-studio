from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cat_layer_studio.constants import PROJECT_DIRECTORIES
from cat_layer_studio.models.project import Project


def create_project(directory: Path, project: Project, master_source: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for child in PROJECT_DIRECTORIES:
        (directory / child).mkdir(exist_ok=True)
    master_name = f"master{master_source.suffix.lower()}"
    master_destination = directory / "source" / master_name
    if master_destination.exists():
        raise FileExistsError("A master image already exists in this project.")
    shutil.copy2(master_source, master_destination)
    project.master_path = master_destination.relative_to(directory).as_posix()
    save_project(directory, project, backup=False)
    return directory / "project.json"


def import_source(project_directory: Path, source: Path, label: str = "candidate") -> str:
    destination_directory = project_directory / "source"
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination = destination_directory / f"{label}{source.suffix.lower()}"
    counter = 2
    while destination.exists():
        destination = destination_directory / f"{label}_{counter}{source.suffix.lower()}"
        counter += 1
    shutil.copy2(source, destination)
    return destination.relative_to(project_directory).as_posix()


def save_project(project_directory: Path, project: Project, *, backup: bool = True) -> None:
    project_path = project_directory / "project.json"
    if backup and project_path.exists():
        backup_directory = project_directory / "backups"
        backup_directory.mkdir(exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        shutil.copy2(project_path, backup_directory / f"project-{timestamp}.json")
    payload = json.dumps(project.to_dict(), indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".project-", suffix=".json.tmp", dir=project_directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, project_path)
    finally:
        temporary.unlink(missing_ok=True)


def load_project(project_file: Path) -> tuple[Path, Project]:
    project_directory = project_file.parent.resolve()
    data = json.loads(project_file.read_text(encoding="utf-8"))
    project = Project.from_dict(data)
    if project.format_version != 1:
        raise ValueError(f"Unsupported project format version: {project.format_version}")
    project.resolve(project_directory, project.master_path)
    if project.candidate:
        project.resolve(project_directory, project.candidate.source_path)
    return project_directory, project
