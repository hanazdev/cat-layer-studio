import pytest
from PIL import Image, ImageDraw

from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.transform_service import (
    calculate_fit_inside_scale,
    fit_inside_transform,
    rasterise_transform,
)


@pytest.mark.parametrize(
    ("source_size", "canvas_size", "expected"),
    [
        ((1254, 1254), (512, 512), 512 / 1254),
        ((1024, 1024), (512, 512), 0.5),
        ((1000, 500), (512, 512), 0.512),
        ((500, 1000), (512, 512), 0.512),
        ((512, 512), (512, 512), 1.0),
        ((256, 256), (512, 512), 2.0),
    ],
)
def test_calculate_fit_inside_scale(
    source_size: tuple[int, int], canvas_size: tuple[int, int], expected: float
) -> None:
    assert calculate_fit_inside_scale(source_size, canvas_size) == pytest.approx(expected)


@pytest.mark.parametrize("bad_size", [(0, 1), (1, 0), (-1, 1), (1, -1)])
def test_calculate_fit_inside_scale_rejects_invalid_dimensions(
    bad_size: tuple[int, int],
) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        calculate_fit_inside_scale(bad_size, (512, 512))
    with pytest.raises(ValueError, match="greater than zero"):
        calculate_fit_inside_scale((512, 512), bad_size)


def test_fit_inside_transform_is_uniform_centred_and_within_canvas() -> None:
    source_size = (1000, 500)
    transform = fit_inside_transform(source_size, (512, 512))
    assert transform.scale_x == transform.scale_y
    assert transform.x == transform.y == transform.rotation_degrees == 0
    assert source_size[0] * transform.scale_x <= 512
    assert source_size[1] * transform.scale_y <= 512


def test_fit_inside_raster_preserves_transparency_and_source() -> None:
    source = Image.new("RGBA", (100, 50), (20, 30, 40, 0))
    ImageDraw.Draw(source).rectangle((10, 10, 89, 39), fill=(20, 30, 40, 180))
    original = source.tobytes()
    result = rasterise_transform(source, fit_inside_transform(source.size, (50, 50)), (50, 50))
    assert source.tobytes() == original
    assert result.mode == "RGBA"
    assert result.getpixel((0, 0))[3] == 0


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
