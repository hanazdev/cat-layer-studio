from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from cat_layer_studio.models.animation import GeneratedAnimation
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.animation_service import sample_track
from cat_layer_studio.services.joint_placement_service import resolve_joint_placement
from cat_layer_studio.services.rig_hierarchy_service import evaluate_joint_matrices


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
            joint_name = layer.attachment_joint or get_rig_template(
                project.rig_profile
            ).attachment_map.get(layer.slot, "Root")
            pivot = resolve_joint_placement(project, joint_name)
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
    *,
    movement_scale: float = 1.0,
    debug_overlay: bool = False,
) -> Image.Image:
    """Render from the same local-to-world hierarchy and key values used by Godot."""
    template = get_rig_template(project.rig_profile)
    rest_world, animated_world = evaluate_joint_matrices(
        project, animation, time, movement_scale=movement_scale
    )
    samples = {
        (track.target_path, track.property_name): sample_track(track, time)
        for track in animation.tracks
    }
    output = Image.new("RGBA", project.canvas_size, (0, 0, 0, 0))
    diagnostic_alphas: dict[str, Image.Image] = {}
    original_head_bounds: tuple[int, int, int, int] | None = None
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
        if debug_overlay and layer.slot == "head":
            original = image.transform(
                project.canvas_size,
                Image.Transform.AFFINE,
                (1, 0, -layer.offset_x, 0, 1, -layer.offset_y),
                resample=Image.Resampling.BICUBIC,
            )
            original_head_bounds = original.getchannel("A").getbbox()
        joint = layer.attachment_joint or "Root"
        if joint not in rest_world:
            joint = "Root"
        delta = animated_world[joint] @ rest_world[joint].inverse()
        source_to_output = delta @ type(delta)(tx=layer.offset_x, ty=layer.offset_y)
        inverse = source_to_output.inverse()
        image = image.transform(
            project.canvas_size,
            Image.Transform.AFFINE,
            (inverse.xx, inverse.yx, inverse.tx, inverse.xy, inverse.yy, inverse.ty),
            resample=Image.Resampling.BICUBIC,
        )
        if debug_overlay and layer.slot in {"head", "body"}:
            diagnostic_alphas[layer.slot] = image.getchannel("A")
        output.alpha_composite(image)
    if debug_overlay:
        draw = ImageDraw.Draw(output)
        for joint in template.joints:
            origin = animated_world[joint.name].point((0.0, 0.0))
            if joint.parent is not None:
                parent_origin = animated_world[joint.parent].point((0.0, 0.0))
                draw.line((*parent_origin, *origin), fill=(255, 210, 0, 230), width=2)
            colour = (0, 229, 255, 255) if joint.name == "Head" else (255, 110, 60, 255)
            draw.ellipse(
                (origin[0] - 4, origin[1] - 4, origin[0] + 4, origin[1] + 4),
                outline=colour,
                width=2,
            )
        head_bounds = (
            diagnostic_alphas.get("head").getbbox() if "head" in diagnostic_alphas else None
        )
        if original_head_bounds:
            draw.rectangle(original_head_bounds, outline=(80, 160, 255, 220), width=1)
        if head_bounds:
            draw.rectangle(head_bounds, outline=(0, 255, 120, 230), width=2)
        if "head" in diagnostic_alphas and "body" in diagnostic_alphas:
            overlap = ImageChops.multiply(diagnostic_alphas["head"], diagnostic_alphas["body"])
            if overlap_bounds := overlap.getbbox():
                draw.rectangle(overlap_bounds, outline=(255, 0, 220, 255), width=2)
    return output
