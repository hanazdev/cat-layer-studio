from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from cat_layer_studio.models.animation import AnimationSet
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.alpha_transform_service import (
    normalise_rgba_for_transform,
    transform_premultiplied_rgba,
)
from cat_layer_studio.services.animation_service import (
    default_animation_set,
    export_status,
    generate_animation,
    preview_status,
)
from cat_layer_studio.services.composition_service import composite_animation_frame
from cat_layer_studio.services.joint_placement_service import ensure_joint_placements
from cat_layer_studio.services.rig_hierarchy_service import layer_delta_transform


def test_unapproved_suggestion_is_previewable_but_not_exportable() -> None:
    project = Project("Policy", "master.png")
    project.animation_set = default_animation_set()
    ensure_joint_placements(project)
    head = next(item for item in project.joint_placements if item.joint_name == "Head")
    head.suggestion_x, head.suggestion_y = head.x, head.y
    settings = next(
        item for item in project.animation_set.templates if item.template_id == "head_tilt_left"
    )
    assert preview_status(settings, project) == "Preview using suggestion"
    assert export_status(settings, project) == "Needs user review"
    assert generate_animation(settings, project, purpose="preview").tracks


def test_animation_v1_migrates_obsolete_defaults_and_invalidates_verification() -> None:
    animation = default_animation_set().to_dict()
    animation["format_version"] = 1
    idle = next(item for item in animation["templates"] if item["template_id"] == "idle_breathing")
    bounce = next(item for item in animation["templates"] if item["template_id"] == "happy_bounce")
    idle["parameters"]["head_movement"] = True
    bounce["parameters"].update(head_movement=True, move_ears_too=True)
    project = Project.from_dict(
        Project("Legacy", "master.png", animation_set=AnimationSet.from_dict(animation)).to_dict()
    )
    # Exercise raw legacy loading as Project.from_dict receives it from JSON.
    raw = Project("Legacy", "master.png").to_dict()
    raw["animation_set"] = animation
    raw["animation_verification_valid"] = True
    raw["godot_export_status"] = "Verified"
    project = Project.from_dict(raw)
    settings = {item.template_id: item for item in project.animation_set.templates}
    assert settings["idle_breathing"].parameters["head_movement"] is False
    assert settings["happy_bounce"].parameters["head_movement"] is False
    assert settings["happy_bounce"].parameters["move_ears_too"] is False
    assert settings["ear_twitch_left"].enabled is False
    assert project.animation_verification_valid is False
    assert project.godot_export_status == "Needs regeneration"


def test_premultiplied_transform_cannot_reveal_hidden_rgb() -> None:
    pixels = np.zeros((9, 9, 4), dtype=np.uint8)
    pixels[..., 0] = 255  # deliberately contaminated transparent red
    pixels[3:6, 3:6] = (0, 200, 40, 255)
    source = Image.fromarray(pixels, "RGBA")
    clean = normalise_rgba_for_transform(source)
    assert np.asarray(clean)[0, 0].tolist() == [0, 0, 0, 0]
    moved = transform_premultiplied_rgba(source, (1, 0, -0.35, 0, 1, -0.4), source.size)
    result = np.asarray(moved)
    visible = result[..., 3] > 0
    assert np.max(result[..., 0][visible], initial=0) == 0


def test_body_only_motion_gives_descendants_identical_delta(tmp_path: Path) -> None:
    components = tmp_path / "components"
    components.mkdir()
    for name, colour in (("body", (80, 120, 180, 255)), ("head", (180, 120, 80, 255))):
        Image.new("RGBA", (32, 32), colour).save(components / f"{name}.png")
    project = Project("Rigid", "master.png", 32, 32)
    project.assembly_layers = [
        AssemblyLayer("body", "Body", "components/body.png", "body", attachment_joint="Body"),
        AssemblyLayer(
            "head", "Head", "components/head.png", "head", z_index=1, attachment_joint="Head"
        ),
    ]
    project.animation_set = default_animation_set()
    idle = generate_animation(project.animation_set.templates[0], project, purpose="preview")
    body_delta = layer_delta_transform(project, "body", idle, idle.duration / 2)
    head_delta = layer_delta_transform(project, "head", idle, idle.duration / 2)
    assert body_delta == head_delta
    frame = composite_animation_frame(tmp_path, project, idle, idle.duration / 2)
    assert frame.size == project.canvas_size
