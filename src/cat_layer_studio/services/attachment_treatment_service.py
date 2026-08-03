from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from cat_layer_studio.models.animation import AnimationKey, GeneratedAnimation, GeneratedTrack
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.attachment_treatment import AttachmentTreatment
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.alpha_transform_service import (
    normalise_rgba_for_transform,
    transform_premultiplied_rgba,
)
from cat_layer_studio.services.joint_placement_service import (
    attachment_masks,
    resolved_joint_placements,
)
from cat_layer_studio.services.rig_hierarchy_service import Affine2D, evaluate_joint_matrices

ATTACHMENT_GUARD_METHOD = "parent_underlay_coverage_guard"
ATTACHMENT_GUARD_ALGORITHM_VERSION = 5
HEAD_TREATMENT_ID = "generated_attachment_guard_body_head"
HEAD_TREATMENT_METHOD = ATTACHMENT_GUARD_METHOD
HEAD_TREATMENT_ALGORITHM_VERSION = ATTACHMENT_GUARD_ALGORITHM_VERSION


@dataclass(frozen=True, slots=True)
class DivergentAttachment:
    joint_name: str
    parent_joint: str
    parent_layer_id: str
    child_layer_id: str
    child_layer_ids: tuple[str, ...]
    template_ids: tuple[str, ...]
    requires_guard: bool


def _matrix_values(matrix: Affine2D) -> tuple[float, ...]:
    return matrix.xx, matrix.xy, matrix.yx, matrix.yy, matrix.tx, matrix.ty


def _same_transform(left: Affine2D, right: Affine2D, tolerance: float = 1e-9) -> bool:
    return all(
        abs(a - b) <= tolerance
        for a, b in zip(_matrix_values(left), _matrix_values(right), strict=True)
    )


def _sample_times(animation: GeneratedAnimation) -> tuple[float, ...]:
    keys = {0.0, animation.duration, animation.duration * 0.25, animation.duration * 0.5}
    keys.add(animation.duration * 0.75)
    keys.update(key.time for track in animation.tracks for key in track.keys)
    ordered = sorted(keys)
    keys.update((left + right) / 2 for left, right in zip(ordered, ordered[1:], strict=False))
    return tuple(sorted(keys))


def _guard_sample_times(animation: GeneratedAnimation) -> tuple[float, ...]:
    return tuple(
        sorted(
            {
                *_sample_times(animation),
                *(float(value) for value in np.linspace(0.0, animation.duration, 33)),
            }
        )
    )


def _attached_joint(project: Project, layer: AssemblyLayer) -> str:
    template = get_rig_template(project.rig_profile)
    return layer.attachment_joint or template.attachment_map.get(layer.slot, "Root")


def discover_divergent_attachments(
    project: Project, animations: list[GeneratedAnimation] | tuple[GeneratedAnimation, ...]
) -> list[DivergentAttachment]:
    """Find parent/child joints that receive different effective transforms.

    Discovery is based only on the rig hierarchy and generated tracks. Template names are not
    consulted, so future animations automatically enter the same coverage workflow.
    """
    template = get_rig_template(project.rig_profile)
    by_joint: dict[str, list[AssemblyLayer]] = {}
    for layer in project.assembly_layers:
        if layer.visible:
            by_joint.setdefault(_attached_joint(project, layer), []).append(layer)
    found: list[DivergentAttachment] = []
    for joint in template.joints:
        if joint.parent is None or not by_joint.get(joint.name) or not by_joint.get(joint.parent):
            continue
        divergent_templates: list[str] = []
        for animation in animations:
            for time in _sample_times(animation):
                rest, animated = evaluate_joint_matrices(project, animation, time)
                child_delta = animated[joint.name] @ rest[joint.name].inverse()
                parent_delta = animated[joint.parent] @ rest[joint.parent].inverse()
                if not _same_transform(child_delta, parent_delta):
                    divergent_templates.append(animation.template_id)
                    break
        if not divergent_templates:
            continue
        parent_layer = max(by_joint[joint.parent], key=lambda item: (item.z_index, item.id))
        child_layer = min(by_joint[joint.name], key=lambda item: (item.z_index, item.id))
        descendants = {joint.name}
        changed = True
        while changed:
            changed = False
            for candidate in template.joints:
                if candidate.parent in descendants and candidate.name not in descendants:
                    descendants.add(candidate.name)
                    changed = True
        child_layer_ids = tuple(
            layer.id
            for layer in project.assembly_layers
            if layer.visible and _attached_joint(project, layer) in descendants
        )
        found.append(
            DivergentAttachment(
                joint.name,
                joint.parent,
                parent_layer.id,
                child_layer.id,
                child_layer_ids,
                tuple(dict.fromkeys(divergent_templates)),
                child_layer.z_index >= parent_layer.z_index,
            )
        )
    return found


