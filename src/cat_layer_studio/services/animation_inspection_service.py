from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

from cat_layer_studio.models.animation import GeneratedAnimation
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.animation_service import (
    inspect_animation,
    maximum_extent_times,
    sample_track,
)
from cat_layer_studio.services.composition_service import (
    composite_animation_frame,
    composite_assembly,
)
from cat_layer_studio.services.joint_placement_service import attachment_masks
from cat_layer_studio.services.rig_hierarchy_service import evaluate_joint_matrices


@dataclass(frozen=True, slots=True)
class RenderedAttachmentDiagnostic:
    status: str
    message: str
    opening_pixels: int = 0
    fringe_pixels: int = 0


def inspect_rendered_attachment(
    project_directory: Path,
    project: Project,
    animation: GeneratedAnimation,
    joint_name: str,
    time: float,
) -> RenderedAttachmentDiagnostic:
    """Inspect the production-rendered local join instead of a global pixel delta."""
    template = get_rig_template(project.rig_profile)
    joint = next(item for item in template.joints if item.name == joint_name)
    rest_world, animated_world = evaluate_joint_matrices(project, animation, time)
    child_delta = animated_world[joint_name] @ rest_world[joint_name].inverse()
    if joint.parent:
        parent_delta = animated_world[joint.parent] @ rest_world[joint.parent].inverse()
        if child_delta == parent_delta:
            return RenderedAttachmentDiagnostic("ok", "No visible attachment gap detected")
    try:
        _parent, _child, overlap = attachment_masks(project_directory, project, joint_name)
    except ValueError:
        return RenderedAttachmentDiagnostic(
            "unknown", "Attachment cannot be validated automatically"
        )
    ys, xs = np.where(overlap)
    if not len(xs):
        return RenderedAttachmentDiagnostic(
            "unknown", "Attachment cannot be validated automatically"
        )
    pad = 4
    x0, x1 = max(0, int(xs.min()) - pad), min(project.canvas_width, int(xs.max()) + pad + 1)
    y0, y1 = max(0, int(ys.min()) - pad), min(project.canvas_height, int(ys.max()) + pad + 1)
    rest = np.asarray(composite_assembly(project_directory, project), dtype=np.int16)[y0:y1, x0:x1]
    frame = np.asarray(
        composite_animation_frame(project_directory, project, animation, time), dtype=np.int16
    )[y0:y1, x0:x1]
    overlap_roi = overlap[y0:y1, x0:x1]
    opening = overlap_roi & (rest[..., 3] >= 32) & (frame[..., 3] < 8)
    opening_pixels = int(np.count_nonzero(opening))
    if opening_pixels:
        angle = next(
            (
                float(value)
                for track in animation.tracks
                if track.target_path.endswith(joint_name) and track.property_name == "rotation"
                for value in [sample_track(track, time)]
                if isinstance(value, (int, float))
            ),
            0.0,
        )
        return RenderedAttachmentDiagnostic(
            "gap",
            f"Transparent opening detected at {np.degrees(angle):+.1f}°",
            opening_pixels,
        )
    edge = (frame[..., 3] > 0) & (frame[..., 3] < 96)
    colour_change = np.max(np.abs(frame[..., :3] - rest[..., :3]), axis=2) > 80
    fringe_pixels = int(np.count_nonzero(edge & colour_change & overlap_roi))
    if fringe_pixels:
        return RenderedAttachmentDiagnostic(
            "fringe", "Possible resampling fringe detected", 0, fringe_pixels
        )
    return RenderedAttachmentDiagnostic("ok", "No visible attachment gap detected")


def _alpha_area(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[9:])


def _edge_has_pixels(image: Image.Image) -> bool:
    alpha = image.getchannel("A")
    width, height = alpha.size
    return any(
        alpha.getpixel(point) > 8
        for point in (
            *((x, 0) for x in range(width)),
            *((x, height - 1) for x in range(width)),
            *((0, y) for y in range(height)),
            *((width - 1, y) for y in range(height)),
        )
    )


def inspect_animation_frames(
    project_directory: Path,
    project: Project,
    animation: GeneratedAnimation,
) -> list[str]:
    """Run conservative clipping/overlap checks at keys and maximum motion extents."""
    warnings = inspect_animation(project, animation)
    rest = composite_assembly(project_directory, project)
    rest_area = max(1, _alpha_area(rest))
    rest_touches_edge = _edge_has_pixels(rest)
    key_times = {key.time for track in animation.tracks for key in track.keys}
    sample_times = sorted({0.0, animation.duration, *key_times, *maximum_extent_times(animation)})
    measurable_change = False
    for time in sample_times:
        frame = composite_animation_frame(project_directory, project, animation, time)
        if ImageChops.difference(rest, frame).getbbox() is not None:
            measurable_change = True
        if _edge_has_pixels(frame) and not rest_touches_edge:
            warnings.append(
                f"{animation.name} needs attention. Visible pixels reach the canvas edge at "
                f"{time:.2f} seconds; inspect this extent for clipping."
            )
            break
        for joint_name in animation.required_joints:
            diagnostic = inspect_rendered_attachment(
                project_directory, project, animation, joint_name, time
            )
            if diagnostic.status in {"gap", "fringe"}:
                warnings.append(f"{animation.name} — {diagnostic.message}")
                break
        area_change = abs(_alpha_area(frame) - rest_area) / rest_area
        if area_change > 0.2:
            warnings.append(
                f"{animation.name} needs attention. Overlap changes sharply at {time:.2f} "
                "seconds; inspect the attachment seams or reduce the movement amount."
            )
            break
    if animation.template_id == "idle_breathing" and not measurable_change:
        warnings.append(
            "Idle breathing — Preview did not show measurable movement. "
            "Try Emphasise movement for checking. Export is blocked until the preview "
            "confirms movement."
        )
    return warnings
