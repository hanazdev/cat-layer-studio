from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from cat_layer_studio.services.master_service import (
    normalise_master_to_canvas,
    save_png_atomic,
)


@pytest.mark.parametrize(
    ("source_size", "expected_bounds"),
    [
        ((1254, 1254), (0, 0, 512, 512)),
        ((1000, 500), (0, 128, 512, 384)),
        ((500, 1000), (128, 0, 384, 512)),
        ((512, 512), (0, 0, 512, 512)),
        ((256, 256), (0, 0, 512, 512)),
    ],
)
def test_normalise_master_fits_centres_and_returns_exact_canvas(
    source_size: tuple[int, int], expected_bounds: tuple[int, int, int, int]
) -> None:
    source = Image.new("RGBA", source_size, (10, 20, 30, 255))
    original = source.tobytes()
    result, scale = normalise_master_to_canvas(source, (512, 512))
    assert result.size == (512, 512)
    assert result.mode == "RGBA"
    assert result.getchannel("A").getbbox() == expected_bounds
    assert source.tobytes() == original
    assert source.width * scale <= 512
    assert source.height * scale <= 512


def test_normalise_master_preserves_transparency_and_exact_scale() -> None:
    source = Image.new("RGBA", (1254, 1254), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((100, 100, 1153, 1153), fill=(20, 30, 40, 180))
    result, scale = normalise_master_to_canvas(source, (512, 512))
    assert scale == pytest.approx(512 / 1254)
    assert result.getpixel((0, 0))[3] < 180
    assert result.getchannel("A").getextrema()[0] == 0


def test_atomic_png_refuses_to_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "master.png"
    destination.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        save_png_atomic(Image.new("RGBA", (2, 2)), destination)
    assert destination.read_bytes() == b"existing"
