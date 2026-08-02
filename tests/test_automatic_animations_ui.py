from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_layer_studio.models.assembly_layer import AssemblyLayer  # noqa: E402
from cat_layer_studio.models.project import Project  # noqa: E402
from cat_layer_studio.views.automatic_animations_view import (  # noqa: E402
    AutomaticAnimationsView,
)


def test_workspace_generates_previews_and_persists_parameter_history(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    components = tmp_path / "components"
    components.mkdir()
    Image.new("RGBA", (32, 32), (180, 90, 40, 255)).save(components / "body.png")
    project = Project("Animated UI", "master.png", 32, 32)
    project.assembly_layers = [
        AssemblyLayer(
            "body",
            "Body",
            "components/body.png",
            "body",
            attachment_joint="Body",
            pivot_x=16,
            pivot_y=20,
        )
    ]
    view = AutomaticAnimationsView()
    view.set_project(tmp_path, project)
    application.processEvents()

    assert view.animation_choice.count() == 7
    assert view.current_animation().name == "idle"
    assert project.animation_set.compatibility_status["blink"].startswith(
        "Blink cannot be generated yet."
    )
    original = project.animation_set.templates[0].parameters["breathing_amount"]
    view._parameter_changed("breathing_amount", "Expressive")
    assert project.animation_set.templates[0].parameters["breathing_amount"] == "Expressive"
    view.undo()
    assert project.animation_set.templates[0].parameters["breathing_amount"] == original
    view.deleteLater()
