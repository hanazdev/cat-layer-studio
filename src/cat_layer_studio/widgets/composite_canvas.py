from __future__ import annotations

from PIL import Image, ImageQt
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)


class CompositeCanvas(QGraphicsView):
    pivot_selected = Signal(float, float)
    movement_point_changed = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._image = QGraphicsPixmapItem()
        self._pivot = QGraphicsEllipseItem(-6, -6, 12, 12)
        self._pivot.setPen(QPen(QColor("#00e5ff"), 2))
        self._pivot.setBrush(QColor(0, 229, 255, 80))
        self._pivot.setVisible(False)
        self._suggested = QGraphicsEllipseItem(-7, -7, 14, 14)
        self._suggested.setPen(QPen(QColor("#42a5f5"), 2))
        self._suggested.setBrush(QColor(0, 0, 0, 0))
        self._suggested.setVisible(False)
        self._template = QGraphicsEllipseItem(-5, -5, 10, 10)
        self._template.setPen(QPen(QColor("#eeeeee"), 1, Qt.PenStyle.DashLine))
        self._template.setVisible(False)
        self._connection = QGraphicsLineItem()
        self._connection.setPen(QPen(QColor("#ffd54f"), 2, Qt.PenStyle.DashLine))
        self._connection.setVisible(False)
        self.scene().addItem(self._image)
        self.scene().addItem(self._pivot)
        self.scene().addItem(self._suggested)
        self.scene().addItem(self._template)
        self.scene().addItem(self._connection)
        self._placing_pivot = False
        self._dragging_pivot = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self.set_background("Checkerboard")

    def set_image(self, image: Image.Image) -> None:
        qimage = ImageQt.ImageQt(image.convert("RGBA"))
        self._image.setPixmap(QPixmap.fromImage(qimage))
        self.setSceneRect(self._image.boundingRect())

    def show_pivot(self, x: float | None, y: float | None) -> None:
        visible = x is not None and y is not None
        self._pivot.setVisible(visible)
        if visible:
            self._pivot.setPos(float(x), float(y))

    def show_movement_points(
        self,
        current: tuple[float, float] | None,
        suggested: tuple[float, float] | None = None,
        template: tuple[float, float] | None = None,
    ) -> None:
        self.show_pivot(*(current or (None, None)))
        for item, point in ((self._suggested, suggested), (self._template, template)):
            item.setVisible(point is not None and point != current)
            if point is not None:
                item.setPos(*point)

    def show_joint_connection(
        self, parent: tuple[float, float] | None, child: tuple[float, float] | None
    ) -> None:
        self._connection.setVisible(parent is not None and child is not None)
        if parent is not None and child is not None:
            self._connection.setLine(parent[0], parent[1], child[0], child[1])

    def place_pivot(self) -> None:
        self._placing_pivot = True
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

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

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self.scale(
            1.2 if event.angleDelta().y() > 0 else 1 / 1.2,
            1.2 if event.angleDelta().y() > 0 else 1 / 1.2,
        )

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if self._placing_pivot and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            self._placing_pivot = False
            self.unsetCursor()
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.pivot_selected.emit(point.x(), point.y())
            return
        if event.button() == Qt.MouseButton.LeftButton and self._pivot.isVisible():
            point = self.mapToScene(event.position().toPoint())
            pivot = self._pivot.pos()
            if abs(point.x() - pivot.x()) <= 10 and abs(point.y() - pivot.y()) <= 10:
                self._dragging_pivot = True
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if self._dragging_pivot:
            point = self.mapToScene(event.position().toPoint())
            self._pivot.setPos(point)
            self.movement_point_changed.emit(point.x(), point.y())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if self._dragging_pivot:
            self._dragging_pivot = False
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            return
        super().mouseReleaseEvent(event)
