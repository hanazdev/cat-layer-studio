from __future__ import annotations

import hashlib
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import ImageChops

from cat_layer_studio.models.animation import AnimationKey, GeneratedAnimation, GeneratedTrack
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.animation_inspection_service import (
    breathing_perceptual_metrics,
    inspect_rendered_attachment,
    production_sample_times,
)
from cat_layer_studio.services.animation_service import (
    body_grounding_anchor,
    generate_animation,
    sample_track,
)
from cat_layer_studio.services.attachment_treatment_service import (
    attachment_treatment_depth_is_valid,
    discover_divergent_attachments,
    head_treatment_is_current,
    prepare_head_tilt_attachments,
    set_head_treatment_enabled,
)
from cat_layer_studio.services.composition_service import (
    composite_animation_frame,
    composite_assembly,
    render_base_layer,
)
from cat_layer_studio.services.godot_export_service import export_godot_rig
from cat_layer_studio.services.joint_placement_service import (
    ensure_joint_placements,
    placement_for,
    set_joint_point,
)
from cat_layer_studio.services.movement_calibration_service import (
    accept_calibration_session,
    begin_calibration_session,
    cancel_calibration_session,
    refresh_working_state,
)
from cat_layer_studio.services.project_service import load_project
from cat_layer_studio.services.rig_hierarchy_service import (
    evaluate_joint_matrices,
    layer_delta_transform,
)


def _fixture_project(tmp_path: Path) -> tuple[Path, Project]:
    source = Path(__file__).parents[1] / "v1_test"
    shutil.copytree(source / "components", tmp_path / "components")
    shutil.copy2(source / "project.json", tmp_path / "project.json")
    directory, project = load_project(tmp_path / "project.json")
    head = placement_for(project, "Head")
    assert head is not None
    head.validation_status = "valid"
    head.safe_rotation_min = -8
    head.safe_rotation_max = 8
    return directory, project


def _affine_values(matrix) -> tuple[float, float, float, float, float, float]:
    return matrix.xx, matrix.xy, matrix.yx, matrix.yy, matrix.tx, matrix.ty


def test_contextual_calibration_is_temporary_cancelable_and_committable() -> None:
    project = Project("Session", "master.png")
    from cat_layer_studio.services.animation_service import default_animation_set

    project.animation_set = default_animation_set()
    ensure_joint_placements(project)
    accepted = placement_for(project, "Head")
    assert accepted is not None
    accepted.approved = True
    accepted.last_approved_x, accepted.last_approved_y = accepted.x, accepted.y
    original = deepcopy(project.to_dict())

    session = begin_calibration_session(project, "head_tilt_left")
    assert session.joint_name == "Head"
    assert session.stationary_parent_joint == "Body"
    set_joint_point(project, "Head", accepted.x + 5, accepted.y - 2)
    refresh_working_state(session, project)
    assert session.dirty and original["joint_placements"] != project.to_dict()["joint_placements"]
    cancel_calibration_session(session, project)
    assert project.to_dict() == original

    session = begin_calibration_session(project, "head_tilt_right")
    set_joint_point(project, "Head", accepted.x + 1, accepted.y)
    accept_calibration_session(session, project)
    assert placement_for(project, "Head").approved is True
    assert session.saved and not session.dirty


def test_divergent_attachment_discovery_is_template_agnostic(tmp_path: Path) -> None:
    _directory, project = _fixture_project(tmp_path)
    animation = GeneratedAnimation(
        "future nod",
        "future_animation_without_special_case",
        1.0,
        True,
        (
            GeneratedTrack(
                "Skeleton2D/Root/Body/Head",
                "rotation",
                "cubic",
                (
                    AnimationKey(0.0, 0.0),
                    AnimationKey(0.5, 0.1),
                    AnimationKey(1.0, 0.0),
                ),
            ),
        ),
    )
    relations = discover_divergent_attachments(project, [animation])
    head = next(item for item in relations if item.joint_name == "Head")
    assert head.parent_joint == "Body"
    assert head.template_ids == ("future_animation_without_special_case",)
    assert head.requires_guard


def test_idle_uses_grounded_scale_and_returns_exactly_to_rest(tmp_path: Path) -> None:
    directory, project = _fixture_project(tmp_path)
    idle_settings = next(
        item for item in project.animation_set.templates if item.template_id == "idle_breathing"
    )
    idle_settings.parameters["breathing_strength"] = "Natural"
    idle = generate_animation(
        idle_settings, project, purpose="preview", project_directory=directory
    )
    tracks = {item.property_name: item for item in idle.tracks}
    assert tracks["scale"].interpolation == "cubic"
    assert tracks["scale"].keys[0].value == (1.0, 1.0)
    assert tracks["scale"].keys[-1].value == (1.0, 1.0)
    position = tracks["position"]
    assert max(abs(key.value[1] - position.keys[0].value[1]) for key in position.keys) < 4
    anchor = body_grounding_anchor(project, directory)
    rest, inhale = evaluate_joint_matrices(project, idle, idle.duration / 2)
    local_anchor = rest["Body"].inverse().point(anchor)
    assert inhale["Body"].point(local_anchor) == pytest.approx(anchor, abs=1e-6)
    assert inhale["Head"].xx == pytest.approx(inhale["Body"].xx)
    assert inhale["Head"].yy == pytest.approx(inhale["Body"].yy)
    _, end = evaluate_joint_matrices(project, idle, idle.duration)
    assert end == rest
    assert sample_track(tracks["scale"], idle.duration / 2) == (1.016, 1.012)
    exhale = composite_animation_frame(directory, project, idle, 0.0)
    inhale = composite_animation_frame(directory, project, idle, idle.duration / 2)
    assert ImageChops.difference(exhale, inhale).getbbox() is not None
    perceptual = breathing_perceptual_metrics(directory, project, idle)
    assert perceptual.torso_width_change >= 4
    assert perceptual.shoulder_height_change >= 4
    assert perceptual.lowest_paw_y_change <= 1
    assert perceptual.passed


