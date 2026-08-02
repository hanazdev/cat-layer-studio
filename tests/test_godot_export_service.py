import json
from pathlib import Path

import pytest
from PIL import Image

from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.godot_export_service import (
    accept_export,
    export_godot_rig,
    rollback_export,
)


def _project(root: Path) -> Project:
    (root / "components").mkdir(parents=True)
    Image.new("RGBA", (16, 16), (120, 30, 20, 255)).save(root / "components" / "head.png")
    project = Project("Generic", "master.png", 16, 16)
    project.assembly_layers = [
        AssemblyLayer(
            "head-id",
            "Neutral head",
            "components/head.png",
            "head",
            z_index=30,
            offset_x=0.25,
            offset_y=-1,
            attachment_joint="Head",
            pivot_x=8,
            pivot_y=9,
        )
    ]
    return project


def test_native_export_contains_generic_scene_manifest_and_runtime_api(tmp_path: Path) -> None:
    source = tmp_path / "studio"
    source.mkdir()
    project = _project(source)
    godot = tmp_path / "game"
    godot.mkdir()
    (godot / "project.godot").write_text('[application]\nconfig/name="Fixture"\n')

    result = export_godot_rig(source, project, godot, "assets/cats/modular/profile")
    scene = result.scene_path.read_text(encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    script = (result.output_directory / "script.gd").read_text(encoding="utf-8")
    assert '[node name="ModularCat2D" type="Node2D"]' in scene
    assert '[node name="Skeleton2D" type="Skeleton2D" parent="."]' in scene
    assert '[node name="Head" type="Bone2D"' in scene
    assert 'metadata/slot_name = "head"' in scene
    # Full-canvas visual local position is calculated from the chosen pivot (8, 9).
    assert "position = Vector2(0.25, -2)" in scene
    assert "C:\\" not in scene
    assert str(source) not in scene
    assert manifest["rig_profile"] == "adult_front_sitting"
    assert manifest["layers"][0]["offset_x"] == 0.25
    assert manifest["layers"][0]["texture_path"] == "textures/head.png"
    assert "func set_part(slot: StringName, texture: Texture2D) -> bool:" in script
    assert result.preview_path.is_file()
    accept_export(result)


def test_export_rejects_paths_outside_godot_project(tmp_path: Path) -> None:
    source = tmp_path / "studio"
    source.mkdir()
    project = _project(source)
    godot = tmp_path / "game"
    godot.mkdir()
    (godot / "project.godot").touch()
    with pytest.raises(ValueError, match="inside"):
        export_godot_rig(source, project, godot, "../outside")


def test_failed_verification_can_restore_previous_export(tmp_path: Path) -> None:
    source = tmp_path / "studio"
    source.mkdir()
    project = _project(source)
    godot = tmp_path / "game"
    output = godot / "assets" / "rig"
    output.mkdir(parents=True)
    (godot / "project.godot").touch()
    (output / "previous.txt").write_text("known-good")
    result = export_godot_rig(source, project, godot, "assets/rig")
    assert not (output / "previous.txt").exists()
    rollback_export(result)
    assert (output / "previous.txt").read_text() == "known-good"
