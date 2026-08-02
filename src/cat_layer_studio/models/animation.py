from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

from cat_layer_studio.constants import ANIMATION_FORMAT_VERSION

AnimationValue = float | int | bool | str | tuple[float, float]


@dataclass(frozen=True, slots=True)
class AnimationKey:
    time: float
    value: AnimationValue
    transition: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if isinstance(self.value, tuple):
            value["value"] = list(self.value)
        return value


@dataclass(frozen=True, slots=True)
class GeneratedTrack:
    target_path: str
    property_name: str
    interpolation: str
    keys: tuple[AnimationKey, ...]


@dataclass(frozen=True, slots=True)
class GeneratedAnimation:
    name: str
    template_id: str
    duration: float
    loop: bool
    tracks: tuple[GeneratedTrack, ...]
    parameters: dict[str, float | int | bool | str] = field(default_factory=dict)
    required_joints: tuple[str, ...] = ()
    required_assets: tuple[str, ...] = ()


@dataclass(slots=True)
class AnimationTemplateSettings:
    template_id: str
    enabled: bool = True
    duration: float = 1.0
    loop: bool = False
    parameters: dict[str, float | int | bool | str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationTemplateSettings:
        allowed = cls.__dataclass_fields__
        return cls(**{key: deepcopy(value) for key, value in data.items() if key in allowed})


@dataclass(slots=True)
class AnimationSet:
    rig_profile: str
    templates: list[AnimationTemplateSettings] = field(default_factory=list)
    format_version: int = ANIMATION_FORMAT_VERSION
    preview_speed: float = 1.0
    preview_loop: bool = True
    compatibility_status: dict[str, str] = field(default_factory=dict)
    preview_status: dict[str, str] = field(default_factory=dict)
    export_status: dict[str, str] = field(default_factory=dict)
    last_successful_export: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rig_profile": self.rig_profile,
            "templates": [template.to_dict() for template in self.templates],
            "format_version": self.format_version,
            "preview_speed": self.preview_speed,
            "preview_loop": self.preview_loop,
            "compatibility_status": dict(self.compatibility_status),
            "preview_status": dict(self.preview_status),
            "export_status": dict(self.export_status),
            "last_successful_export": self.last_successful_export,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationSet:
        format_version = int(data.get("format_version", ANIMATION_FORMAT_VERSION))
        if format_version not in {1, ANIMATION_FORMAT_VERSION}:
            raise ValueError(f"Unsupported animation format version: {format_version}")
        templates = [
            AnimationTemplateSettings.from_dict(item) for item in data.get("templates", [])
        ]
        if format_version < 2:
            # Version 1 stored obsolete template defaults as though they were user choices.
            for template in templates:
                if template.template_id in {"idle_breathing", "happy_bounce"}:
                    template.parameters["head_movement"] = False
                if template.template_id == "happy_bounce":
                    template.parameters["move_ears_too"] = False
                if template.template_id.startswith("ear_twitch_"):
                    template.enabled = False
        return cls(
            rig_profile=str(data.get("rig_profile", "adult_front_sitting")),
            templates=templates,
            format_version=ANIMATION_FORMAT_VERSION,
            preview_speed=float(data.get("preview_speed", 1.0)),
            preview_loop=bool(data.get("preview_loop", True)),
            compatibility_status={}
            if format_version < 2
            else dict(data.get("compatibility_status", {})),
            preview_status={} if format_version < 2 else dict(data.get("preview_status", {})),
            export_status={} if format_version < 2 else dict(data.get("export_status", {})),
            last_successful_export=None
            if format_version < 2
            else data.get("last_successful_export"),
        )


@dataclass(slots=True)
class AnimationHistory:
    states: list[AnimationSet] = field(default_factory=list)
    index: int = -1

    def reset(self, animation_set: AnimationSet) -> None:
        self.states = [deepcopy(animation_set)]
        self.index = 0

    def commit(self, animation_set: AnimationSet) -> None:
        snapshot = deepcopy(animation_set)
        if self.index >= 0 and snapshot == self.states[self.index]:
            return
        self.states = self.states[: self.index + 1]
        self.states.append(snapshot)
        self.index += 1

    def undo(self) -> AnimationSet | None:
        if self.index <= 0:
            return None
        self.index -= 1
        return deepcopy(self.states[self.index])

    def redo(self) -> AnimationSet | None:
        if self.index + 1 >= len(self.states):
            return None
        self.index += 1
        return deepcopy(self.states[self.index])
