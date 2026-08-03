from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from cat_layer_studio.models.animation import AnimationKey, GeneratedAnimation, GeneratedTrack
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.attachment_treatment import AttachmentTreatment
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.animation_inspection_service import inspect_rendered_attachment
from cat_layer_studio.services.attachment_treatment_service import (
    discover_divergent_attachments,
    prepare_animation_attachment_treatments,
    treatment_active,
)
from cat_layer_studio.services.composition_service import (
    composite_animation_frame,
    composite_assembly,
    project_render_layers,
    render_base_layer,
)
from cat_layer_studio.services.joint_placement_service import (
    accept_joint_placement,
    ensure_joint_placements,
    set_joint_point,
)
from cat_layer_studio.services.rig_hierarchy_service import layer_delta_transform


def _overlap_fixture(directory: Path) -> tuple[Project, GeneratedAnimation]:
    components = directory / "components"
    components.mkdir()
    body = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(body).ellipse((13, 20, 51, 63), fill=(235, 235, 235, 255))
    body.save(components / "body.png")
    head = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(head).ellipse((10, 4, 54, 42), fill=(238, 238, 238, 255))
    head.save(components / "head.png")
    project = Project("Overlap", "master.png", 64, 64)
    project.assembly_layers = [
        AssemblyLayer(
            "body",
            "Body",
            "components/body.png",
            "body",
            attachment_joint="Body",
            z_index=20,
        ),
        AssemblyLayer(
            "head",
            "Head",
            "components/head.png",
            "head",
            attachment_joint="Head",
            z_index=30,
        ),
    ]
    ensure_joint_placements(project)
    for joint, point in {
        "Root": (32, 32),
        "Body": (32, 42),
        "Head": (32, 27),
        "EarScreenLeft": (23, 14),
        "EarScreenRight": (41, 14),
        "Tail": (48, 48),
    }.items():
        set_joint_point(project, joint, *point)
        accept_joint_placement(project, joint)
    animation = GeneratedAnimation(
        "future overlap motion",
        "future_overlap_motion",
        1.0,
        True,
        (
            GeneratedTrack(
                "Skeleton2D/Root/Body/Head",
                "position",
                "cubic",
                (
                    AnimationKey(0.0, (0.0, -15.0)),
                    AnimationKey(0.5, (1.0, -16.0)),
                    AnimationKey(1.0, (0.0, -15.0)),
                ),
            ),
            GeneratedTrack(
                "Skeleton2D/Root/Body/Head",
                "rotation",
                "cubic",
                (AnimationKey(0.0, 0.0), AnimationKey(0.5, 0.12), AnimationKey(1.0, 0.0)),
            ),
            GeneratedTrack(
                "Skeleton2D/Root/Body/Head",
                "scale",
                "cubic",
                (
                    AnimationKey(0.0, (1.0, 1.0)),
                    AnimationKey(0.5, (1.02, 0.99)),
                    AnimationKey(1.0, (1.0, 1.0)),
                ),
            ),
        ),
    )
    return project, animation


def test_full_layers_survive_future_rotation_translation_scale_and_loops(
    tmp_path: Path,
) -> None:
    project, animation = _overlap_fixture(tmp_path)
    body = next(layer for layer in project.assembly_layers if layer.slot == "body")
    sources_before = {
        layer.id: hashlib.sha256((tmp_path / layer.texture_path).read_bytes()).hexdigest()
        for layer in project.assembly_layers
    }
    base_body = render_base_layer(tmp_path, project, body).tobytes()
    rest_before = composite_assembly(tmp_path, project)
    relations = discover_divergent_attachments(project, [animation])
    assert [(item.parent_joint, item.joint_name) for item in relations] == [("Body", "Head")]
    results = prepare_animation_attachment_treatments(tmp_path, project, [animation])
    assert results[animation.template_id].startswith("Passed")
    treatment = project.attachment_treatments[0]
    assert treatment.method == "parent_underlay_coverage_guard"
    assert body.z_index < treatment.z_index < next(
        layer.z_index for layer in project.assembly_layers if layer.slot == "head"
    )
    assert not treatment_active(project, treatment, animation, 0.0)
    assert treatment_active(project, treatment, animation, 0.5)
    assert not treatment_active(project, treatment, animation, animation.duration)

    body_delta = layer_delta_transform(project, body.id, animation, 0.5)
    assert (body_delta.xx, body_delta.xy, body_delta.yx, body_delta.yy) == (1, 0, 0, 1)
    assert (body_delta.tx, body_delta.ty) == (0, 0)
    assert render_base_layer(tmp_path, project, body).tobytes() == base_body
    assert (
        ImageChops.difference(rest_before, composite_assembly(tmp_path, project)).getbbox() is None
    )
    midpoint = composite_animation_frame(tmp_path, project, animation, 0.5)
    repeated_midpoint = composite_animation_frame(tmp_path, project, animation, 0.5)
    assert midpoint.tobytes() == repeated_midpoint.tobytes()
    returned = composite_animation_frame(tmp_path, project, animation, animation.duration)
    assert returned.tobytes() == rest_before.tobytes()
    diagnostic = inspect_rendered_attachment(tmp_path, project, animation, "Head", 0.5)
    assert diagnostic.status == "ok"
    assert diagnostic.coverage_deficit_pixels == 0
    assert {
        layer.id: hashlib.sha256((tmp_path / layer.texture_path).read_bytes()).hexdigest()
        for layer in project.assembly_layers
    } == sources_before


