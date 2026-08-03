from __future__ import annotations

import json
from copy import deepcopy

import pytest

from cat_layer_studio.models.animation import AnimationHistory
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.animation_service import (
    AnimationCompatibilityError,
    default_animation_set,
    generate_animation,
    generate_animation_set,
    reset_template,
)
from cat_layer_studio.services.joint_placement_service import (
    accept_joint_placement,
    ensure_joint_placements,
)


def _project() -> Project:
    project = Project("Animated", "master.png")
    project.animation_set = default_animation_set(project.rig_profile)
    return project


def test_default_templates_generate_stable_rest_safe_mvp_tracks() -> None:
    project = _project()
    ensure_joint_placements(project)
    for joint_name in ("Head", "Tail"):
        placement = accept_joint_placement(project, joint_name)
        placement.validation_status = "valid"
        placement.safe_rotation_min = -8
        placement.safe_rotation_max = 8
    generated, warnings = generate_animation_set(project, purpose="preview")

    assert [animation.name for animation in generated] == [
        "idle",
        "tail_sway",
        "head_tilt_left",
        "head_tilt_right",
        "happy_bounce",
    ]
    assert "blink" in warnings
    assert warnings["ear_twitch_left"] == "Not supported by this artwork"
    assert {track.target_path for animation in generated for track in animation.tracks} >= {
        "Skeleton2D/Root/Body",
        "Skeleton2D/Root/Body/Head",
        "Skeleton2D/Root/Body/Tail",
    }
    for animation in generated:
        for track in animation.tracks:
            assert track.keys[0].time == 0
            assert track.keys[-1].time == pytest.approx(animation.duration)
            assert track.keys[-1].value == track.keys[0].value


def test_generation_is_deterministic_and_duration_is_persistent() -> None:
    project = _project()
    settings = project.animation_set.templates[0]
    settings.duration = 2.25
    first = generate_animation(settings, project)
    second = generate_animation(deepcopy(settings), project)
    assert first == second
    assert first.duration == 2.25


def test_missing_joint_is_rejected_clearly() -> None:
    project = _project()
    with pytest.raises(AnimationCompatibilityError, match="Body, Head"):
        generate_animation(project.animation_set.templates[0], project, available_joints=set())


def test_blink_requires_closed_art_and_uses_visibility_swaps() -> None:
    project = _project()
    project.assembly_layers = [
        AssemblyLayer("lo", "Left open", "left-open.png", "eye_screen_left", asset_state="open"),
        AssemblyLayer("ro", "Right open", "right-open.png", "eye_screen_right", asset_state="open"),
    ]
    blink = project.animation_set.templates[-1]
    with pytest.raises(
        AnimationCompatibilityError,
        match=r"Missing: left closed eye and right closed eye\.",
    ):
        generate_animation(blink, project)

    project.assembly_layers.extend(
        [
            AssemblyLayer(
                "lc", "Left closed", "left-closed.png", "eye_screen_left", asset_state="closed"
            ),
            AssemblyLayer(
                "rc",
                "Right closed",
                "right-closed.png",
                "eye_screen_right",
                asset_state="closed",
            ),
        ]
    )
    generated = generate_animation(
        blink,
        project,
        asset_node_paths={
            "left_open_eye": "Eyes/LeftOpen",
            "right_open_eye": "Eyes/RightOpen",
            "left_closed_eye": "Eyes/LeftClosed",
            "right_closed_eye": "Eyes/RightClosed",
        },
    )
    assert generated.name == "blink"
    assert all(track.property_name == "visible" for track in generated.tracks)
    assert generated.tracks[0].keys[0].value is True
    assert generated.tracks[0].keys[-1].value is True


def test_template_reset_and_history_undo_redo() -> None:
    animation_set = default_animation_set()
    history = AnimationHistory()
    history.reset(animation_set)
    animation_set.templates[0].parameters["breathing_strength"] = "Noticeable"
    history.commit(animation_set)
    assert history.undo().templates[0].parameters["breathing_strength"] == "Natural"
    assert history.redo().templates[0].parameters["breathing_strength"] == "Noticeable"
    reset_template(animation_set, "idle_breathing")
    assert animation_set.templates[0].parameters["breathing_strength"] == "Natural"


def test_animation_settings_round_trip_inside_project_json() -> None:
    project = _project()
    project.animation_set.templates[1].parameters["direction"] = "Right first"
    payload = json.loads(json.dumps(project.to_dict()))
    restored = Project.from_dict(payload)
    assert restored.animation_set.to_dict() == project.animation_set.to_dict()