def enabled_attachment_treatment(project: Project, joint_name: str) -> AttachmentTreatment | None:
    return next(
        (
            item
            for item in project.attachment_treatments
            if item.joint_name == joint_name
            and item.enabled
            and item.method == ATTACHMENT_GUARD_METHOD
            and item.algorithm_version >= ATTACHMENT_GUARD_ALGORITHM_VERSION
            and item.provenance_version >= ATTACHMENT_GUARD_ALGORITHM_VERSION
            and attachment_treatment_depth_is_valid(project, item)
        ),
        None,
    )


def attachment_treatment_depth_is_valid(
    project: Project, treatment: AttachmentTreatment
) -> bool:
    """Return whether a generated parent underlay preserves every native child layer."""
    parent = next(
        (item for item in project.assembly_layers if item.id == treatment.parent_layer_id), None
    )
    children = [
        item for item in project.assembly_layers if item.id in treatment.child_layer_ids
    ]
    if parent is None or not children:
        return False
    return parent.z_index < treatment.z_index < min(item.z_index for item in children)


def enabled_head_treatment(project: Project) -> AttachmentTreatment | None:
    return enabled_attachment_treatment(project, "Head")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def treatment_is_current(project_directory: Path, project: Project, joint_name: str) -> bool:
    treatment = enabled_attachment_treatment(project, joint_name)
    if treatment is None or not treatment.source_layer_hashes:
        return False
    if not (project_directory / treatment.texture_path).is_file():
        return False
    current: dict[str, str] = {}
    for layer_id in treatment.source_layer_ids:
        layer = next((item for item in project.assembly_layers if item.id == layer_id), None)
        if layer is None:
            return False
        current[layer_id] = _sha256(project.resolve(project_directory, layer.texture_path))
    return current == treatment.source_layer_hashes


def head_treatment_is_current(project_directory: Path, project: Project) -> bool:
    return treatment_is_current(project_directory, project, "Head")


