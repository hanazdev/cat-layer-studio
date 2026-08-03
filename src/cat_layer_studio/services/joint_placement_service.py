from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from cat_layer_studio.models.joint_placement import JointPlacement
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import RigJoint, RigTemplate, get_rig_template

ALPHA_THRESHOLD = 32
MIN_COMPONENT_PIXELS = 16


@dataclass(frozen=True, slots=True)
class PlacementSuggestion:
    x: float
    y: float
    confidence: str
    reason: str
    overlap_pixels: int
    used_fallback: bool


@dataclass(frozen=True, slots=True)
class MovementDiagnostic:
    joint_name: str
    value: float
    status: str
    message: str
    overlap_ratio: float
    outside_pixels: int = 0


@dataclass(frozen=True, slots=True)
class MovementPreparationResult:
    prepared: tuple[str, ...]
    needs_review: tuple[str, ...]
    missing_artwork: tuple[str, ...]
    not_supported: tuple[str, ...]

    @property
    def summary(self) -> str:
        return (
            "Movement preparation complete\n\n"
            f"{len(self.prepared)} movements prepared automatically\n"
            f"{len(self.needs_review)} movements need review\n"
            f"{len(self.missing_artwork)} animations are missing artwork\n"
            f"{len(self.not_supported)} animations are not supported by this rig"
        )


def _joint(template: RigTemplate, joint_name: str) -> RigJoint:
    try:
        return next(item for item in template.joints if item.name == joint_name)
    except StopIteration as error:
        raise KeyError(f"Unknown joint: {joint_name}") from error


def placement_for(project: Project, joint_name: str) -> JointPlacement | None:
    return next((item for item in project.joint_placements if item.joint_name == joint_name), None)


def ensure_joint_placements(project: Project) -> list[JointPlacement]:
    """Migrate legacy projects without treating old per-layer defaults as user approval."""
    template = get_rig_template(project.rig_profile)
    known = {item.joint_name for item in project.joint_placements}
    for joint in template.joints:
        if joint.name not in known:
            project.joint_placements.append(
                JointPlacement(
                    joint.name,
                    float(joint.suggested_pivot[0]),
                    float(joint.suggested_pivot[1]),
                    source="template",
                    validation_status="not_checked",
                )
            )
    return project.joint_placements


def resolve_joint_placement(project: Project, joint_name: str) -> tuple[float, float]:
    """The sole placement resolver: approved, generated suggestion, rig fallback."""
    placement = placement_for(project, joint_name)
    if placement is not None and placement.approved:
        return float(placement.x), float(placement.y)
    if (
        placement is not None
        and placement.suggestion_x is not None
        and placement.suggestion_y is not None
    ):
        return float(placement.suggestion_x), float(placement.suggestion_y)
    template = get_rig_template(project.rig_profile)
    pivot = _joint(template, joint_name).suggested_pivot
    return float(pivot[0]), float(pivot[1])


def resolved_joint_placements(project: Project) -> dict[str, tuple[float, float]]:
    template = get_rig_template(project.rig_profile)
    return {joint.name: resolve_joint_placement(project, joint.name) for joint in template.joints}


def template_joint_placement(project: Project, joint_name: str) -> tuple[float, float]:
    return _joint(get_rig_template(project.rig_profile), joint_name).suggested_pivot


def _descendants(template: RigTemplate, joint_name: str) -> set[str]:
    result = {joint_name}
    changed = True
    while changed:
        changed = False
        for joint in template.joints:
            if joint.parent in result and joint.name not in result:
                result.add(joint.name)
                changed = True
    return result


def _alpha_for_layer(project_directory: Path, project: Project, layer) -> np.ndarray:
    with Image.open(project.resolve(project_directory, layer.texture_path)) as opened:
        alpha = opened.convert("RGBA").getchannel("A")
    if alpha.size != project.canvas_size:
        raise ValueError(f"{layer.display_name} does not match the project canvas.")
    moved = alpha.transform(
        project.canvas_size,
        Image.Transform.AFFINE,
        (1, 0, -layer.offset_x, 0, 1, -layer.offset_y),
        resample=Image.Resampling.BILINEAR,
    )
    return np.asarray(moved, dtype=np.uint8)


