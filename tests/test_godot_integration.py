import shutil
from pathlib import Path

import pytest
from PIL import Image

from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.godot_export_service import accept_export, export_godot_rig
from cat_layer_studio.services.godot_verification_service import verify_godot_export


def test_generated_rig_loads_and_replaces_a_slot_in_real_godot(tmp_path: Path) -> None:
    godot_executable = shutil.which("godot") or shutil.which("godot4")
    if not godot_executable:
        pytest.skip("Godot 4.6 is not installed")
    executable = Path(godot_executable)
    if executable.suffix.lower() in {".bat", ".cmd"}:
        candidates = sorted(executable.parent.glob("Godot*.exe"))
        if not candidates:
            pytest.skip("The Godot command is only a wrapper; choose an executable")
        executable = candidates[0]
    studio = tmp_path / "studio"
    components = studio / "components"
    components.mkdir(parents=True)
    Image.new("RGBA", (32, 32), (220, 80, 40, 255)).save(components / "body.png")
    Image.new("RGBA", (32, 32), (40, 120, 220, 180)).save(components / "head.png")
    for name in ("left_open", "right_open", "left_closed", "right_closed"):
        Image.new("RGBA", (32, 32), (30, 30, 30, 80)).save(components / f"{name}.png")
    project = Project("Integration", "master.png", 32, 32)
    project.assembly_layers = [
        AssemblyLayer(
            "body",
            "Body",
            "components/body.png",
            "body",
            z_index=20,
            attachment_joint="Body",
            pivot_x=16,
            pivot_y=20,
        ),
        AssemblyLayer(
            "head",
            "Head",
            "components/head.png",
            "head",
            z_index=30,
            offset_x=0.25,
            offset_y=-1,
            attachment_joint="Head",
            pivot_x=16,
            pivot_y=15,
        ),
        AssemblyLayer(
            "left-open",
            "Left open eye",
            "components/left_open.png",
            "eye_screen_left",
            z_index=40,
            attachment_joint="Head",
            asset_state="open",
        ),
        AssemblyLayer(
            "right-open",
            "Right open eye",
            "components/right_open.png",
            "eye_screen_right",
            z_index=41,
            attachment_joint="Head",
            asset_state="open",
        ),
        AssemblyLayer(
            "left-closed",
            "Left closed eye",
            "components/left_closed.png",
            "eye_screen_left",
            z_index=42,
            attachment_joint="Head",
            asset_state="closed",
        ),
        AssemblyLayer(
            "right-closed",
            "Right closed eye",
            "components/right_closed.png",
            "eye_screen_right",
            z_index=43,
            attachment_joint="Head",
            asset_state="closed",
        ),
    ]
    game = tmp_path / "game"
    game.mkdir()
    (game / "project.godot").write_text(
        '[application]\nconfig/name="CatLayerStudioFixture"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n',
        encoding="utf-8",
    )
    exported = export_godot_rig(studio, project, game, "assets/cats/modular/test")
    verification = verify_godot_export(
        executable,
        game,
        "res://assets/cats/modular/test/verify_rig.gd",
    )
    assert verification.passed, verification.output
    accept_export(exported)
