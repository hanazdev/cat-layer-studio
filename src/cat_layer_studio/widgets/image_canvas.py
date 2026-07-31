from __future__ import annotations

from enum import Enum

from PIL import Image, ImageQt
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


class CanvasTool(Enum):
    PAN = "pan"
    KEEP = "keep"
    REMOVE = "remove"
    POINT = "point"


class ImageCanvas(QGraphicsView):
    coordinates_changed = Signal(int, int)
    point_selected = Signal(float, float)
    mask_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._image_item = QGraphicsPixmapItem()
        self._mask_item = QGraphicsPixmapItem()
        self.scene().addItem(self._image_item)
        self.scene().addItem(self._mask_item)
        self._mask_item.setOpacity(0.35)
        self._mask = QImage()
        self._tool = CanvasTool.PAN
        self._brush_size = 20
        self._drawing = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMouseTracking(True)
        self.setBackgroundBrush(QColor("#36383d"))

    def set_image(self, image: Image.Image) -> None:
        qimage = ImageQt.ImageQt(image.convert("RGBA"))
        self._image_item.setPixmap(QPixmap.fromImage(qimage))
        self.setSceneRect(self._image_item.boundingRect())
        if self._mask.isNull() or self._mask.size() != qimage.size():
            self._mask = QImage(qimage.size(), QImage.Format.Format_Grayscale8)
            self._mask.fill(0)
            self._update_mask_item()

    def set_background(self, name: str) -> None:
        colours = {
            "White": "#ffffff",
            "Black": "#000000",
            "Mid-grey": "#777777",
            "Magenta": "#ff00ff",
            "Checkerboard": "#36383d",
        }
        self.setBackgroundBrush(QColor(colours.get(name, "#36383d")))

    def fit_to_view(self) -> None:
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def actual_size(self) -> None:
        self.resetTransform()

    def set_tool(self, tool: CanvasTool) -> None:
        self._tool = tool
        self.setDragMode(
            QGraphicsView.DragMode.ScrollHandDrag
            if tool == CanvasTool.PAN
            else QGraphicsView.DragMode.NoDrag
        )

    def set_brush_size(self, size: int) -> None:
        self._brush_size = size

    def mask(self) -> Image.Image:
        memory = self._mask.constBits().tobytes()
        return Image.frombuffer(
            "L",
            (self._mask.width(), self._mask.height()),
            memory,
            "raw",
            "L",
            self._mask.bytesPerLine(),
            1,
        ).copy()

    def clear_mask(self) -> None:
        self._mask.fill(0)
        self._update_mask_item()
        self.mask_changed.emit()

    def invert_mask(self) -> None:
        self._mask.invertPixels()
        self._update_mask_item()
        self.mask_changed.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        point = self.mapToScene(event.position().toPoint())
        self.coordinates_changed.emit(round(point.x()), round(point.y()))
        if self._drawing:
            self._paint_mask(point)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        point = self.mapToScene(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and self._tool == CanvasTool.POINT:
            self.point_selected.emit(point.x(), point.y())
            return
        if event.button() == Qt.MouseButton.LeftButton and self._tool in {
            CanvasTool.KEEP,
            CanvasTool.REMOVE,
        }:
            self._drawing = True
            self._paint_mask(point)
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if self._drawing:
            self._drawing = False
            self.mask_changed.emit()
        super().mouseReleaseEvent(event)

    def _paint_mask(self, point: QPointF) -> None:
        painter = QPainter(self._mask)
        colour = QColor(255, 255, 255) if self._tool == CanvasTool.KEEP else QColor(0, 0, 0)
        painter.setPen(
            QPen(colour, self._brush_size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        painter.drawPoint(point)
        painter.end()
        self._update_mask_item()

    def _update_mask_item(self) -> None:
        overlay = QImage(self._mask.size(), QImage.Format.Format_RGBA8888)
        overlay.fill(Qt.GlobalColor.transparent)
        painter = QPainter(overlay)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(0, 0, self._mask)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(overlay.rect(), QColor(0, 200, 255, 180))
        painter.end()
        self._mask_item.setPixmap(QPixmap.fromImage(overlay))