def test_attachment_preparation_rejects_a_guard_that_would_cross_a_native_layer(
    tmp_path: Path,
) -> None:
    project, animation = _overlap_fixture(tmp_path)
    head = next(layer for layer in project.assembly_layers if layer.slot == "head")
    head.z_index = 21

    results = prepare_animation_attachment_treatments(tmp_path, project, [animation])
    assert "no safe parent-underlay z-index" in results[animation.template_id]
    assert not project.attachment_treatments


def test_compositor_preserves_z_order_between_repeated_transform_runs(
    tmp_path: Path,
) -> None:
    components = tmp_path / "components"
    components.mkdir()
    specs = {
        "body": ((220, 30, 30, 255), (3, 3, 44, 44)),
        "head": ((30, 80, 230, 255), (7, 7, 40, 40)),
        "overlay": ((20, 200, 70, 255), (15, 15, 33, 33)),
        "detail": ((245, 210, 20, 255), (20, 20, 28, 28)),
        "guard": ((220, 30, 30, 255), (3, 3, 44, 44)),
    }
    for name, (colour, bounds) in specs.items():
        image = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle(bounds, fill=colour)
        image.save(components / f"{name}.png")

    project = Project("Ordering", "master.png", 48, 48)
    project.assembly_layers = [
        AssemblyLayer(
            "body", "Body", "components/body.png", "body", z_index=10, attachment_joint="Body"
        ),
        AssemblyLayer(
            "head", "Head", "components/head.png", "head", z_index=30, attachment_joint="Head"
        ),
        AssemblyLayer(
            "overlay",
            "Overlay",
            "components/overlay.png",
            "pattern",
            z_index=40,
            attachment_joint="Root",
        ),
        AssemblyLayer(
            "detail",
            "Detail",
            "components/detail.png",
            "expression",
            z_index=50,
            attachment_joint="Head",
        ),
    ]
    ensure_joint_placements(project)
    for joint, point in {
        "Root": (24, 24),
        "Body": (24, 29),
        "Head": (24, 17),
        "EarScreenLeft": (17, 10),
        "EarScreenRight": (31, 10),
        "Tail": (38, 36),
    }.items():
        set_joint_point(project, joint, *point)
        accept_joint_placement(project, joint)
    animation = GeneratedAnimation(
        "ordering",
        "ordering",
        1.0,
        True,
        (
            GeneratedTrack(
                "Skeleton2D/Root/Body/Head",
                "position",
                "cubic",
                (
                    AnimationKey(0.0, (0.0, -12.0)),
                    AnimationKey(0.5, (1.0, -13.0)),
                    AnimationKey(1.0, (0.0, -12.0)),
                ),
            ),
            GeneratedTrack(
                "Skeleton2D/Root/Body/Head",
                "rotation",
                "cubic",
                (AnimationKey(0.0, 0.0), AnimationKey(0.5, 0.05), AnimationKey(1.0, 0.0)),
            ),
            GeneratedTrack(
                "Skeleton2D/Root/Body/Head",
                "scale",
                "cubic",
                (
                    AnimationKey(0.0, (1.0, 1.0)),
                    AnimationKey(0.5, (1.02, 0.99)),
                    AnimationKey(1.0, (1.0, 1.0)),
                ),
            ),
        ),
    )
    treatment = AttachmentTreatment(
        "guard",
        "Head",
        "parent_underlay_coverage_guard",
        "components/guard.png",
        "Body",
        11,
        ("body", "head"),
        ("ordering",),
        provenance_version=5,
        algorithm_version=5,
        parent_layer_id="body",
        child_layer_ids=("head", "detail"),
    )
    project.attachment_treatments = [treatment]
    assert treatment_active(project, treatment, animation, 0.5)
    assert [item.id for item in project_render_layers(project)] == [
        "body",
        "guard",
        "head",
        "overlay",
        "detail",
    ]

    frame = np.asarray(composite_animation_frame(tmp_path, project, animation, 0.5))
    isolated: dict[str, np.ndarray] = {}
    for layer in project.assembly_layers:
        single = deepcopy(project)
        single.assembly_layers = [deepcopy(layer)]
        single.attachment_treatments = []
        isolated[layer.id] = np.asarray(
            composite_animation_frame(tmp_path, single, animation, 0.5)
        )[..., 3]

    masks = {
        "detail": isolated["detail"] == 255,
        "overlay": (isolated["overlay"] == 255) & (isolated["detail"] == 0),
        "head": (isolated["head"] == 255)
        & (isolated["overlay"] == 0)
        & (isolated["detail"] == 0),
    }
    expected = {
        "detail": lambda rgb: rgb[0] > 200 and rgb[1] > 170 and rgb[2] < 80,
        "overlay": lambda rgb: rgb[1] > 150 and rgb[1] > rgb[0] and rgb[1] > rgb[2],
        "head": lambda rgb: rgb[2] > 180 and rgb[2] > rgb[0] and rgb[2] > rgb[1],
    }
    for name, mask in masks.items():
        assert np.any(mask), name
        assert expected[name](frame[mask][0, :3]), name