def test_generated_attachment_is_exportable_metadata_and_gates_both_directions(
    tmp_path: Path,
) -> None:
    directory, project = _fixture_project(tmp_path)
    source_paths = [
        project.resolve(
            directory,
            next(layer.texture_path for layer in project.assembly_layers if layer.slot == slot),
        )
        for slot in ("head", "body")
    ]
    source_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths]
    results = prepare_head_tilt_attachments(directory, project)
    treatment = project.attachment_treatments[0]
    assert treatment.method == "parent_underlay_coverage_guard"
    assert treatment.algorithm_version == 5
    assert treatment.provenance_version == 5
    assert treatment.mask_bounds is not None
    assert treatment.protected_region_bounds is not None
    body_z = next(item.z_index for item in project.assembly_layers if item.slot == "body")
    head_z = next(item.z_index for item in project.assembly_layers if item.slot == "head")
    detail_z = min(
        item.z_index
        for item in project.assembly_layers
        if item.slot.startswith(("ear_screen", "eye_screen"))
    )
    assert body_z < treatment.z_index < head_z < detail_z
    assert treatment.z_index == body_z + 1
    assert attachment_treatment_depth_is_valid(project, treatment)
    assert treatment.source_layer_hashes
    assert treatment.regeneration_provenance
    assert treatment.source_layer_ids
    assert treatment.regeneration_provenance["base_layers"] == ("preserved immutable and unmasked")
    assert treatment.coverage_policy == (
        "preserve_parent_alpha_under_child_with_2px_hidden_feather"
    )
    assert treatment.parent_layer_id
    assert treatment.child_layer_ids
    assert len(treatment.validated_backgrounds) == 4
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths] == source_hashes
    assert (directory / treatment.texture_path).is_file()
    assert set(results) == {"head_tilt_left", "head_tilt_right"}
    assert all(status.startswith("Passed") for status in results.values()), (
        results,
        treatment.verification_details,
    )
    assert head_treatment_is_current(directory, project)
    native_rest = composite_assembly(directory, project)
    set_head_treatment_enabled(project, False)
    assert (
        ImageChops.difference(native_rest, composite_assembly(directory, project)).getbbox() is None
    )
    set_head_treatment_enabled(project, True)
    last_animation = None
    last_extreme = 0.0
    for template_id in results:
        settings = next(
            item for item in project.animation_set.templates if item.template_id == template_id
        )
        animation = generate_animation(
            settings, project, purpose="preview", project_directory=directory
        )
        rotation = next(track for track in animation.tracks if track.property_name == "rotation")
        extreme = next(key.time for key in rotation.keys if key.value)
        last_animation, last_extreme = animation, extreme
        diagnostic = inspect_rendered_attachment(directory, project, animation, "Head", extreme)
        assert diagnostic.status == "ok"
        assert diagnostic.boundary_coverage >= 0.95
        assert diagnostic.largest_uncovered_component <= 2
        assert diagnostic.high_contrast_edge_length <= 8

    # The rejected cropped treatment cannot earn tolerance merely because metadata says a
    # treatment exists.
    treatment.method = "body_front_depth_occlusion"
    treatment.algorithm_version = 3
    treatment.provenance_version = 3
    treatment.z_index = (
        next(item.z_index for item in project.assembly_layers if item.slot == "head") - 1
    )
    assert last_animation is not None
    rejected_v3 = inspect_rendered_attachment(
        directory, project, last_animation, "Head", last_extreme
    )
    assert rejected_v3.status == "boundary"
    prepare_head_tilt_attachments(directory, project)
    set_head_treatment_enabled(project, False)
    assert project.attachment_treatments[0].enabled is False
    assert last_animation is not None
    untreated = inspect_rendered_attachment(
        directory, project, last_animation, "Head", last_extreme
    )
    assert untreated.status in {"gap", "fringe", "boundary"}, untreated
    set_head_treatment_enabled(project, True)

    godot = tmp_path / "godot"
    godot.mkdir()
    (godot / "project.godot").write_text('[application]\nconfig/name="Issue7"\n')
    exported = export_godot_rig(directory, project, godot, "generated/cat")
    scene = exported.scene_path.read_text(encoding="utf-8")
    manifest = exported.animation_manifest_path.read_text(encoding="utf-8")
    assert "AttachmentCoverageGuard" in scene
    assert "parent_underlay_coverage_guard" in manifest
    assert "texture_filter = 2" in scene
    assert (exported.output_directory / "verification_frames").is_dir()
    verifier = (exported.output_directory / "verify_rig.gd").read_text(encoding="utf-8")
    assert "rendered motion parity exceeded tolerance" in verifier
    assert "idle_breathing" in verifier
    assert "head_tilt_left" in verifier
    assert "head_tilt_right" in verifier
    animation_library = exported.animation_library_path.read_text(encoding="utf-8")
    assert 'NodePath("Skeleton2D/Root/Body:scale")' in animation_library
    assert "AttachmentCoverageGuard:visible" in animation_library


