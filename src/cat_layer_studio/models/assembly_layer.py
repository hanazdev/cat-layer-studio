from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from cat_layer_studio.constants import DEFAULT_RIG_PROFILE


@dataclass(slots=True)
class AssemblyLayer:
    id: str
    display_name: str
    texture_path: str
    slot: str
    visible: bool = True
    locked: bool = False
    z_index: int = 0
    offset_x: float = 0.0
    offset_y: float = 0.0
    opacity: float = 1.0
    attachment_joint: str | None = None
    pivot_x: float | None = None
    pivot_y: float | None = None
    tint_group: str | None = None
    rig_profile: str = DEFAULT_RIG_PROFILE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssemblyLayer:
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in allowed})
