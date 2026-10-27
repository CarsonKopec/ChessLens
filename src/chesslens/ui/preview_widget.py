"""Captured-frame preview with the board grid on top (spec §6.2 manual calibration, §6.7 left panel)."""

from __future__ import annotations

import chess
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from chesslens.capture.roi import Roi
from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import from_board_coords, grid_lines, screen_cell_to_square, to_board_coords

_HANDLE_RADIUS = 7
_DRAG_THRESHOLD = 4
_BOARD_COLOUR = QColor(0, 220, 120)
_UNCERTAIN_COLOUR = QColor(255, 170, 0)


def edge_labels(orientation: Orientation) -> list[tuple[tuple[float, float], str]]:
    """Where to write file letters (bottom row) and rank numbers (left column), in board squares.

    Ranks sit a little above the middle of their square so the bottom-left
    square can carry both labels without them overlapping.
    """
    files = [
        ((col + 0.5, 7.84), chess.FILE_NAMES[chess.square_file(screen_cell_to_square(7, col, orientation))])
        for col in range(8)
    ]
    ranks = [
        ((0.16, row + 0.35), chess.RANK_NAMES[chess.square_rank(screen_cell_to_square(row, 0, orientation))])
        for row in range(8)
    ]
    return files + ranks


class PreviewWidget(QWidget):
    """The latest frame, zoomed to the crop, with the calibrated board's grid and corner handles.

    Drag a corner handle to adjust the board, drag anywhere else to draw a
    new board rectangle, click a square to edit it in the position editor
    (right click clears it).
    """

    board_drawn = Signal(object)  # (x, y, w, h) in frame pixels
    corner_moved = Signal(int, object)  # corner index, (x, y) in frame pixels
    square_clicked = Signal(int, int, object)  # screen row, screen col, Qt.MouseButton

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(360, 360)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._image: QImage | None = None
        self._frame_size = (0, 0)
        self._view: Roi | None = None
        self._corners: np.ndarray | None = None
        self._orientation = Orientation.WHITE_BOTTOM
        self._uncertain: list[tuple[int, int, str]] = []
        self._message = "Pick the mirroring window and press Start capture."
        self._press: QPointF | None = None
        self._dragging_corner: int | None = None
        self._rubber: QRectF | None = None

    # -- state ---------------------------------------------------------------

    def show_frame(self, image: np.ndarray) -> None:
        h, w = image.shape[:2]
        self._image = QImage(image.data, w, h, image.strides[0], QImage.Format.Format_BGR888).copy()
        self._frame_size = (w, h)
        self.update()

    def set_view(self, roi: Roi | None) -> None:
        """Show only this part of the frame (the crop), enlarged."""
        self._view = roi
        self.update()

    def set_board(self, corners: np.ndarray | None, orientation: Orientation) -> None:
        self._corners = None if corners is None else np.float32(corners)
        self._orientation = orientation
        self.update()

    def set_uncertain(self, cells: list[tuple[int, int, str]]) -> None:
        """Mark squares the piece reading isn't sure about: (screen row, col, best guess or '')."""
        self._uncertain = list(cells)
        self.update()

    def set_message(self, text: str) -> None:
        """Dim the preview and show `text`, so a stale picture is never mistaken for live input."""
        self._message = text
        self.update()

    def clear(self, message: str) -> None:
        self._image = None
        self.set_message(message)

    # -- geometry ------------------------------------------------------------

    def _view_rect(self) -> tuple[int, int, int, int]:
        w, h = self._frame_size
        return self._view.to_pixels(w, h) if self._view else (0, 0, w, h)

    def _target(self) -> QRectF:
        _, _, vw, vh = self._view_rect()
        scale = min(self.width() / vw, self.height() / vh)
        return QRectF((self.width() - vw * scale) / 2, (self.height() - vh * scale) / 2, vw * scale, vh * scale)

    def _to_widget(self, x: float, y: float) -> QPointF:
        vx, vy, vw, _ = self._view_rect()
        t = self._target()
        scale = t.width() / vw
        return QPointF(t.x() + (x - vx) * scale, t.y() + (y - vy) * scale)

    def _to_frame(self, pos: QPointF) -> tuple[float, float]:
        """Widget point → frame pixels, clamped to the visible part so corners can't be dragged out of sight."""
        vx, vy, vw, vh = self._view_rect()
        t = self._target()
        scale = t.width() / vw
        x = min(max(vx + (pos.x() - t.x()) / scale, float(vx)), float(vx + vw))
        y = min(max(vy + (pos.y() - t.y()) / scale, float(vy)), float(vy + vh))
        return x, y

    def _handle_at(self, pos: QPointF) -> int | None:
        if self._corners is None:
            return None
        for i, (x, y) in enumerate(self._corners):
            c = self._to_widget(x, y)
            if (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2 <= (_HANDLE_RADIUS + 3) ** 2:
                return i
        return None

    def _cell_at(self, pos: QPointF) -> tuple[int, int] | None:
        if self._corners is None:
            return None
        u, v = to_board_coords(self._corners, [self._to_frame(pos)])[0]
        if 0 <= u < 8 and 0 <= v < 8:
            return int(v), int(u)
        return None

    # -- painting ------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(24, 24, 28))
        if self._image is not None:
            target = self._target()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(target, self._image, QRectF(*self._view_rect()))
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setClipRect(target)
            if self._corners is not None:
                self._paint_board(p)
            p.setClipping(False)
            if self._rubber is not None:
                p.setPen(QPen(QColor(255, 210, 0), 2, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(self._rubber)
        if self._message:
            if self._image is not None:
                p.fillRect(self.rect(), QColor(0, 0, 0, 160))
            p.setPen(QColor(235, 235, 235))
            font = p.font()
            font.setPointSize(12)
            p.setFont(font)
            p.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._message,
            )

    def _paint_board(self, p: QPainter) -> None:
        corners = self._corners
        for i, (a, b) in enumerate(grid_lines(corners)):
            outer = i in (0, 8, 9, 17)
            p.setPen(QPen(_BOARD_COLOUR, 2 if outer else 1))
            p.drawLine(self._to_widget(*a), self._to_widget(*b))

        # Coordinate labels just inside the bottom and left edges, so the orientation is visible at a glance.
        cell_px = (self._to_widget(*corners[1]).x() - self._to_widget(*corners[0]).x()) / 8
        font = QFont(self.font())
        font.setPixelSize(max(9, min(16, int(cell_px * 0.3))))
        font.setBold(True)
        p.setFont(font)
        for (u, v), text in edge_labels(self._orientation):
            centre = self._to_widget(*from_board_coords(corners, [(u, v)])[0])
            box = QRectF(centre.x() - 8, centre.y() - 8, 16, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QColor(255, 255, 255))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

        # Confidence overlay: an amber box and "?" (plus the best guess) on squares the reading doubts.
        for row, col, guess in self._uncertain:
            quad = [self._to_widget(*pt) for pt in from_board_coords(corners, [(col, row), (col + 1, row), (col + 1, row + 1), (col, row + 1)])]
            p.setPen(QPen(_UNCERTAIN_COLOUR, 3))
            p.setBrush(QColor(255, 170, 0, 60))
            p.drawPolygon(QPolygonF(quad))
            centre = self._to_widget(*from_board_coords(corners, [(col + 0.5, row + 0.5)])[0])
            box = QRectF(centre.x() - 14, centre.y() - 9, 28, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(_UNCERTAIN_COLOUR)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, f"{guess}?" if guess else "?")

        p.setPen(QPen(QColor(0, 0, 0), 1.5))
        p.setBrush(_BOARD_COLOUR)
        for x, y in corners:
            p.drawEllipse(self._to_widget(x, y), _HANDLE_RADIUS, _HANDLE_RADIUS)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_BOARD_COLOUR, 1))
        p.drawPolygon(QPolygonF([self._to_widget(x, y) for x, y in corners]))

    # -- mouse ---------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._image is None:
            return
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            cell = self._cell_at(pos)
            if cell is not None:
                self.square_clicked.emit(*cell, Qt.MouseButton.RightButton)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = pos
            self._dragging_corner = self._handle_at(pos)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._press is None:
            over_handle = self._handle_at(pos) is not None
            self.setCursor(Qt.CursorShape.SizeAllCursor if over_handle else Qt.CursorShape.CrossCursor)
            return
        if self._dragging_corner is not None:
            self.corner_moved.emit(self._dragging_corner, self._to_frame(pos))
            return
        if self._rubber is not None or (pos - self._press).manhattanLength() > _DRAG_THRESHOLD:
            t = self._target()
            clamp = QPointF(min(max(pos.x(), t.left()), t.right()), min(max(pos.y(), t.top()), t.bottom()))
            self._rubber = QRectF(self._press, clamp).normalized()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        press, rubber, corner = self._press, self._rubber, self._dragging_corner
        self._press, self._rubber, self._dragging_corner = None, None, None
        self.update()
        if corner is not None:
            return
        if rubber is not None:
            if rubber.width() >= 24 and rubber.height() >= 24:
                x0, y0 = self._to_frame(rubber.topLeft())
                x1, y1 = self._to_frame(rubber.bottomRight())
                self.board_drawn.emit((x0, y0, x1 - x0, y1 - y0))
            return
        cell = self._cell_at(press)
        if cell is not None:
            self.square_clicked.emit(*cell, Qt.MouseButton.LeftButton)
