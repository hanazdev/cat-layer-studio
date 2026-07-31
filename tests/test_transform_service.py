from PIL import Image, ImageDraw

from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.transform_service import rasterise_transform


def test_translation_supports_subpixels_and_preserves_canvas() -> None:
    source = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((4, 4, 5, 5), fill=(255, 0, 0, 255))
    result = rasterise_transform(source, Transform(x=2.25, y=-1.5), (20, 20))
    assert result.size == (20, 20)
    assert result.mode == "RGBA"
    alpha = result.getchannel("A")
    assert alpha.getbbox() is not None
    centre_x = sum(x * alpha.getpixel((x, 8)) for x in range(20)) / sum(
        alpha.getpixel((x, 8)) for x in range(20)
    )
    assert 11 <= centre_x <= 13


def test_independent_scale_and_rotation_leave_source_unchanged() -> None:
    source = Image.new("RGBA", (8, 8), (1, 2, 3, 0))
    ImageDraw.Draw(source).rectangle((2, 3, 5, 4), fill=(10, 20, 30, 255))
    original = source.tobytes()
    result = rasterise_transform(
        source, Transform(scale_x=1.5, scale_y=0.75, rotation_degrees=12.5), (32, 32)
    )
    assert source.tobytes() == original
    assert result.getchannel("A").getbbox() is not None


def test_non_uniform_scale_warning_levels() -> None:
    assert Transform(scale_x=1.02, scale_y=1).divergence_level == "ok"
    assert Transform(scale_x=1.04, scale_y=1).divergence_level == "warning"
    assert Transform(scale_x=1.07, scale_y=1).divergence_level == "strong"
    assert Transform(scale_x=1.11, scale_y=1).divergence_level == "confirmation"
