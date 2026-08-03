from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class JointPlacement:
    joint_name: str
    x: float
    y: float
    source: str = "suggested"
    approved: bool = False
    suggestion_x: float | None = None
    suggestion_y: float | None = None
    confidence: str = "unknown"
    safe_rotation_min: float | None = None
    safe_rotation_max: float | None = None
    validation_status: str = "not_checked"
    suggestion_reason: str = ""
    last_approved_x: float | None = None
    last_approved_y: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JointPlacement:
        allowed = cls.__dataclass_fields__
        value = {key: deepcopy(item) for key, item in data.items() if key in allowed}
        value["x"] = float(value["x"])
        value["y"] = float(value["y"])
        if value.get("approved"):
            value.setdefault("last_approved_x", value["x"])
            value.setdefault("last_approved_y", value["y"])
            if value.get("last_approved_x") is None:
                value["last_approved_x"] = value["x"]
            if value.get("last_approved_y") is None:
                value["last_approved_y"] = value["y"]
        return cls(**value)


@dataclass(slots=True)
class JointPlacementHistory:
    states: list[list[JointPlacement]] = field(default_factory=list)
    index: int = -1

    def reset(self, placements: list[JointPlacement]) -> None:
        self.states = [deepcopy(placements)]
        self.index = 0

    def commit(self, placements: list[JointPlacement]) -> None:
        state = deepcopy(placements)
        if self.index >= 0 and state == self.states[self.index]:
            return
        self.states = self.states[: self.index + 1]
        self.states.append(state)
        self.index += 1

    def undo(self) -> list[JointPlacement] | None:
        if self.index <= 0:
            return None
        self.index -= 1
        return deepcopy(self.states[self.index])

    def redo(self) -> list[JointPlacement] | None:
        if self.index + 1 >= len(self.states):
            return None
        self.index += 1
        return deepcopy(self.states[self.index])


@dataclass(slots=True)
class MovementCalibrationSession:
    """A reversible, animation-specific movement setup transaction."""

    animation_template_id: str
    joint_name: str
    stationary_parent_joint: str
    original_joint_placement: JointPlacement
    working_joint_placement: JointPlacement
    original_animation_parameters: dict[str, Any]
    requested_range: tuple[float, float] | None
    project_state_at_start: dict[str, Any]
    dirty: bool = False
    saved: bool = False
