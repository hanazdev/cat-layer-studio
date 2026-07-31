from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cat_layer_studio.constants import DEFAULT_CANVAS, DEFAULT_RIG_PROFILE, PROJECT_FORMAT_VERSION
from cat_layer_studio.models.transform import Transform


@dataclass(slots=True)
class CandidateState:
    source_path: str
    transform: Transform = field(default_factory=Transform)
    mask_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["transform"] = self.transform.to_dict()
        return value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateState:
        value = dict(data)
        value["transform"] = Transform.from_dict(value.get("transform", {}))
        return cls(**value)


@dataclass(slots=True)
class Project:
    name: str
    master_path: str
    canvas_width: int = DEFAULT_CANVAS[0]
    canvas_height: int = DEFAULT_CANVAS[1]
    export_directory: str = "exports"
    rig_profile: str = DEFAULT_RIG_PROFILE
    key_colour: str | None = None
    candidate: CandidateState | None = None
    format_version: int = PROJECT_FORMAT_VERSION
    master_original_path: str | None = None
    master_working_path: str | None = None
    master_original_size: tuple[int, int] | None = None
    master_canvas_size: tuple[int, int] | None = None
    master_resize_scale: float | None = None
    master_normalisation_mode: str | None = None

    @property
    def canvas_size(self) -> tuple[int, int]:
        return self.canvas_width, self.canvas_height

    def resolve(self, project_directory: Path, relative_path: str) -> Path:
        path = (project_directory / relative_path).resolve()
        root = project_directory.resolve()
        if path != root and root not in path.parents:
            raise ValueError("Project path escapes the project directory.")
        return path

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.candidate:
            value["candidate"] = self.candidate.to_dict()
        return value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        value = dict(data)
        if value.get("candidate"):
            value["candidate"] = CandidateState.from_dict(value["candidate"])
        for key in ("master_original_size", "master_canvas_size"):
            if value.get(key) is not None:
                value[key] = tuple(value[key])
        # Legacy projects only have master_path. Keep that file as both references in memory.
        value.setdefault("master_original_path", value.get("master_path"))
        value.setdefault("master_working_path", value.get("master_path"))
        return cls(**value)