def _animation_fingerprint(animations: list[GeneratedAnimation]) -> str:
    payload = repr(
        [
            (
                animation.template_id,
                animation.duration,
                [
                    (
                        track.target_path,
                        track.property_name,
                        [(key.time, key.value) for key in track.keys],
                    )
                    for track in animation.tracks
                ],
            )
            for animation in sorted(animations, key=lambda item: item.template_id)
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _single_animation_fingerprint(animation: GeneratedAnimation) -> str:
    return _animation_fingerprint([animation])


def animation_treatments_are_current(
    project_directory: Path,
    project: Project,
    animation: GeneratedAnimation,
) -> bool:
    for relation in discover_divergent_attachments(project, [animation]):
        if not relation.requires_guard:
            continue
        treatment = enabled_attachment_treatment(project, relation.joint_name)
        if (
            treatment is None
            or animation.template_id not in treatment.template_ids
            or not treatment.animation_fingerprints
            or treatment.animation_fingerprints.get(animation.template_id)
            != _single_animation_fingerprint(animation)
            or not treatment_is_current(project_directory, project, relation.joint_name)
        ):
            return False
    return True


def treatment_active(
    project: Project,
    treatment: AttachmentTreatment,
    animation: GeneratedAnimation,
    time: float,
) -> bool:
    rest, animated = evaluate_joint_matrices(project, animation, time)
    if treatment.joint_name not in rest or treatment.parent_joint not in rest:
        return False
    child_delta = animated[treatment.joint_name] @ rest[treatment.joint_name].inverse()
    parent_delta = animated[treatment.parent_joint] @ rest[treatment.parent_joint].inverse()
    return not _same_transform(child_delta, parent_delta)


def with_attachment_visibility_tracks(
    project: Project,
    animations: list[GeneratedAnimation],
    node_paths: dict[str, str],
) -> list[GeneratedAnimation]:
    """Add discrete guard visibility tracks matching the canonical preview activation rule."""
    result: list[GeneratedAnimation] = []
    for animation in animations:
        tracks = list(animation.tracks)
        for treatment in project.attachment_treatments:
            path = node_paths.get(treatment.treatment_id)
            if path is None or animation.template_id not in treatment.template_ids:
                continue
            base_times = sorted(
                {0.0, animation.duration, *(key.time for track in tracks for key in track.keys)}
            )
            states: dict[float, bool] = {
                time: treatment_active(project, treatment, animation, time) for time in base_times
            }
            keys: dict[float, bool] = dict(states)
            epsilon = min(1e-5, animation.duration / 100000)
            for left, right in zip(base_times, base_times[1:], strict=False):
                midpoint = (left + right) / 2
                middle_state = treatment_active(project, treatment, animation, midpoint)
                if middle_state != states[left]:
                    keys[min(right, left + epsilon)] = middle_state
                if middle_state != states[right]:
                    keys[max(left, right - epsilon)] = middle_state
            tracks.append(
                GeneratedTrack(
                    path,
                    "visible",
                    "nearest",
                    tuple(AnimationKey(time, value) for time, value in sorted(keys.items())),
                )
            )
        result.append(replace(animation, tracks=tuple(tracks)))
    return result


def largest_attachment_component(mask: np.ndarray) -> np.ndarray:
    visited = np.zeros(mask.shape, dtype=bool)
    best: list[tuple[int, int]] = []
    height, width = mask.shape
    for start_y, start_x in zip(*np.where(mask), strict=True):
        if visited[start_y, start_x]:
            continue
        component: list[tuple[int, int]] = []
        queue = deque([(int(start_y), int(start_x))])
        visited[start_y, start_x] = True
        while queue:
            y, x = queue.popleft()
            component.append((y, x))
            for next_y, next_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if (
                    0 <= next_y < height
                    and 0 <= next_x < width
                    and mask[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    queue.append((next_y, next_x))
        if len(component) > len(best):
            best = component
    result = np.zeros(mask.shape, dtype=bool)
    for y, x in best:
        result[y, x] = True
    return result


def _placed_layer_image(
    project_directory: Path, project: Project, layer: AssemblyLayer
) -> Image.Image:
    with Image.open(project.resolve(project_directory, layer.texture_path)) as opened:
        source = normalise_rgba_for_transform(opened)
    return transform_premultiplied_rgba(
        source, (1, 0, -layer.offset_x, 0, 1, -layer.offset_y), project.canvas_size
    )


def _transformed_image(image: Image.Image, delta: Affine2D, size: tuple[int, int]) -> Image.Image:
    inverse = delta.inverse()
    return transform_premultiplied_rgba(
        image,
        (inverse.xx, inverse.yx, inverse.tx, inverse.xy, inverse.yy, inverse.ty),
        size,
    )


def _boundary(mask: np.ndarray) -> np.ndarray:
    eroded = (
        np.asarray(
            Image.fromarray((mask * 255).astype(np.uint8), "L").filter(ImageFilter.MinFilter(3))
        )
        >= 128
    )
    return mask & ~eroded


def _guard_identifier(relation: DivergentAttachment) -> str:
    return (
        f"generated_attachment_guard_{relation.parent_joint.lower()}_{relation.joint_name.lower()}"
    )


def _parent_underlay_z_index(
    project: Project, relation: DivergentAttachment, parent: AssemblyLayer
) -> int:
    guard_z = parent.z_index + 1
    next_native_z = min(
        (
            layer.z_index
            for layer in project.assembly_layers
            if layer.z_index > parent.z_index
        ),
        default=None,
    )
    if next_native_z is None or guard_z >= next_native_z:
        raise ValueError(
            f"{relation.joint_name} has no safe parent-underlay z-index between "
            f"{parent.display_name} and the next native layer. Increase their z-index spacing."
        )
    return guard_z


def generate_attachment_coverage_guard(
    project_directory: Path,
    project: Project,
    relation: DivergentAttachment,
    animations: list[GeneratedAnimation] | tuple[GeneratedAnimation, ...],
) -> AttachmentTreatment:
    parent = next(item for item in project.assembly_layers if item.id == relation.parent_layer_id)
    child = next(item for item in project.assembly_layers if item.id == relation.child_layer_id)
    guard_z = _parent_underlay_z_index(project, relation, parent)
    parent_mask, child_mask, overlap = attachment_masks(
        project_directory, project, relation.joint_name
    )
    opened = (
        Image.fromarray((overlap * 255).astype(np.uint8), "L")
        .filter(ImageFilter.MinFilter(5))
        .filter(ImageFilter.MaxFilter(5))
    )
    component = largest_attachment_component(np.asarray(opened) >= 128)
    ys, xs = np.where(component)
    if len(xs) < 16:
        raise ValueError(f"{relation.joint_name} has no substantial attachment overlap.")
    bounds = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    pad = 24
    protected_bounds = (
        max(0, bounds[0] - pad),
        max(0, bounds[1] - pad),
        min(project.canvas_width, bounds[2] + pad),
        min(project.canvas_height, bounds[3] + pad),
    )
    protected = np.zeros_like(component)
    protected[
        protected_bounds[1] : protected_bounds[3], protected_bounds[0] : protected_bounds[2]
    ] = True
    face_cutoff = (
        int(round(bounds[1] + (bounds[3] - bounds[1]) * 0.55))
        if relation.joint_name == "Head"
        else None
    )
    parent_image = _placed_layer_image(project_directory, project, parent)
    child_image = _placed_layer_image(project_directory, project, child)
    swept = np.zeros_like(component)
    sampled_rotations: list[float] = []
    selected = [item for item in animations if item.template_id in relation.template_ids]
    for animation in selected:
        for time in _guard_sample_times(animation):
            rest, animated = evaluate_joint_matrices(project, animation, time)
            child_delta = animated[relation.joint_name] @ rest[relation.joint_name].inverse()
            parent_delta = animated[relation.parent_joint] @ rest[relation.parent_joint].inverse()
            if _same_transform(child_delta, parent_delta):
                continue
            child_world = _transformed_image(child_image, child_delta, project.canvas_size)
            edge_world = _boundary(np.asarray(child_world.getchannel("A")) >= 16)
            # Express the moving boundary in the parent's rest coordinate system so the guard
            # can remain attached to the complete, unchanged parent layer.
            world_edge = Image.fromarray((edge_world * 255).astype(np.uint8), "L")
            edge_in_parent = world_edge.transform(
                project.canvas_size,
                Image.Transform.AFFINE,
                (
                    parent_delta.xx,
                    parent_delta.yx,
                    parent_delta.tx,
                    parent_delta.xy,
                    parent_delta.yy,
                    parent_delta.ty,
                ),
                resample=Image.Resampling.BILINEAR,
            )
            swept |= np.asarray(edge_in_parent) >= 16
            sampled_rotations.extend(
                float(value)
                for track in animation.tracks
                if track.target_path.endswith(relation.joint_name)
                and track.property_name == "rotation"
                for value in [next((key.value for key in track.keys if key.time == time), 0.0)]
                if isinstance(value, (int, float))
            )
    pivots = resolved_joint_placements(project)
    child_pivot = pivots[relation.joint_name]
    parent_pivot = pivots[relation.parent_joint]
    direction = np.asarray(
        (parent_pivot[0] - child_pivot[0], parent_pivot[1] - child_pivot[1]),
        dtype=np.float64,
    )
    length = float(np.linalg.norm(direction))
    if length:
        direction /= length
    grid_y, grid_x = np.indices(component.shape)
    projection = (grid_x - child_pivot[0]) * direction[0] + (grid_y - child_pivot[1]) * direction[1]
    parent_facing_threshold = float(np.percentile(projection[component], 55))
    parent_facing_overlap = component & (projection >= parent_facing_threshold)
    core = ((swept & parent_mask) | parent_facing_overlap) & protected
    core = (
        np.asarray(
            Image.fromarray((core * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(21))
        )
        >= 128
    )
    core &= parent_mask & protected
    if int(np.count_nonzero(core)) < 16:
        raise ValueError(f"{relation.joint_name} has no usable swept attachment boundary.")

    # Core pixels retain the native parent's complete alpha. Only a two-pixel outer skirt is
    # feathered, and only where both source layers are already substantially opaque.
    outer = (
        np.asarray(
            Image.fromarray((core * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(5))
        )
        >= 128
    )
    hidden = parent_mask & child_mask & protected
    skirt = outer & ~core & hidden
    mask = np.zeros_like(parent_mask, dtype=np.uint8)
    mask[core] = 255
    blurred = np.asarray(
        Image.fromarray((outer * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(1.0))
    )
    mask[skirt] = blurred[skirt]
    pixels = np.asarray(parent_image, dtype=np.uint8).copy()
    parent_alpha = pixels[..., 3].copy()
    pixels[..., 3] = (parent_alpha.astype(np.uint16) * mask.astype(np.uint16) // 255).astype(
        np.uint8
    )
    pixels[pixels[..., 3] == 0, :3] = 0

    output_directory = project_directory / "generated" / "attachments"
    output_directory.mkdir(parents=True, exist_ok=True)
    output = output_directory / f"{_guard_identifier(relation)}.png"
    Image.fromarray(pixels, "RGBA").save(output)
    source_ids = (parent.id, child.id)
    treatment = AttachmentTreatment(
        treatment_id=_guard_identifier(relation),
        joint_name=relation.joint_name,
        method=ATTACHMENT_GUARD_METHOD,
        texture_path=output.relative_to(project_directory).as_posix(),
        parent_joint=relation.parent_joint,
        z_index=guard_z,
        source_layer_ids=source_ids,
        template_ids=relation.template_ids,
        provenance_version=ATTACHMENT_GUARD_ALGORITHM_VERSION,
        algorithm_version=ATTACHMENT_GUARD_ALGORITHM_VERSION,
        mask_bounds=bounds,
        face_protection_cutoff_y=face_cutoff,
        sampled_angle_range=(
            min(sampled_rotations, default=0.0),
            max(sampled_rotations, default=0.0),
        ),
        source_layer_hashes={
            layer.id: _sha256(project.resolve(project_directory, layer.texture_path))
            for layer in (parent, child)
        },
        parent_layer_id=parent.id,
        child_layer_ids=relation.child_layer_ids,
        transform_owner=relation.parent_joint,
        coverage_policy="preserve_parent_alpha_under_child_with_2px_hidden_feather",
        protected_region_bounds=protected_bounds,
        animation_fingerprints={
            animation.template_id: _single_animation_fingerprint(animation)
            for animation in selected
        },
        regeneration_provenance={
            "generator": "Cat Layer Studio",
            "algorithm": ATTACHMENT_GUARD_METHOD,
            "algorithm_version": ATTACHMENT_GUARD_ALGORITHM_VERSION,
            "source": "Complete native parent layer",
            "component_policy": "swept child boundary nearest the rig attachment",
            "base_layers": "preserved immutable and unmasked",
            "sample_count": sum(len(_guard_sample_times(item)) for item in selected),
            "animation_fingerprint": _animation_fingerprint(selected),
        },
        verification_status="Needs automatic fix",
    )
    project.attachment_treatments = [
        item for item in project.attachment_treatments if item.joint_name != relation.joint_name
    ]
    project.attachment_treatments.append(treatment)
    project.animation_verification_valid = False
    project.godot_export_status = "Needs regeneration"
    return treatment


def prepare_animation_attachment_treatments(
    project_directory: Path,
    project: Project,
    animations: list[GeneratedAnimation] | tuple[GeneratedAnimation, ...],
) -> dict[str, str]:
    from cat_layer_studio.services.animation_inspection_service import (
        inspect_rendered_attachment,
        production_sample_times,
    )

    results: dict[str, str] = {}
    relations = discover_divergent_attachments(project, animations)
    for relation in relations:
        if not relation.requires_guard:
            for template_id in relation.template_ids:
                results.setdefault(template_id, "Passed with native parent depth")
            continue
        try:
            treatment = generate_attachment_coverage_guard(
                project_directory, project, relation, animations
            )
        except ValueError as error:
            for template_id in relation.template_ids:
                results[template_id] = f"Not supported by this artwork — {error}"
            continue
        details: dict[str, str] = {}
        for animation in animations:
            if animation.template_id not in relation.template_ids:
                continue
            diagnostics = [
                (
                    time,
                    inspect_rendered_attachment(
                        project_directory, project, animation, relation.joint_name, time
                    ),
                )
                for time in production_sample_times(animation)
            ]
            failed = next(((time, item) for time, item in diagnostics if item.status != "ok"), None)
            details[animation.template_id] = (
                f"At {failed[0]:.6f}s: {failed[1].message}"
                if failed
                else "All production samples passed."
            )
            results[animation.template_id] = (
                "Not supported by this artwork"
                if failed
                else "Passed with generated attachment treatment"
            )
        treatment.verification_details = details
        treatment.verification_status = (
            "Passed with generated attachment treatment"
            if details
            and all(value == "All production samples passed." for value in details.values())
            else "Not supported by this artwork"
        )
        treatment.edge_coverage_result = {
            "templates_checked": len(details),
            "templates_passed": sum(
                value == "All production samples passed." for value in details.values()
            ),
            "maximum_core_alpha_deficit": 2,
            "maximum_connected_fringe_pixels": 2,
            "maximum_high_contrast_edge_pixels": 8,
        }
    if project.animation_set:
        for template_id, status in results.items():
            project.animation_set.export_status[template_id] = (
                "Not supported by this artwork"
                if status.startswith("Not supported by this artwork")
                else status
            )
    return results


def prepare_head_tilt_attachments(project_directory: Path, project: Project) -> dict[str, str]:
    """Compatibility wrapper for the beginner Head Tilt workflow."""
    from cat_layer_studio.services.animation_service import generate_animation

    animations = [
        generate_animation(item, project, purpose="preview", project_directory=project_directory)
        for item in (project.animation_set.templates if project.animation_set else [])
        if item.enabled and item.template_id.startswith("head_tilt")
    ]
    return prepare_animation_attachment_treatments(project_directory, project, animations)


def set_head_treatment_enabled(project: Project, enabled: bool) -> None:
    treatment = next(
        (item for item in project.attachment_treatments if item.joint_name == "Head"), None
    )
    if treatment is None:
        raise ValueError("No generated Head attachment treatment exists.")
    treatment.enabled = enabled
    project.animation_verification_valid = False
    project.godot_export_status = "Needs regeneration"
