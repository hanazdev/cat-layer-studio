from __future__ import annotations

from PIL import Image, ImageDraw, ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.transform_service import rasterise_transform


def _checkerboard(size: tuple[int, int], cell: int = 12) -> Image.Image:
    image = Image.new("RGBA", size, (238, 238, 238, 255))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(205, 205, 205, 255))
    return image


def _panel(
    source: Image.Image,
    canvas_size: tuple[int, int],
    transform: Transform,
    show_whole_source: bool,
) -> QPixmap:
    panel_size = 280
    stage_size = (
        max(source.width, canvas_size[0]) if show_whole_source else canvas_size[0],
        max(source.height, canvas_size[1]) if show_whole_source else canvas_size[1],
    )
    scale = min((panel_size - 20) / stage_size[0], (panel_size - 20) / stage_size[1])
    stage_pixels = (max(1, round(stage_size[0] * scale)), max(1, round(stage_size[1] * scale)))
    panel = _checkerboard((panel_size, panel_size))
    origin = ((panel_size - stage_pixels[0]) // 2, (panel_size - stage_pixels[1]) // 2)

    if show_whole_source:
        shown = source.convert("RGBA").resize(
            (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
            Image.Resampling.LANCZOS,
        )
        panel.alpha_composite(
            shown,
            (origin[0] + (stage_pixels[0] - shown.width) // 2,
             origin[1] + (stage_pixels[1] - shown.height) // 2),
        )
    else:
        fitted = rasterise_transform(source, transform, canvas_size)
        shown = fitted.resize(stage_pixels, Image.Resampling.LANCZOS)
        panel.alpha_composite(shown, origin)

    draw = ImageDraw.Draw(panel)
    canvas_pixels = (round(canvas_size[0] * scale), round(canvas_size[1] * scale))
    canvas_origin = (
        origin[0] + (stage_pixels[0] - canvas_pixels[0]) // 2,
        origin[1] + (stage_pixels[1] - canvas_pixels[1]) // 2,
    )
    draw.rectangle(
        (*canvas_origin, canvas_origin[0] + canvas_pixels[0], canvas_origin[1] + canvas_pixels[1]),
        outline=(0, 190, 255, 255),
        width=3,
    )
    if not show_whole_source:
        fitted_size = (round(source.width * transform.scale_x * scale),
                       round(source.height * transform.scale_y * scale))
        fitted_origin = (
            origin[0] + (stage_pixels[0] - fitted_size[0]) // 2,
            origin[1] + (stage_pixels[1] - fitted_size[1]) // 2,
        )
        draw.rectangle(
            (*fitted_origin, fitted_origin[0] + fitted_size[0], fitted_origin[1] + fitted_size[1]),
            outline=(255, 154, 46, 255),
            width=2,
        )
    return QPixmap.fromImage(ImageQt.ImageQt(panel))


class FitPreviewDialog(QDialog):
    """Before-and-after approval for the whole-image fit."""

    def __init__(
        self,
        source: Image.Image,
        canvas_size: tuple[int, int],
        transform: Transform,
        *,
        apply_label: str = "Apply fit",
        keep_label: str = "Keep current size",
        window_title: str = "Preview whole-image fit",
        subject_label: str = "Original image",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.setModal(True)
        self.choice = "cancel"
        fitted_size = (
            source.width * transform.scale_x,
            source.height * transform.scale_y,
        )

        heading = QLabel(
            f"{subject_label}: {source.width} × {source.height}\n"
            f"Locked canvas: {canvas_size[0]} × {canvas_size[1]}\n"
            f"Suggested resize: {transform.scale_x * 100:.2f}%\n"
            f"Resulting visible bounds: approximately {fitted_size[0]:.0f} × "
            f"{fitted_size[1]:.0f}"
        )
        heading.setWordWrap(True)
        previews = QHBoxLayout()
        for title, pixmap, caption in (
            ("Before", _panel(source, canvas_size, Transform(), True),
             "Blue: locked canvas"),
            ("After", _panel(source, canvas_size, transform, False),
             "Blue: canvas · Orange: fitted bounds"),
        ):
            column = QVBoxLayout()
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image = QLabel()
            image.setPixmap(pixmap)
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            note = QLabel(caption)
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(label)
            column.addWidget(image)
            column.addWidget(note)
            previews.addLayout(column)

        explanation = QLabel(
            f"Cat Layer Studio can resize this image uniformly to "
            f"{transform.scale_x * 100:.2f}% and centre it. Nothing will be cropped or stretched."
        )
        explanation.setWordWrap(True)
        if transform.scale_x > 1:
            explanation.setText(
                explanation.text()
                + " This image is smaller than the locked canvas; enlarging it may soften the "
                "artwork."
            )
        alpha = source.convert("RGBA").getchannel("A")
        edge_content = any(
            alpha.crop(box).getbbox() is not None
            for box in (
                (0, 0, source.width, 1),
                (0, source.height - 1, source.width, source.height),
                (0, 0, 1, source.height),
                (source.width - 1, 0, source.width, source.height),
            )
        )
        if edge_content:
            explanation.setText(
                explanation.text()
                + " Content touches a source edge, so it may already be cropped in the imported "
                "file."
            )

        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton(apply_label, QDialogButtonBox.ButtonRole.AcceptRole)
        self.keep_button = buttons.addButton(keep_label, QDialogButtonBox.ButtonRole.RejectRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button.clicked.connect(self._apply)
        self.keep_button.clicked.connect(self._keep)
        cancel.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addLayout(previews)
        layout.addWidget(explanation)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        self.choice = "apply"
        self.accept()

    def _keep(self) -> None:
        self.choice = "keep"
        self.reject()
