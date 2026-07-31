import numpy as np
from PIL import Image, ImageDraw

from cat_layer_studio.services.alpha_validation_service import clear_hidden_rgb, validate_alpha
from cat_layer_studio.services.comparison_service import difference, overlay
from cat_layer_studio.services.image_loader import normalise_to_canvas
from cat_layer_studio.services.mask_service import apply_mask


def test_normalise_centres_without_stretching() -> None:
    image = Image.new("RGBA", (2, 4), (255, 0, 0, 255))
    result = normalise_to_canvas(image, (8, 8))
    assert result.size == (8, 8)
    assert result.getchannel("A").getbbox() == (3, 2, 5, 6)


def test_mask_preserves_existing_alpha() -> None:
    image = Image.new("RGBA", (4, 4), (20, 30, 40, 128))
    mask = Image.new("L", (4, 4), 0)
    ImageDraw.Draw(mask).rectangle((0, 0, 1, 3), fill=255)
    result = apply_mask(image, mask)
    alpha = np.asarray(result.getchannel("A"))
    assert np.all(alpha[:, :2] == 128)
    assert np.all(alpha[:, 2:] == 0)


def test_alpha_report_flags_edge_contact_and_hidden_rgb_can_be_cleared() -> None:
    image = Image.new("RGBA", (4, 4), (100, 100, 100, 0))
    image.putpixel((0, 1), (255, 0, 0, 255))
    report = validate_alpha(image)
    assert report.has_visible_pixels
    assert report.touches_edge
    cleared = clear_hidden_rgb(image)
    assert cleared.getpixel((3, 3)) == (0, 0, 0, 0)


def test_overlay_and_difference_keep_rgba_canvas() -> None:
    master = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    candidate = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    assert overlay(master, candidate).size == (4, 4)
    assert difference(master, candidate, highlighted=True).mode == "RGBA"
