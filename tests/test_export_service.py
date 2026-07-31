from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.export_service import export_component
from cat_layer_studio.services.transform_service import rasterise_transform


def test_full_canvas_export_reimports_as_approved_preview(tmp_path: Path) -> None:
    source = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    ImageDraw.Draw(source).ellipse((4, 6, 15, 14), fill=(120, 80, 40, 220))
    transform = Transform(x=3.25, y=-2, scale_x=1.1, scale_y=0.98, rotation_degrees=4.2)
    expected = rasterise_transform(source, transform, (64, 64))
    destination = tmp_path / "tail.png"
    result = export_component(source, transform, (64, 64), destination)
    with Image.open(destination) as reopened:
        assert reopened.convert("RGBA").tobytes() == expected.tobytes()
        assert reopened.size == (64, 64)
        assert reopened.mode == "RGBA"
    assert result.godot_position == (0, 0)
    assert result.godot_scale == (1, 1)
    assert result.godot_rotation == 0


def test_export_does_not_overwrite_without_confirmation(tmp_path: Path) -> None:
    destination = tmp_path / "component.png"
    destination.write_bytes(b"existing")
    with pytest.raises(FileExistsError, match="Confirm replacement"):
        export_component(Image.new("RGBA", (2, 2)), Transform(), (4, 4), destination)
