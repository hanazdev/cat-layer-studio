from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cat_layer_studio.constants import (
    JOINT_PLACEMENT_FORMAT_VERSION,
    PROJECT_DIRECTORIES,
    PROJECT_FORMAT_VERSION,
)
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.image_loader import load_image
from cat_layer_studio.services.joint_placement_service import ensure_joint_placements
from cat_layer_studio.services.master_service import normalise_master_to_canvas, save_png_atomic


def create_project(
    directory: Path,
    project: Project,
    master_source: Path,
    *,
    normalise_master: bool = False,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "project.json").exists():
        raise FileExistsError("A project already exists in this directory.")
    for child in PROJECT_DIRECTORIES:
        (directory / child).mkdir(exist_ok=True)
    loaded = load_image(master_source)
    master_name = (
        f"master_original{master_source.suffix.lower()}"
        if normalise_master
        else f"master{master_source.suffix.lower()}"
    )
    master_destination = directory / "source" / master_name
    if master_destination.exists():
        raise FileExistsError("A master image already exists in this project.")
    working_destination = directory / "master" / "master_canvas.png"
    created: list[Path] = []
    try:
        shutil.copy2(master_source, master_destination)
        created.append(master_destination)
        active = master_destination
        scale = 1.0
        mode = None
        if normalise_master:
            working, scale = normalise_master_to_canvas(loaded.image, project.canvas_size)
            save_png_atomic(working, working_destination)
            created.append(working_destination)
            active = working_destination
            mode = "fit_inside"
        project.master_path = active.relative_to(directory).as_posix()
        project.master_original_path = master_destination.relative_to(directory).as_posix()
        project.master_working_path = project.master_path
        project.master_original_size = loaded.original_size
        project.master_canvas_size = project.canvas_size
        project.master_resize_scale = scale
        project.master_normalisation_mode = mode
        save_project(directory, project, backup=False)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return directory / "project.json"


def replace_master(
    project_directory: Path,
    project: Project,
    master_source: Path,
    *,
    normalise_master: bool,
) -> None:
    """Import and activate a replacement while preserving the previous master and metadata."""
    loaded = load_image(master_source)
    token = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    original_name = f"master_original-{token}{master_source.suffix.lower()}"
    original = project_directory / "source" / original_name
    working = project_directory / "master" / f"master_canvas-{token}.png"
    old = project.to_dict()
    created: list[Path] = []
    try:
        shutil.copy2(master_source, original)
        created.append(original)
        active = original
        scale = 1.0
        mode = None
        if normalise_master:
            image, scale = normalise_master_to_canvas(loaded.image, project.canvas_size)
            save_png_atomic(image, working)
            created.append(working)
            active = working
            mode = "fit_inside"
        project.master_path = active.relative_to(project_directory).as_posix()
        project.master_original_path = original.relative_to(project_directory).as_posix()
        project.master_working_path = project.master_path
        project.master_original_size = loaded.original_size
        project.master_canvas_size = project.canvas_size
        project.master_resize_scale = scale
        project.master_normalisation_mode = mode
        save_project(project_directory, project, backup=True)
    except Exception:
        restored = Project.from_dict(old)
        for field_name in Project.__dataclass_fields__:
            setattr(project, field_name, getattr(restored, field_name))
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


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
    ensure_joint_placements(project)
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
    if project.format_version != PROJECT_FORMAT_VERSION:
        raise ValueError(f"Unsupported project format version: {project.format_version}")
    if project.joint_placement_format_version != JOINT_PLACEMENT_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported joint placement format version: {project.joint_placement_format_version}"
        )
    ensure_joint_placements(project)
    project.resolve(project_directory, project.master_path)
    if project.master_original_path:
        project.resolve(project_directory, project.master_original_path)
    if project.master_working_path:
        project.resolve(project_directory, project.master_working_path)
    if project.candidate:
        project.resolve(project_directory, project.candidate.source_path)
    for layer in project.assembly_layers:
        project.resolve(project_directory, layer.texture_path)
    return project_directory, project
