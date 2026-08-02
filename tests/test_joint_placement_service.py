from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.joint_placement import JointPlacementHistory
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.animation_service import default_animation_set, generate_animation
from cat_layer_studio.services.composition_service import composite_assembly
from cat_layer_studio.services.joint_placement_service import (
    accept_joint_placement,
    ensure_joint_placements,
    find_safe_rotation_range,
    placement_for,
    reset_joint_to_suggestion,
    reset_joint_to_template,
    resolve_joint_placement,
    set_joint_point,
    suggest_joint_placement,
    update_suggestion,
)
from cat_layer_studio.services.project_service import load_project, save_project


def _art_project(root: Path, *, overlap: bool = True) -> Project:
    components = root / "components"
    components.mkdir()
    body = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(body).rectangle((18, 28, 46, 60), fill=(120, 80, 40, 255))
    body.putpixel((1, 1), (255, 255, 255, 10))
    body.save(components / "body.png")
    head = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    top, bottom = (14, 36) if overlap else (2, 20)
    ImageDraw.Draw(head).rectangle((14, top, 50, bottom), fill=(210, 160, 100, 255))
    head.putpixel((1, 1), (255, 255, 255, 10))
    head.save(components / "head.png")
    project = Project("Movement", "master.png", 64, 64)
    project.assembly_layers = [
        AssemblyLayer(
            "body",
            "Body",
            "components/body.png",
            "body",
            attachment_joint="Body",
            z_index=1,
        ),
        AssemblyLayer(
            "head",
            "Head",
            "components/head.png",
            "head",
            attachment_joint="Head",
            z_index=2,
        ),
    ]
    ensure_joint_placements(project)
    return project


def test_overlap_suggestion_filters_noise_and_lies_inside_attachment(tmp_path: Path) -> None:
    project = _art_project(tmp_path)
    suggestion = suggest_joint_placement(tmp_path, project, "Head")
    assert suggestion.used_fallback is False
    assert suggestion.confidence == "high"
    assert 18 <= suggestion.x <= 46
    assert 28 <= suggestion.y <= 36
    assert (suggestion.x, suggestion.y) != (1, 1)


def test_no_overlap_uses_low_confidence_boundary_fallback(tmp_path: Path) -> None:
    project = _art_project(tmp_path, overlap=False)
    suggestion = suggest_joint_placement(tmp_path, project, "Head")
    assert suggestion.used_fallback is True
    assert suggestion.confidence == "low"
    assert "No substantial overlap" in suggestion.reason


def test_resolver_priority_edit_history_resets_and_round_trip(tmp_path: Path) -> None:
    project = _art_project(tmp_path)
    placement = update_suggestion(tmp_path, project, "Head")
    suggested = (placement.suggestion_x, placement.suggestion_y)
    assert resolve_joint_placement(project, "Head") == suggested
    history = JointPlacementHistory()
    history.reset(project.joint_placements)
    set_joint_point(project, "Head", 30.25, 31.5)
    history.commit(project.joint_placements)
    assert project.animation_verification_valid is False
    assert resolve_joint_placement(project, "Head") == suggested
    accept_joint_placement(project, "Head")
    assert resolve_joint_placement(project, "Head") == (30.25, 31.5)
    assert history.undo() is not None
    reset_joint_to_suggestion(project, "Head")
    assert (placement_for(project, "Head").x, placement_for(project, "Head").y) == suggested
    reset_joint_to_template(project, "Head")
    assert (placement_for(project, "Head").x, placement_for(project, "Head").y) == (256, 270)

    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    save_project(tmp_path, project, backup=False)
    _, restored = load_project(tmp_path / "project.json")
    assert restored.to_dict() == project.to_dict()


def test_pivot_change_is_pixel_exact_at_rest_and_clamps_animation(tmp_path: Path) -> None:
    project = _art_project(tmp_path)
    before = composite_assembly(tmp_path, project)
    set_joint_point(project, "Head", 31.5, 32.25)
    placement = accept_joint_placement(project, "Head")
    placement.safe_rotation_min = -3.5
    placement.safe_rotation_max = 4.0
    placement.validation_status = "valid"
    after = composite_assembly(tmp_path, project)
    assert before.tobytes() == after.tobytes()

    project.animation_set = default_animation_set()
    settings = next(
        item for item in project.animation_set.templates if item.template_id == "head_tilt_left"
    )
    animation = generate_animation(settings, project)
    assert animation.parameters["requested_rotation_degrees"] == -8
    assert animation.parameters["generated_rotation_degrees"] == -3.5
    assert animation.parameters["clamped_to_safe_range"] is True


def test_current_adult_front_sitting_regression_fixture() -> None:
    project_file = Path(__file__).parents[1] / "v1_test" / "project.json"
    directory, project = load_project(project_file)
    placement = update_suggestion(directory, project, "Head")
    assert (placement.suggestion_x, placement.suggestion_y) == (253.0, 201.0)
    assert placement.confidence == "high"
    assert (placement.suggestion_x, placement.suggestion_y) != (256.0, 270.0)
    accept_joint_placement(project, "Head")
    assert find_safe_rotation_range(directory, project, "Head") == (-15.0, 15.0)
