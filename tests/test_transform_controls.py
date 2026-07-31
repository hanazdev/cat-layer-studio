import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from cat_layer_studio.models.transform import Transform  # noqa: E402
from cat_layer_studio.services.image_loader import LoadedImage  # noqa: E402
from cat_layer_studio.views.fit_component_view import FitComponentView  # noqa: E402
from cat_layer_studio.widgets.fit_preview_dialog import FitPreviewDialog  # noqa: E402
from cat_layer_studio.widgets.transform_controls import TransformControls  # noqa: E402


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_programmatic_fit_keeps_unrounded_scale() -> None:
    _application()
    controls = TransformControls()
    scale = 512 / 1254
    controls.set_transform(Transform(scale_x=scale, scale_y=scale))
    assert controls.width.value() == 40.83
    assert controls.transform().scale_x == scale
    assert controls.transform().scale_y == scale
    controls._nudge(1, -1)
    assert controls.transform().scale_x == scale
    assert controls.transform().scale_y == scale


def test_fit_button_emits_request() -> None:
    _application()
    controls = TransformControls()
    requests: list[bool] = []
    controls.fit_inside_requested.connect(lambda: requests.append(True))
    controls.fit_inside_button.click()
    assert requests == [True]


def test_approved_fit_is_exact_and_undoable(monkeypatch) -> None:
    _application()
    view = FitComponentView()
    image = Image.new("RGBA", (1254, 1254), (0, 0, 0, 0))
    view.candidate = LoadedImage(image, "RGBA", image.size, "PNG", True)
    previous = Transform(x=7.25, y=-3.5)
    view.history = [previous]
    view.controls.set_transform(previous)
    monkeypatch.setattr(FitPreviewDialog, "exec", lambda _self: QDialog.DialogCode.Accepted)

    assert view.preview_fit_inside()
    fitted = view.controls.transform()
    assert fitted.scale_x == 512 / 1254
    assert fitted.scale_y == 512 / 1254
    assert fitted.x == fitted.y == fitted.rotation_degrees == 0

    view.undo()
    assert view.controls.transform() == previous
