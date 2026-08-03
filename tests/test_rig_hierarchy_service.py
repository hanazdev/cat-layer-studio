from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.animation_service import default_animation_set, generate_animation
from cat_layer_studio.services.composition_service import (
    composite_animation_frame,
    composite_assembly,
)
from cat_layer_studio.services.joint_placement_service import (
    accept_joint_placement,
    ensure_joint_placements,
    set_joint_point,
)
from cat_layer_studio.services.rig_hierarchy_service import evaluate_joint_matrices


def _overlap_project(directory: Path) -> Project:
    components = directory / "components"
    components.mkdir()
    body = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(body).rectangle((20, 28, 44, 58), fill=(120, 80, 50, 255))
    body.save(components / "body.png")
    head = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(head).rectangle((16, 8, 48, 35), fill=(210, 150, 90, 255))
    head.save(components / "head.png")
    project = Project("Overlap", "master.png", 64, 64)
    project.assembly_layers = [
        AssemblyLayer(
            "body",
            "Body",
            "components/body.png",
            "body",
            attachment_joint="Body",
            pivot_x=32,
            pivot_y=42,
            z_index=1,
        ),
        AssemblyLayer(
            "head",
            "Head",
            "components/head.png",
            "head",
            attachment_joint="Head",
            pivot_x=32,
            pivot_y=31,
            z_index=2,
        ),
    ]
    project.animation_set = default_animation_set()
    ensure_joint_placements(project)
    set_joint_point(project, "Head", 32, 31)
    placement = accept_joint_placement(project, "Head")
    placement.validation_status = "valid"
    placement.safe_rotation_min = -10
    placement.safe_rotation_max = 10
    return project


def test_body_translation_carries_head_once_and_matches_generated_key(tmp_path: Path) -> None:
    project = _overlap_project(tmp_path)
    idle = generate_animation(project.animation_set.templates[0], project)
    rest, midpoint = evaluate_joint_matrices(project, idle, idle.duration / 2)

    assert midpoint["Body"].yy > rest["Body"].yy
    assert midpoint["Head"].yy > rest["Head"].yy
    assert midpoint["Head"].xx == midpoint["Body"].xx

    rest_frame = composite_assembly(tmp_path, project)
    midpoint_frame = composite_animation_frame(tmp_path, project, idle, idle.duration / 2)
    assert ImageChops.difference(rest_frame, midpoint_frame).getbbox() is not None


def test_head_tilt_uses_saved_neck_pivot_preserves_overlap_and_rest(tmp_path: Path) -> None:
    project = _overlap_project(tmp_path)
    settings = next(
        item for item in project.animation_set.templates if item.template_id == "head_tilt_left"
    )
    animation = generate_animation(settings, project, purpose="preview")
    extent_time = animation.tracks[0].keys[1].time
    rest, extent = evaluate_joint_matrices(project, animation, extent_time)

    assert rest["Head"].point((0, 0)) == (32, 31)
    assert extent["Head"].point((0, 0)) == (32, 31)
    tilted = composite_animation_frame(tmp_path, project, animation, extent_time)
    alpha = tilted.getchannel("A")
    # The known neck band stays continuously occupied across the central attachment.
    assert all(alpha.getpixel((32, y)) > 0 for y in range(27, 37))

    rest_frame = composite_assembly(tmp_path, project)
    returned = composite_animation_frame(tmp_path, project, animation, animation.duration)
    assert returned.tobytes() == rest_frame.tobytes()
