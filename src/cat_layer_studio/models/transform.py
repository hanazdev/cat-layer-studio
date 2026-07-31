from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any


@dataclass(slots=True)
class Transform:
    """A non-destructive candidate transform in canonical canvas coordinates."""

    x: float = 0.0
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation_degrees: float = 0.0
    pivot_x: float | None = None
    pivot_y: float | None = None

    def validate(self) -> None:
        values = (self.x, self.y, self.scale_x, self.scale_y, self.rotation_degrees)
        if not all(isfinite(value) for value in values):
            raise ValueError("Transform values must be finite.")
        if self.scale_x <= 0 or self.scale_y <= 0:
            raise ValueError("Width and height must be greater than 0%.")

    @property
    def divergence_percent(self) -> float:
        return abs(self.scale_x - self.scale_y) * 100.0

    @property
    def divergence_level(self) -> str:
        divergence = self.divergence_percent
        if divergence > 10:
            return "confirmation"
        if divergence > 5:
            return "strong"
        if divergence >= 3:
            return "warning"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transform:
        return cls(**data)
