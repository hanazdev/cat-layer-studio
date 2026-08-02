from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from cat_layer_studio.models.animation import GeneratedAnimation
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.animation_service import sample_track


def ordered_layers(layers: list[AssemblyLayer]) -> list[AssemblyLayer]:
    return sorted(layers, key=lambda layer: (layer.z_index, layer.id))


def _closed_eye_at_rest(layer: AssemblyLayer) -> bool:
    if layer.slot not in {"eye_screen_left", "eye_screen_right"}:
        return False
    state_hint = (layer.asset_state or f"{layer.display_name} {layer.texture_path}").lower()
    return "closed" in state_hint


def composite_assembly(
    project_directory: Path,
    project: Project,
    *,
    include_hidden: bool = False,
    rotation_layer_id: str | None = None,
    rotation_degrees: float = 0.0,
) -> Image.Image:
    output = Image.new("RGBA", project.canvas_size, (0, 0, 0, 0))
    for layer in ordered_layers(project.assembly_layers):
        if (not layer.visible or _closed_eye_at_rest(layer)) and not include_hidden:
            continue
        path = project.resolve(project_directory, layer.texture_path)
        with Image.open(path) as opened:
            image = opened.convert("RGBA")
        if image.size != project.canvas_size:
            raise ValueError(f"{layer.display_name} does not match the project canvas.")
        if layer.opacity < 1:
            alpha = image.getchannel("A").point(
                lambda value, opacity=layer.opacity: round(value * opacity)
            )
            image.putalpha(alpha)
        # Pillow's integer paste anchor is deterministic. Sub-pixel offsets use an affine
        # transform so preview and exported Godot coordinates retain the exact decimal values.
        translated = image.transform(
            project.canvas_size,
            Image.Transform.AFFINE,
            (1, 0, -layer.offset_x, 0, 1, -layer.offset_y),
            resample=Image.Resampling.BICUBIC,
        )
        if layer.id == rotation_layer_id and rotation_degrees:
            pivot = (
                layer.pivot_x if layer.pivot_x is not None else project.canvas_width / 2,
                layer.pivot_y if layer.pivot_y is not None else project.canvas_height / 2,
            )
            translated = translated.rotate(
                -rotation_degrees,
                center=pivot,
                resample=Image.Resampling.BICUBIC,
            )
        output.alpha_composite(translated)
    return output


def finite_layer_coordinates(layer: AssemblyLayer) -> bool:
    values = (layer.offset_x, layer.offset_y, layer.opacity)
    return all(math.isfinite(value) for value in values)


def composite_animation_frame(
    project_directory: Path,
    project: Project,
    animation: GeneratedAnimation,
    time: float,
) -> Image.Image:
    """Render a deterministic lightweight preview from the same generated track data."""
    template = get_rig_template(project.rig_profile)
    parents = {joint.name: joint.parent for joint in template.joints}
    pivots = {joint.name: joint.suggested_pivot for joint in template.joints}
    paths: dict[str, str] = {}
    for joint in template.joints:
        paths[joint.name] = (
            f"Skeleton2D/{joint.name}"
            if joint.parent is None
            else f"{paths[joint.parent]}/{joint.name}"
        )
    samples = {
        (track.target_path, track.property_name): sample_track(track, time)
        for track in animation.tracks
    }
    output = Image.new("RGBA", project.canvas_size, (0, 0, 0, 0))
    for layer in ordered_layers(project.assembly_layers):
        visible = layer.visible and not _closed_eye_at_rest(layer)
        fallback_path = f"Visuals/{layer.id}"
        if (fallback_path, "visible") in samples:
            visible = bool(samples[(fallback_path, "visible")])
        if not visible:
            continue
        with Image.open(project.resolve(project_directory, layer.texture_path)) as opened:
            image = opened.convert("RGBA")
        if image.size != project.canvas_size:
            raise ValueError(f"{layer.display_name} does not match the project canvas.")
        if layer.opacity < 1:
            alpha = image.getchannel("A").point(
                lambda value, opacity=layer.opacity: round(value * opacity)
            )
            image.putalpha(alpha)
        image = image.transform(
            project.canvas_size,
            Image.Transform.AFFINE,
            (1, 0, -layer.offset_x, 0, 1, -layer.offset_y),
            resample=Image.Resampling.BICUBIC,
        )
        joint = layer.attachment_joint or "Root"
        ancestry: list[str] = []
        current: str | None = joint
        while current is not None:
            ancestry.append(current)
            current = parents.get(current)
        accumulated_x = accumulated_y = 0.0
        for name in reversed(ancestry):
            position = samples.get((paths[name], "position"))
            if isinstance(position, tuple):
                rest_joint = next(item for item in template.joints if item.name == name)
                if rest_joint.parent is None:
                    rest_position = rest_joint.suggested_pivot
                else:
                    parent_pivot = pivots[rest_joint.parent]
                    rest_position = (
                        rest_joint.suggested_pivot[0] - parent_pivot[0],
                        rest_joint.suggested_pivot[1] - parent_pivot[1],
                    )
                dx = float(position[0]) - rest_position[0]
                dy = float(position[1]) - rest_position[1]
                accumulated_x += dx
                accumulated_y += dy
                image = image.transform(
                    project.canvas_size,
                    Image.Transform.AFFINE,
                    (1, 0, -dx, 0, 1, -dy),
                    resample=Image.Resampling.BICUBIC,
                )
            rotation = samples.get((paths[name], "rotation"), 0.0)
            if isinstance(rotation, (int, float)) and rotation:
                pivot = (
                    pivots[name][0] + accumulated_x,
                    pivots[name][1] + accumulated_y,
                )
                image = image.rotate(
                    -math.degrees(float(rotation)),
                    center=pivot,
                    resample=Image.Resampling.BICUBIC,
                )
        output.alpha_composite(image)
    return output
