from __future__ import annotations

import math
from dataclasses import dataclass

from cat_layer_studio.models.animation import GeneratedAnimation
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import RigTemplate, get_rig_template
from cat_layer_studio.services.joint_placement_service import resolved_joint_placements


@dataclass(frozen=True, slots=True)
class Affine2D:
    """Small deterministic 2D affine matrix using Godot's transform convention."""

    xx: float = 1.0
    xy: float = 0.0
    yx: float = 0.0
    yy: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def __matmul__(self, other: Affine2D) -> Affine2D:
        return Affine2D(
            self.xx * other.xx + self.yx * other.xy,
            self.xy * other.xx + self.yy * other.xy,
            self.xx * other.yx + self.yx * other.yy,
            self.xy * other.yx + self.yy * other.yy,
            self.xx * other.tx + self.yx * other.ty + self.tx,
            self.xy * other.tx + self.yy * other.ty + self.ty,
        )

    @classmethod
    def local(cls, position: tuple[float, float], rotation: float = 0.0) -> Affine2D:
        cosine, sine = math.cos(rotation), math.sin(rotation)
        return cls(cosine, sine, -sine, cosine, position[0], position[1])

    def inverse(self) -> Affine2D:
        determinant = self.xx * self.yy - self.yx * self.xy
        if abs(determinant) < 1e-12:
            raise ValueError("Cannot invert a zero-scale rig transform.")
        xx, xy = self.yy / determinant, -self.xy / determinant
        yx, yy = -self.yx / determinant, self.xx / determinant
        return Affine2D(
            xx, xy, yx, yy, -(xx * self.tx + yx * self.ty), -(xy * self.tx + yy * self.ty)
        )

    def point(self, point: tuple[float, float]) -> tuple[float, float]:
        return (
            self.xx * point[0] + self.yx * point[1] + self.tx,
            self.xy * point[0] + self.yy * point[1] + self.ty,
        )


def configured_joint_pivots(
    project: Project, template: RigTemplate | None = None
) -> dict[str, tuple[float, float]]:
    """Compatibility alias for the canonical project-level placement resolver."""
    return resolved_joint_placements(project)


def local_rest_positions(
    template: RigTemplate, pivots: dict[str, tuple[float, float]]
) -> dict[str, tuple[float, float]]:
    return {
        joint.name: pivots[joint.name]
        if joint.parent is None
        else (
            pivots[joint.name][0] - pivots[joint.parent][0],
            pivots[joint.name][1] - pivots[joint.parent][1],
        )
        for joint in template.joints
    }


def joint_paths(template: RigTemplate) -> dict[str, str]:
    paths: dict[str, str] = {}
    for joint in template.joints:
        paths[joint.name] = (
            f"Skeleton2D/{joint.name}"
            if joint.parent is None
            else f"{paths[joint.parent]}/{joint.name}"
        )
    return paths


def evaluate_joint_matrices(
    project: Project,
    animation: GeneratedAnimation | None = None,
    time: float = 0.0,
    *,
    movement_scale: float = 1.0,
) -> tuple[dict[str, Affine2D], dict[str, Affine2D]]:
    """Return rest and animated world matrices from the same local hierarchy Godot exports."""
    from cat_layer_studio.services.animation_service import sample_track

    template = get_rig_template(project.rig_profile)
    pivots = configured_joint_pivots(project, template)
    rest_positions = local_rest_positions(template, pivots)
    paths = joint_paths(template)
    samples = (
        {}
        if animation is None
        else {
            (track.target_path, track.property_name): sample_track(track, time)
            for track in animation.tracks
        }
    )
    rest_world: dict[str, Affine2D] = {}
    animated_world: dict[str, Affine2D] = {}
    for joint in template.joints:
        rest_position = rest_positions[joint.name]
        sampled_position = samples.get((paths[joint.name], "position"), rest_position)
        if not isinstance(sampled_position, tuple):
            sampled_position = rest_position
        position = (
            rest_position[0] + (float(sampled_position[0]) - rest_position[0]) * movement_scale,
            rest_position[1] + (float(sampled_position[1]) - rest_position[1]) * movement_scale,
        )
        sampled_rotation = samples.get((paths[joint.name], "rotation"), 0.0)
        rotation = (
            float(sampled_rotation) * movement_scale
            if isinstance(sampled_rotation, (int, float))
            else 0.0
        )
        rest_local = Affine2D.local(rest_position)
        animated_local = Affine2D.local(position, rotation)
        if joint.parent is None:
            rest_world[joint.name] = rest_local
            animated_world[joint.name] = animated_local
        else:
            rest_world[joint.name] = rest_world[joint.parent] @ rest_local
            animated_world[joint.name] = animated_world[joint.parent] @ animated_local
    return rest_world, animated_world


def layer_rest_world_transform(project: Project, layer_id: str) -> Affine2D:
    layer = next(item for item in project.assembly_layers if item.id == layer_id)
    rest, _animated = evaluate_joint_matrices(project)
    joint = layer.attachment_joint or "Root"
    return rest.get(joint, rest["Root"]) @ Affine2D(tx=layer.offset_x, ty=layer.offset_y)


def layer_animated_world_transform(
    project: Project, layer_id: str, animation: GeneratedAnimation, time: float
) -> Affine2D:
    layer = next(item for item in project.assembly_layers if item.id == layer_id)
    _rest, animated = evaluate_joint_matrices(project, animation, time)
    joint = layer.attachment_joint or "Root"
    return animated.get(joint, animated["Root"]) @ Affine2D(tx=layer.offset_x, ty=layer.offset_y)


def layer_delta_transform(
    project: Project, layer_id: str, animation: GeneratedAnimation, time: float
) -> Affine2D:
    return layer_animated_world_transform(project, layer_id, animation, time) @ (
        layer_rest_world_transform(project, layer_id).inverse()
    )
