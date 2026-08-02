import os
from pathlib import Path

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_layer_studio.models.project import Project  # noqa: E402
from cat_layer_studio.views.modular_preview_view import ModularPreviewView  # noqa: E402


def test_add_select_nudge_and_undo_in_modular_preview(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    (tmp_path / "components").mkdir()
    component = tmp_path / "components" / "eye_screen_left_round.png"
    Image.new("RGBA", (16, 16), (255, 255, 255, 128)).save(component)
    project = Project("UI", "master.png", 16, 16)
    view = ModularPreviewView()
    view.set_project(tmp_path, project)
    view.add_component(component)
    application.processEvents()

    assert len(project.assembly_layers) == 1
    assert project.assembly_layers[0].slot == "eye_screen_left"
    view.layers.topLevelItem(0).setSelected(True)
    view.nudge(-1, 0)
    assert project.assembly_layers[0].offset_x == -1
    view.nudge(0.25, 0)
    assert project.assembly_layers[0].offset_x == -0.75
    view.undo()
    assert project.assembly_layers[0].offset_x == -1
    view.redo()
    assert project.assembly_layers[0].offset_x == -0.75
    view.deleteLater()
