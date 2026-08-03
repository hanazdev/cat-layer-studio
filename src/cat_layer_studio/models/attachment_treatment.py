from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class AttachmentTreatment:
    treatment_id: str
    joint_name: str
    method: str
    texture_path: str
    parent_joint: str
    z_index: int
    source_layer_ids: tuple[str, ...]
    template_ids: tuple[str, ...]
    enabled: bool = True
    generated_from: str = "existing artwork overlap"
    alpha_threshold: int = 16
    provenance_version: int = 1
    verification_status: str = "Needs automatic fix"
    verification_details: dict[str, str] | None = None
    validated_backgrounds: tuple[str, ...] = (
        "black",
        "white",
        "grey",
        "saturated magenta",
    )
    algorithm_version: int = 2
    mask_bounds: tuple[int, int, int, int] | None = None
    face_protection_cutoff_y: int | None = None
    sampled_angle_range: tuple[float, float] | None = None
    edge_coverage_result: dict[str, float | int | str] | None = None
    source_layer_hashes: dict[str, str] | None = None
    regeneration_provenance: dict[str, str | int | float] | None = None
    parent_layer_id: str | None = None
    child_layer_ids: tuple[str, ...] = ()
    transform_owner: str | None = None
    coverage_policy: str = "preserve_parent_alpha"
    protected_region_bounds: tuple[int, int, int, int] | None = None
    animation_fingerprints: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_layer_ids"] = list(self.source_layer_ids)
        value["template_ids"] = list(self.template_ids)
        value["child_layer_ids"] = list(self.child_layer_ids)
        value["validated_backgrounds"] = list(self.validated_backgrounds)
        if self.mask_bounds is not None:
            value["mask_bounds"] = list(self.mask_bounds)
        if self.sampled_angle_range is not None:
            value["sampled_angle_range"] = list(self.sampled_angle_range)
        if self.protected_region_bounds is not None:
            value["protected_region_bounds"] = list(self.protected_region_bounds)
        return value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttachmentTreatment:
        allowed = cls.__dataclass_fields__
        value = {key: item for key, item in data.items() if key in allowed}
        value["source_layer_ids"] = tuple(value.get("source_layer_ids", ()))
        value["template_ids"] = tuple(value.get("template_ids", ()))
        value["child_layer_ids"] = tuple(value.get("child_layer_ids", ()))
        value["validated_backgrounds"] = tuple(value.get("validated_backgrounds", ()))
        if value.get("mask_bounds") is not None:
            value["mask_bounds"] = tuple(value["mask_bounds"])
        if value.get("sampled_angle_range") is not None:
            value["sampled_angle_range"] = tuple(value["sampled_angle_range"])
        if value.get("protected_region_bounds") is not None:
            value["protected_region_bounds"] = tuple(value["protected_region_bounds"])
        return cls(**value)
