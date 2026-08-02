from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_layer_studio.models.assembly_layer import AssemblyLayer  # noqa: E402
from cat_layer_studio.models.project import Project  # noqa: E402
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