def attachment_masks(
    project_directory: Path, project: Project, joint_name: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return filtered parent, child-hierarchy and overlap masks in assembled coordinates."""
    template = get_rig_template(project.rig_profile)
    joint = _joint(template, joint_name)
    shape = (project.canvas_height, project.canvas_width)
    parent = np.zeros(shape, dtype=np.uint8)
    child = np.zeros(shape, dtype=np.uint8)
    child_joints = _descendants(template, joint_name)
    for layer in project.assembly_layers:
        attached = layer.attachment_joint or template.attachment_map.get(layer.slot, "Root")
        if not layer.visible:
            continue
        alpha = _alpha_for_layer(project_directory, project, layer)
        if attached in child_joints:
            child = np.maximum(child, alpha)
        elif attached == joint.parent:
            parent = np.maximum(parent, alpha)
    return (
        parent >= ALPHA_THRESHOLD,
        child >= ALPHA_THRESHOLD,
        (parent >= ALPHA_THRESHOLD) & (child >= ALPHA_THRESHOLD),
    )


def _components(mask: np.ndarray) -> list[np.ndarray]:
    height, width = mask.shape
    seen = np.zeros(mask.shape, dtype=bool)
    components: list[np.ndarray] = []
    for y, x in np.argwhere(mask):
        if seen[y, x]:
            continue
        stack = [(int(y), int(x))]
        seen[y, x] = True
        pixels: list[tuple[int, int]] = []
        while stack:
            py, px = stack.pop()
            pixels.append((py, px))
            for ny, nx in ((py - 1, px), (py + 1, px), (py, px - 1), (py, px + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        components.append(np.asarray(pixels, dtype=np.int32))
    return components


def _nearest_pixel(pixels: np.ndarray, hint: tuple[float, float]) -> tuple[float, float]:
    centre = pixels.mean(axis=0)
    cy, cx = int(round(float(centre[0]))), int(round(float(centre[1])))
    if np.any(np.all(pixels == (cy, cx), axis=1)):
        return float(cx), float(cy)
    distances = (pixels[:, 1] - centre[1]) ** 2 + (pixels[:, 0] - centre[0]) ** 2
    y, x = pixels[int(np.argmin(distances))]
    return float(x), float(y)


def suggest_joint_placement(
    project_directory: Path, project: Project, joint_name: str
) -> PlacementSuggestion:
    template = get_rig_template(project.rig_profile)
    fallback = _joint(template, joint_name).suggested_pivot
    parent, child, overlap = attachment_masks(project_directory, project, joint_name)
    substantial = [item for item in _components(overlap) if len(item) >= MIN_COMPONENT_PIXELS]
    if substantial:
        # Area is the primary signal; the semantic template hint breaks close ties only.
        largest = max(len(item) for item in substantial)
        plausible = [item for item in substantial if len(item) >= largest * 0.35]
        component = min(
            plausible,
            key=lambda item: (
                -len(item) + math.dist((item[:, 1].mean(), item[:, 0].mean()), fallback) * 0.05
            ),
        )
        x, y = _nearest_pixel(component, fallback)
        fraction = len(component) / max(1, int(np.count_nonzero(child)))
        confidence = (
            "high"
            if len(component) >= 128 and fraction >= 0.005 and len(plausible) == 1
            else "medium"
        )
        reason = (
            f"Inside the substantial {joint_name}/{_joint(template, joint_name).parent} "
            f"artwork overlap ({len(component)} opaque pixels)."
        )
        return PlacementSuggestion(x, y, confidence, reason, len(component), False)
    parent_pixels = np.argwhere(parent)
    child_pixels = np.argwhere(child)
    if len(parent_pixels) and len(child_pixels):
        # Boundary approximation, deliberately low confidence.
        sample_parent = parent_pixels[:: max(1, len(parent_pixels) // 2000)]
        sample_child = child_pixels[:: max(1, len(child_pixels) // 2000)]
        best = (float("inf"), fallback)
        for py, px in sample_parent:
            distances = (sample_child[:, 0] - py) ** 2 + (sample_child[:, 1] - px) ** 2
            cy, cx = sample_child[int(np.argmin(distances))]
            distance = float(np.min(distances))
            if distance < best[0]:
                best = (distance, ((float(px) + float(cx)) / 2, (float(py) + float(cy)) / 2))
        return PlacementSuggestion(
            best[1][0],
            best[1][1],
            "low",
            "No substantial overlap; nearest opposing boundaries used.",
            0,
            True,
        )
    return PlacementSuggestion(
        float(fallback[0]),
        float(fallback[1]),
        "low",
        "Artwork is missing; rig-template fallback used.",
        0,
        True,
    )


def update_suggestion(project_directory: Path, project: Project, joint_name: str) -> JointPlacement:
    ensure_joint_placements(project)
    suggestion = suggest_joint_placement(project_directory, project, joint_name)
    placement = placement_for(project, joint_name)
    assert placement is not None
    placement.suggestion_x = suggestion.x
    placement.suggestion_y = suggestion.y
    placement.confidence = suggestion.confidence
    placement.suggestion_reason = suggestion.reason
    if not placement.approved:
        placement.x, placement.y = suggestion.x, suggestion.y
        placement.source = "suggested"
    return placement


def set_joint_point(project: Project, joint_name: str, x: float, y: float) -> JointPlacement:
    ensure_joint_placements(project)
    placement = placement_for(project, joint_name)
    assert placement is not None
    placement.x, placement.y = float(x), float(y)
    placement.source = "user"
    placement.approved = False
    placement.validation_status = "not_checked"
    placement.safe_rotation_min = placement.safe_rotation_max = None
    project.animation_verification_valid = False
    project.godot_export_status = "Needs regeneration"
    if project.animation_set:
        project.animation_set.last_successful_export = None
    return placement


def reset_joint_to_suggestion(project: Project, joint_name: str) -> JointPlacement:
    placement = placement_for(project, joint_name)
    if placement is None or placement.suggestion_x is None or placement.suggestion_y is None:
        raise ValueError(f"No generated suggestion is available for {joint_name}.")
    return set_joint_point(project, joint_name, placement.suggestion_x, placement.suggestion_y)


def reset_joint_to_template(project: Project, joint_name: str) -> JointPlacement:
    x, y = template_joint_placement(project, joint_name)
    placement = set_joint_point(project, joint_name, x, y)
    placement.source = "template"
    return placement


def accept_joint_placement(project: Project, joint_name: str) -> JointPlacement:
    placement = placement_for(project, joint_name)
    if placement is None:
        raise ValueError(f"No movement point exists for {joint_name}.")
    placement.approved = True
    placement.last_approved_x = placement.x
    placement.last_approved_y = placement.y
    placement.source = "user" if placement.source != "template" else "template"
    return placement


def restore_last_approved_placement(project: Project, joint_name: str) -> JointPlacement:
    placement = placement_for(project, joint_name)
    if placement is None or placement.last_approved_x is None or placement.last_approved_y is None:
        raise ValueError(f"No previously approved movement point exists for {joint_name}.")
    placement.x, placement.y = placement.last_approved_x, placement.last_approved_y
    placement.approved = True
    placement.source = "user"
    return placement


def prepare_movements_automatically(
    project_directory: Path, project: Project
) -> MovementPreparationResult:
    """Prepare all enabled independent movements, preserving every user-approved point."""
    from cat_layer_studio.services.animation_service import (
        compatibility_message,
        required_movement_joints,
    )

    if project.animation_set is None:
        return MovementPreparationResult((), (), (), ())
    prepared: set[str] = set()
    review: set[str] = set()
    missing: set[str] = set()
    unsupported: set[str] = set()
    for settings in project.animation_set.templates:
        message = compatibility_message(settings, project)
        if message:
            (unsupported if settings.template_id.startswith("ear_twitch") else missing).add(
                settings.template_id
            )
            continue
        if not settings.enabled:
            continue
        for joint_name in required_movement_joints(settings):
            placement = placement_for(project, joint_name)
            if placement is not None and placement.approved:
                minimum, maximum = find_safe_rotation_range(project_directory, project, joint_name)
                if minimum < 0 < maximum:
                    placement.validation_status = "valid"
                    prepared.add(joint_name)
                else:
                    placement.validation_status = "needs_attention"
                    review.add(joint_name)
                continue
            placement = update_suggestion(project_directory, project, joint_name)
            minimum, maximum = find_safe_rotation_range(project_directory, project, joint_name)
            seam_safe = minimum < 0 < maximum
            if placement.confidence == "high" and seam_safe:
                placement.validation_status = "valid"
                placement.approved = True
                placement.source = "automatic"
                placement.last_approved_x = placement.x
                placement.last_approved_y = placement.y
                prepared.add(joint_name)
            else:
                placement.validation_status = "valid" if seam_safe else "needs_attention"
                review.add(joint_name)
    project.animation_verification_valid = False
    return MovementPreparationResult(
        tuple(sorted(prepared)),
        tuple(sorted(review)),
        tuple(sorted(missing)),
        tuple(sorted(unsupported)),
    )


def _rotate_mask(mask: np.ndarray, angle: float, pivot: tuple[float, float]) -> np.ndarray:
    image = Image.fromarray((mask * 255).astype(np.uint8), "L")
    return (
        np.asarray(
            image.rotate(-angle, center=pivot, resample=Image.Resampling.BILINEAR), dtype=np.uint8
        )
        >= ALPHA_THRESHOLD
    )


def inspect_joint_movement(
    project_directory: Path, project: Project, joint_name: str, angle: float
) -> MovementDiagnostic:
    parent, child, rest_overlap = attachment_masks(project_directory, project, joint_name)
    pivot = resolve_joint_placement(project, joint_name)
    moved = _rotate_mask(child, angle, pivot)
    overlap = parent & moved
    base = max(1, int(np.count_nonzero(rest_overlap)))
    ratio = int(np.count_nonzero(overlap)) / base
    plausible = (
        bool(rest_overlap[int(round(pivot[1])), int(round(pivot[0]))])
        if (
            0 <= round(pivot[0]) < project.canvas_width
            and 0 <= round(pivot[1]) < project.canvas_height
        )
        else False
    )
    if not plausible:
        status = "warning"
        message = (
            f"{joint_name} movement needs attention. The movement point is outside the "
            "substantial attachment overlap."
        )
    elif ratio < 0.55:
        status = "warning"
        message = (
            f"{joint_name} movement needs attention. A transparent gap appears near the "
            f"attachment at {angle:+g}°."
        )
    else:
        status = "ok"
        message = f"{joint_name} attachment remains connected at {angle:+g}°."
    return MovementDiagnostic(joint_name, angle, status, message, ratio)


def find_safe_rotation_range(
    project_directory: Path,
    project: Project,
    joint_name: str,
    *,
    minimum: float = -15.0,
    maximum: float = 15.0,
    step: float = 0.5,
) -> tuple[float, float]:
    values = np.arange(minimum, maximum + step / 2, step)
    safe = [
        float(value)
        for value in values
        if inspect_joint_movement(project_directory, project, joint_name, float(value)).status
        == "ok"
    ]
    negative = [value for value in safe if value <= 0]
    positive = [value for value in safe if value >= 0]
    result = (min(negative, default=0.0), max(positive, default=0.0))
    placement = placement_for(project, joint_name)
    if placement is None:
        raise ValueError(f"No movement point exists for {joint_name}.")
    placement.safe_rotation_min, placement.safe_rotation_max = result
    placement.validation_status = "valid" if result != (0.0, 0.0) else "needs_attention"
    project.animation_verification_valid = False
    return result