def test_v4_attachment_and_legacy_animation_approvals_migrate_to_stale(tmp_path: Path) -> None:
    directory, project = _fixture_project(tmp_path)
    prepare_head_tilt_attachments(directory, project)
    data = project.to_dict()
    data["attachment_treatment_format_version"] = 4
    data["animation_set"]["format_version"] = 3
    data["animation_set"]["templates"][0]["parameters"]["breathing_strength"] = "Natural"
    data["animation_verification_valid"] = True
    data["godot_export_status"] = "Godot Verified — Rig and animations"
    migrated = Project.from_dict(data)
    assert migrated.attachment_treatment_format_version == 5
    assert migrated.animation_set.format_version == 4
    assert migrated.animation_verification_valid is False
    assert migrated.godot_export_status == "Needs regeneration"
    assert migrated.attachment_treatments[0].verification_status == "Needs automatic fix"
    idle = next(
        item for item in migrated.animation_set.templates if item.template_id == "idle_breathing"
    )
    assert idle.parameters["breathing_strength"] == "Natural"


def test_head_tilt_preserves_body_and_moves_head_hierarchy_exactly_once(
    tmp_path: Path,
) -> None:
    directory, project = _fixture_project(tmp_path)
    prepare_head_tilt_attachments(directory, project)
    body = next(layer for layer in project.assembly_layers if layer.slot == "body")
    body_source_hash = hashlib.sha256(
        project.resolve(directory, body.texture_path).read_bytes()
    ).hexdigest()
    body_rest_raster = render_base_layer(directory, project, body).tobytes()

    for template_id in ("head_tilt_left", "head_tilt_right"):
        settings = next(
            item for item in project.animation_set.templates if item.template_id == template_id
        )
        animation = generate_animation(
            settings, project, purpose="preview", project_directory=directory
        )
        assert not any(
            track.target_path.endswith("/Body")
            and track.property_name in {"position", "rotation", "scale"}
            for track in animation.tracks
        )
        moving_layers = [
            layer
            for layer in project.assembly_layers
            if layer.slot
            in {
                "head",
                "ear_screen_left",
                "ear_screen_right",
                "eye_screen_left",
                "eye_screen_right",
            }
        ]
        for time in production_sample_times(animation):
            rest, animated = evaluate_joint_matrices(project, animation, time)
            assert animated["Body"] == rest["Body"]
            body_delta = layer_delta_transform(project, body.id, animation, time)
            assert body_delta.xx == 1.0
            assert body_delta.xy == 0.0
            assert body_delta.yx == 0.0
            assert body_delta.yy == 1.0
            assert body_delta.tx == 0.0
            assert body_delta.ty == 0.0
            assert render_base_layer(directory, project, body).tobytes() == body_rest_raster
            head_delta = layer_delta_transform(
                project,
                next(layer.id for layer in moving_layers if layer.slot == "head"),
                animation,
                time,
            )
            assert all(
                _affine_values(layer_delta_transform(project, layer.id, animation, time))
                == pytest.approx(_affine_values(head_delta), abs=1e-12)
                for layer in moving_layers
            )

    assert hashlib.sha256(
        project.resolve(directory, body.texture_path).read_bytes()
    ).hexdigest() == (body_source_hash)


def test_happy_bounce_fixture_remains_one_rigid_seam_free_group(tmp_path: Path) -> None:
    directory, project = _fixture_project(tmp_path)
    settings = next(
        item for item in project.animation_set.templates if item.template_id == "happy_bounce"
    )
    animation = generate_animation(
        settings, project, purpose="preview", project_directory=directory
    )
    midpoint = animation.duration / 2
    rigid_ids = [
        layer.id
        for layer in project.assembly_layers
        if layer.slot
        in {
            "body",
            "head",
            "ear_screen_left",
            "ear_screen_right",
            "eye_screen_left",
            "eye_screen_right",
        }
    ]
    deltas = [
        layer_delta_transform(project, layer_id, animation, midpoint) for layer_id in rigid_ids
    ]
    assert deltas and all(delta == deltas[0] for delta in deltas)
    start = composite_animation_frame(directory, project, animation, 0.0)
    end = composite_animation_frame(directory, project, animation, animation.duration)
    assert ImageChops.difference(start, end).getbbox() is None
