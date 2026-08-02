import json
import math
from pathlib import Path

import pytest
from PIL import Image

from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.assembly_service import (
    AssemblyHistory,
    apply_recommended_order,
    move_layer,
)
from cat_layer_studio.services.component_library_service import suggest_slot
from cat_layer_studio.services.composition_service import composite_assembly
from cat_layer_studio.services.layer_validation_service import validate_assembly
from cat_layer_studio.services.project_service import load_project, save_project


def _layer(identifier: str, texture: str, slot: str, z: int) -> AssemblyLayer:
    return AssemblyLayer(identifier, identifier.title(), texture, slot, z_index=z)


def test_assembly_layer_round_trip_and_legacy_project_migration(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    Image.new("RGBA", (8, 8)).save(tmp_path / "source" / "master.png")
    (tmp_path / "project.json").write_text(
        json.dumps({"name": "Legacy", "master_path": "source/master.png"}),
        encoding="utf-8",
    )
    _, legacy = load_project(tmp_path / "project.json")
    assert legacy.assembly_layers == []
    assert legacy.assembly_format_version == 1

    (tmp_path / "components").mkdir()
    Image.new("RGBA", (8, 8)).save(tmp_path / "components" / "tail.png")
    legacy.canvas_width = legacy.canvas_height = 8
    legacy.assembly_layers = [
        AssemblyLayer(
            "tail-id",
            "Tail",
            "components/tail.png",
            "tail",
            offset_x=0.25,
            pivot_x=2.5,
            pivot_y=6.25,
        )
    ]
    save_project(tmp_path, legacy)
    _, reopened = load_project(tmp_path / "project.json")
    assert reopened.assembly_layers[0].to_dict() == legacy.assembly_layers[0].to_dict()


def test_order_actions_and_recommended_ears_in_front() -> None:
    project = Project("Rig", "source/master.png")
    project.assembly_layers = [
        _layer("head", "components/head.png", "head", 10),
        _layer("ear", "components/ear.png", "ear_screen_left", 20),
        _layer("tail", "components/tail.png", "tail", 30),
    ]
    apply_recommended_order(project)
    assert {layer.slot: layer.z_index for layer in project.assembly_layers} == {
        "head": 30,
        "ear_screen_left": 40,
        "tail": 10,
    }
    move_layer(project.assembly_layers, "tail", "top")
    assert max(project.assembly_layers, key=lambda layer: layer.z_index).id == "tail"
    assert len({layer.z_index for layer in project.assembly_layers}) == 3


def test_history_undo_redo_copies_state() -> None:
    layers = [_layer("body", "components/body.png", "body", 10)]
    history = AssemblyHistory()
    history.reset(layers)
    layers[0].offset_x = 0.25
    history.commit(layers)
    layers[0].offset_x = 5
    history.commit(layers)
    assert history.undo()[0].offset_x == 0.25
    assert history.undo()[0].offset_x == 0
    assert history.redo()[0].offset_x == 0.25


def test_composite_respects_visibility_z_opacity_and_subpixel_offsets(tmp_path: Path) -> None:
    (tmp_path / "components").mkdir()
    red = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    red.putpixel((3, 3), (255, 0, 0, 255))
    red.save(tmp_path / "components" / "red.png")
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(tmp_path / "components" / "blue.png")
    project = Project("Composite", "master.png", 8, 8)
    project.assembly_layers = [
        _layer("blue", "components/blue.png", "body", 1),
        AssemblyLayer(
            "red",
            "Red",
            "components/red.png",
            "head",
            z_index=2,
            offset_x=0.25,
            opacity=0.5,
        ),
    ]
    output = composite_assembly(tmp_path, project)
    assert output.size == (8, 8)
    assert output.getpixel((3, 3)) != (0, 0, 255, 255)
    project.assembly_layers[1].visible = False
    assert composite_assembly(tmp_path, project).getpixel((3, 3)) == (0, 0, 255, 255)


def test_validation_exposes_failures_and_path_escape(tmp_path: Path) -> None:
    project = Project("Invalid", "master.png", 8, 8)
    project.assembly_layers = [
        _layer("same", "../escape.png", "head", 1),
        _layer("same", "components/missing.png", "head", 1),
    ]
    results = {result.name: result.status for result in validate_assembly(tmp_path, project)}
    assert results["Texture paths"] == "Failed"
    assert results["Unique layer IDs"] == "Failed"
    assert results["Slot assignments"] == "Needs attention"
    assert results["Draw order"] == "Needs attention"

    project.assembly_layers[0].offset_x = math.nan
    results = {result.name: result.status for result in validate_assembly(tmp_path, project)}
    assert results["Layer coordinates"] == "Failed"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("ear_left_upright", "ear_screen_left"),
        ("ear_screen_right_upright", "ear_screen_right"),
        ("left_eye_colourless", "eye_screen_left"),
        ("body-neutral", "body"),
    ],
)
def test_slot_suggestions_cover_acceptance_asset_names(filename: str, expected: str) -> None:
    assert suggest_slot(filename) == expected
