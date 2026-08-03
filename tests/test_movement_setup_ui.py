from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_layer_studio.models.assembly_layer import AssemblyLayer  # noqa: E402
from cat_layer_studio.models.project import Project  # noqa: E402
from cat_layer_studio.services.animation_service import default_animation_set  # noqa: E402
from cat_layer_studio.services.joint_placement_service import placement_for  # noqa: E402
from cat_layer_studio.views.movement_setup_view import MovementSetupView  # noqa: E402


def test_drag_numeric_nudge_undo_reset_and_accept(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    components = tmp_path / "components"
    components.mkdir()
    for name, bounds in (("body", (10, 25, 38, 47)), ("head", (8, 8, 40, 31))):
        image = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle(bounds, fill=(180, 110, 70, 255))
        image.save(components / f"{name}.png")
    project = Project("Movement UI", "master.png", 48, 48)
    project.assembly_layers = [
        AssemblyLayer(
            "body",
            "Body",
            "components/body.png",
            "body",
            attachment_joint="Body",
        ),
        AssemblyLayer(
            "head",
            "Head",
            "components/head.png",
            "head",
            attachment_joint="Head",
        ),
    ]
    view = MovementSetupView()
    view.set_project(tmp_path, project)
    application.processEvents()
    placement = placement_for(project, "Head")
    assert placement is not None and placement.suggestion_x is not None
    original = (placement.x, placement.y)

    view.nudge(0.25, 0)
    assert placement_for(project, "Head").x == original[0] + 0.25
    view.undo()
    assert placement_for(project, "Head").x == original[0]
    view._dragged(22.5, 27.25)
    assert (placement_for(project, "Head").x, placement_for(project, "Head").y) == (
        22.5,
        27.25,
    )
    view.reset_to_suggestion()
    view.accept()
    assert placement_for(project, "Head").approved is True
    view.deleteLater()


def test_head_tilt_context_is_locked_temporary_and_has_visible_recovery(tmp_path: Path) -> None:
    _application = QApplication.instance() or QApplication([])
    components = tmp_path / "components"
    components.mkdir()
    for name, bounds in (("body", (8, 22, 40, 47)), ("head", (7, 6, 41, 29))):
        image = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle(bounds, fill=(210, 170, 130, 255))
        image.save(components / f"{name}.png")
    project = Project("Context", "master.png", 48, 48)
    project.animation_set = default_animation_set()
    project.assembly_layers = [
        AssemblyLayer("body", "Body", "components/body.png", "body", attachment_joint="Body"),
        AssemblyLayer(
            "head",
            "Head",
            "components/head.png",
            "head",
            attachment_joint="Head",
            z_index=1,
        ),
    ]
    view = MovementSetupView()
    view.set_project(tmp_path, project)
    accepted = placement_for(project, "Head")
    accepted.approved = True
    accepted.last_approved_x, accepted.last_approved_y = accepted.x, accepted.y
    original = project.to_dict()
    saves: list[bool] = []
    view.project_changed.connect(lambda: saves.append(True))
    view.open_context("head_tilt_left")
    assert view.joint_choice.count() == 1
    assert view.joint_choice.currentText() == "Head"
    assert "Head\n├── Ears\n└── Eyes" in view.moving_hierarchy.text()
    for button in (
        view.undo_button,
        view.redo_button,
        view.reset_suggestion_button,
        view.restore_accepted_button,
        view.cancel_button,
        view.accept_button,
    ):
        assert button.isHidden() is False
    view.nudge(1, 0)
    assert saves == []
    view.cancel_all_changes()
    assert project.to_dict() == original
    assert saves == [True]
    view.deleteLater()
