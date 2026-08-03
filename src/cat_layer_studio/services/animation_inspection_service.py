from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from cat_layer_studio.models.animation import GeneratedAnimation
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.alpha_transform_service import (
    normalise_rgba_for_transform,
    transform_premultiplied_rgba,
)
from cat_layer_studio.services.animation_service import (
    inspect_animation,
    maximum_extent_times,
    sample_track,
)
from cat_layer_studio.services.attachment_treatment_service import (
    attachment_treatment_depth_is_valid,
    enabled_attachment_treatment,
    largest_attachment_component,
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
    boundary_coverage: float = 0.0
    largest_uncovered_component: int = 0
    high_contrast_edge_length: int = 0
    coverage_deficit_pixels: int = 0


@dataclass(frozen=True, slots=True)
class BreathingPerceptualMetrics:
    torso_width_change: int
    shoulder_height_change: int
    lowest_paw_y_change: int

    @property
    def passed(self) -> bool:
        return (
            self.torso_width_change >= 4
            and self.shoulder_height_change >= 4
            and self.lowest_paw_y_change <= 1
        )


def _largest_component_size(mask: np.ndarray) -> int:
    remaining = mask.copy()
    largest = 0
    height, width = remaining.shape
    for start_y, start_x in zip(*np.where(remaining), strict=True):
        if not remaining[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]
        remaining[start_y, start_x] = False
        size = 0
        while stack:
            y, x = stack.pop()
            size += 1
            for next_y, next_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= next_y < height and 0 <= next_x < width and remaining[next_y, next_x]:
                    remaining[next_y, next_x] = False
                    stack.append((next_y, next_x))
        largest = max(largest, size)
    return largest


def _transformed_layer_alpha(
    project_directory: Path,
    project: Project,
    animation: GeneratedAnimation,
    time: float,
    slot: str | None = None,
    layer_id: str | None = None,
) -> np.ndarray:
    layer = next(
        item
        for item in project.assembly_layers
        if (layer_id is not None and item.id == layer_id)
        or (layer_id is None and item.slot == slot)
    )
    with Image.open(project.resolve(project_directory, layer.texture_path)) as opened:
        source = normalise_rgba_for_transform(opened)
    placed = transform_premultiplied_rgba(
        source, (1, 0, -layer.offset_x, 0, 1, -layer.offset_y), project.canvas_size
    )
    rest_world, animated_world = evaluate_joint_matrices(project, animation, time)
    joint_name = layer.attachment_joint or "Root"
    delta = animated_world[joint_name] @ rest_world[joint_name].inverse()
    inverse = delta.inverse()
    rendered = transform_premultiplied_rgba(
        placed,
        (inverse.xx, inverse.yx, inverse.tx, inverse.xy, inverse.yy, inverse.ty),
        project.canvas_size,
    )
    return np.asarray(rendered.getchannel("A"), dtype=np.uint8)


def _attachment_boundary_metrics(
    project_directory: Path,
    project: Project,
    animation: GeneratedAnimation,
    time: float,
    overlap: np.ndarray,
    frame: np.ndarray,
    joint_name: str,
) -> tuple[float, int, int, int, tuple[int, int, int, int] | None]:
    treatment = enabled_attachment_treatment(project, joint_name)
    ys, xs = np.where(overlap)
    if not len(xs):
        return 0.0, 0, 0, 0, None
    cleaned = (
        Image.fromarray((overlap * 255).astype(np.uint8), "L")
        .filter(ImageFilter.MinFilter(5))
        .filter(ImageFilter.MaxFilter(5))
    )
    component = largest_attachment_component(np.asarray(cleaned) >= 128)
    child_layer_id = (
        treatment.source_layer_ids[1] if treatment and len(treatment.source_layer_ids) > 1 else None
    )
    if child_layer_id is None:
        template = get_rig_template(project.rig_profile)
        child_layer_id = next(
            layer.id
            for layer in project.assembly_layers
            if (layer.attachment_joint or template.attachment_map.get(layer.slot, "Root"))
            == joint_name
        )
    child_alpha = _transformed_layer_alpha(
        project_directory, project, animation, time, layer_id=child_layer_id
    )
    eroded = np.asarray(Image.fromarray(child_alpha, "L").filter(ImageFilter.MinFilter(3))) >= 32
    child_boundary = (child_alpha >= 32) & ~eroded
    protected = np.zeros_like(component)
    if treatment and treatment.protected_region_bounds:
        x0, y0, x1, y1 = treatment.protected_region_bounds
        protected[y0:y1, x0:x1] = True
    else:
        component_ys, component_xs = np.where(component)
        if len(component_xs):
            pad = 24
            x0 = max(0, int(component_xs.min()) - pad)
            x1 = min(project.canvas_width, int(component_xs.max()) + pad + 1)
            y0 = max(0, int(component_ys.min()) - pad)
            y1 = min(project.canvas_height, int(component_ys.max()) + pad + 1)
            protected[y0:y1, x0:x1] = True
    if treatment and treatment.face_protection_cutoff_y is not None:
        protected[: treatment.face_protection_cutoff_y] = False
    if treatment and treatment.parent_layer_id:
        parent_layer_id = treatment.parent_layer_id
    else:
        template = get_rig_template(project.rig_profile)
        joint = next(item for item in template.joints if item.name == joint_name)
        parent_candidates = [
            layer
            for layer in project.assembly_layers
            if (layer.attachment_joint or template.attachment_map.get(layer.slot, "Root"))
            == joint.parent
        ]
        parent_layer_id = (
            max(parent_candidates, key=lambda item: (item.z_index, item.id)).id
            if parent_candidates
            else None
        )
    parent_alpha = (
        _transformed_layer_alpha(
            project_directory, project, animation, time, layer_id=parent_layer_id
        ).astype(np.int16)
        if parent_layer_id is not None
        else np.zeros_like(child_alpha, dtype=np.int16)
    )
    relevant_boundary = child_boundary & protected & (parent_alpha >= 16)
    relevant_pixels = int(np.count_nonzero(relevant_boundary))
    if relevant_pixels == 0:
        return 1.0, 0, 0, 0, None
    treatment_alpha = np.zeros_like(child_alpha)
    treatment_preserves_depth = False
    child = next(item for item in project.assembly_layers if item.id == child_layer_id)
    parent_layer = next(
        (item for item in project.assembly_layers if item.id == parent_layer_id), None
    )
    native_parent_is_above = bool(parent_layer and parent_layer.z_index > child.z_index)
    if treatment is not None:
        treatment_preserves_depth = attachment_treatment_depth_is_valid(project, treatment)
        with Image.open(project.resolve(project_directory, treatment.texture_path)) as opened:
            source_treatment = normalise_rgba_for_transform(opened)
        rest_world, animated_world = evaluate_joint_matrices(project, animation, time)
        parent_delta = (
            animated_world[treatment.parent_joint] @ rest_world[treatment.parent_joint].inverse()
        )
        inverse = parent_delta.inverse()
        rendered_treatment = transform_premultiplied_rgba(
            source_treatment,
            (inverse.xx, inverse.yx, inverse.tx, inverse.xy, inverse.yy, inverse.ty),
            project.canvas_size,
        )
        treatment_alpha = np.asarray(rendered_treatment.getchannel("A"))
    covered = relevant_boundary & (
        ((treatment_alpha >= 16) & treatment_preserves_depth) | native_parent_is_above
    )
    uncovered = relevant_boundary & ~covered
    coverage = int(np.count_nonzero(covered)) / relevant_pixels
    largest_uncovered = _largest_component_size(uncovered)
    uncovered_ys, uncovered_xs = np.where(uncovered)
    uncovered_bounds = (
        (
            int(uncovered_xs.min()),
            int(uncovered_ys.min()),
            int(uncovered_xs.max()) + 1,
            int(uncovered_ys.max()) + 1,
        )
        if len(uncovered_xs)
        else None
    )

    if parent_layer_id is None:
        coverage_deficit = 0
    else:
        deficit = relevant_boundary & (parent_alpha - frame[..., 3].astype(np.int16) > 2)
        coverage_deficit = int(np.count_nonzero(deficit))

    # An opaque seam is a contrast discontinuity even when alpha coverage is complete.
    # Measure it only on the rotating boundary that is not owned by the Body occluder.
    worst_contrast_component = 0
    backgrounds = (
        (0, 0, 0),
        (255, 255, 255),
        (128, 128, 128),
        (255, 0, 180),
    )
    rgba = frame.astype(np.float32)
    alpha = rgba[..., 3:4] / 255.0
    for background in backgrounds:
        rgb = rgba[..., :3] * alpha + np.asarray(background, dtype=np.float32) * (1 - alpha)
        luminance = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
        below_luminance = np.zeros_like(luminance)
        below_luminance[:-1] = luminance[1:]
        contrast = np.abs(luminance - below_luminance) >= 20
        worst_contrast_component = max(
            worst_contrast_component,
            _largest_component_size(uncovered & contrast),
        )
    return (
        coverage,
        largest_uncovered,
        worst_contrast_component,
        coverage_deficit,
        uncovered_bounds,
    )


def production_sample_times(animation: GeneratedAnimation) -> list[float]:
    """Rest, motion, hold, return, and final-rest samples used by production gating."""
    key_times = {key.time for track in animation.tracks for key in track.keys}
    return sorted(
        {
            0.0,
            animation.duration * 0.25,
            animation.duration * 0.5,
            animation.duration * 0.75,
            animation.duration,
            *key_times,
            *maximum_extent_times(animation),
        }
    )


def breathing_perceptual_metrics(
    project_directory: Path,
    project: Project,
    animation: GeneratedAnimation,
) -> BreathingPerceptualMetrics:
    """Measure normal-size Body silhouette motion at exhale and peak inhale."""
    alphas = [
        _transformed_layer_alpha(project_directory, project, animation, time, "body")
        for time in (0.0, animation.duration / 2)
    ]
    masks = [alpha >= 32 for alpha in alphas]
    bounds: list[tuple[int, int, int, int]] = []
    for mask in masks:
        ys, xs = np.where(mask)
        if not len(xs):
            return BreathingPerceptualMetrics(0, 0, project.canvas_height)
        bounds.append((int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))
    torso_y = round(bounds[0][1] + (bounds[0][3] - bounds[0][1]) * 0.60)
    widths: list[int] = []
    for mask in masks:
        row = np.where(mask[torso_y])[0]
        widths.append(int(row.max() - row.min() + 1) if len(row) else 0)
    return BreathingPerceptualMetrics(
        torso_width_change=widths[1] - widths[0],
        shoulder_height_change=bounds[0][1] - bounds[1][1],
        lowest_paw_y_change=abs(bounds[1][3] - bounds[0][3]),
    )


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
        if all(
            abs(left - right) <= 1e-9
            for left, right in zip(
                (
                    child_delta.xx,
                    child_delta.xy,
                    child_delta.yx,
                    child_delta.yy,
                    child_delta.tx,
                    child_delta.ty,
                ),
                (
                    parent_delta.xx,
                    parent_delta.xy,
                    parent_delta.yx,
                    parent_delta.yy,
                    parent_delta.tx,
                    parent_delta.ty,
                ),
                strict=True,
            )
        ):
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
    coverage, largest_uncovered, contrast_length, coverage_deficit, uncovered_bounds = (
        _attachment_boundary_metrics(
            project_directory,
            project,
            animation,
            time,
            overlap,
            np.asarray(composite_animation_frame(project_directory, project, animation, time)),
            joint_name,
        )
    )
    if coverage < 0.95 or largest_uncovered > 2 or contrast_length > 8 or coverage_deficit:
        return RenderedAttachmentDiagnostic(
            "boundary",
            "Visible lower-Head attachment boundary detected "
            f"({coverage:.1%} covered, {largest_uncovered}px uncovered component, "
            f"{contrast_length}px contrast edge, bounds {uncovered_bounds})",
            opening_pixels,
            0,
            coverage,
            largest_uncovered,
            contrast_length,
            coverage_deficit,
        )
    edge = (frame[..., 3] > 0) & (frame[..., 3] < 96)
    colour_change = np.max(np.abs(frame[..., :3] - rest[..., :3]), axis=2) > 80
    fringe_mask = edge & colour_change & overlap_roi
    fringe_pixels = int(np.count_nonzero(fringe_mask))
    fringe_length = _largest_component_size(fringe_mask)
    if fringe_length > 8:
        return RenderedAttachmentDiagnostic(
            "fringe",
            f"Connected resampling fringe detected ({fringe_length}px edge)",
            0,
            fringe_pixels,
        )
    return RenderedAttachmentDiagnostic(
        "ok",
        "No visible attachment gap or opaque boundary detected",
        0,
        fringe_pixels,
        coverage,
        largest_uncovered,
        contrast_length,
        coverage_deficit,
    )


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
    sample_times = production_sample_times(animation)
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
            if diagnostic.status in {"gap", "fringe", "boundary"}:
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
    if animation.template_id == "idle_breathing":
        breathing = breathing_perceptual_metrics(project_directory, project, animation)
        if not breathing.passed:
            warnings.append(
                "Idle breathing — normal-size perceptual check failed "
                f"(torso {breathing.torso_width_change:+d}px, shoulders "
                f"{breathing.shoulder_height_change:+d}px, paws "
                f"{breathing.lowest_paw_y_change}px)."
            )
    return warnings
