"""Digital chessboard for viewing and correcting a position (spec §6.7 centre panel)."""

from __future__ import annotations

import chess
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QSizePolicy, QToolButton, QWidget

from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import screen_cell_to_square, square_to_screen_cell

LIGHT_SQUARE = QColor(240, 217, 181)
DARK_SQUARE = QColor(181, 136, 99)
LAST_MOVE_TINT = QColor(205, 210, 60, 120)
PIECE_FONT = "Segoe UI Symbol"
# Unicode solid ("black") chess glyphs; the outline ("white") glyph for each is 6 code points lower.
_SOLID_GLYPHS = {
    chess.KING: "♚",
    chess.QUEEN: "♛",
    chess.ROOK: "♜",
    chess.BISHOP: "♝",
    chess.KNIGHT: "♞",
    chess.PAWN: "♟",
}


def _glyph_path(text: str, font: QFont, offset: QPointF) -> QPainterPath:
    path = QPainterPath()
    path.addText(0, 0, font, text)
    # The glyphs have overlapping contours; odd-even filling turns parts of them inside out.
    path.setFillRule(Qt.FillRule.WindingFill)
    path.translate(offset)
    return path


def draw_piece(painter: QPainter, rect: QRectF, piece: chess.Piece) -> None:
    """Black pieces: the solid glyph in black. White pieces: the solid glyph in white as the body,
    with the outline glyph in black on top for the details."""
    font = QFont(PIECE_FONT)
    font.setPixelSize(max(8, int(rect.height() * 0.8)))
    solid_text = _SOLID_GLYPHS[piece.piece_type]
    offset = rect.center() - _glyph_path(solid_text, font, QPointF()).boundingRect().center()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if piece.color == chess.WHITE:
        painter.fillPath(_glyph_path(solid_text, font, offset), QColor(252, 252, 250))
        painter.fillPath(_glyph_path(chr(ord(solid_text) - 6), font, offset), QColor(25, 25, 25))
    else:
        painter.fillPath(_glyph_path(solid_text, font, offset), QColor(25, 25, 25))
    painter.restore()


def piece_icon(piece: chess.Piece | None, size: int = 36) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(200, 185, 160))
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 5, 5)
    if piece is None:
        p.setPen(QPen(QColor(160, 30, 30), 3))
        m = size * 0.28
        p.drawLine(QPointF(m, m), QPointF(size - m, size - m))
        p.drawLine(QPointF(size - m, m), QPointF(m, size - m))
    else:
        draw_piece(p, QRectF(0, 0, size, size), piece)
    p.end()
    return QIcon(pixmap)


class BoardEditor(QWidget):
    """Clickable 8×8 board showing a position, drawn the same way round as the phone.

    Mouse: left click applies the palette tool, right click clears.
    Keyboard: arrows move the cursor, K Q R B N P place white pieces,
    k q r b n p black ones, Delete clears, Space applies the palette tool.
    """

    square_clicked = Signal(int, object)  # chess.Square, Qt.MouseButton
    square_typed = Signal(int, object)  # chess.Square, chess.Piece | None

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(280, 280)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Position editor board")
        self._board = chess.BaseBoard()
        self._orientation = Orientation.WHITE_BOTTOM
        self._cursor = chess.E2
        self._arrow: chess.Move | None = None
        self._last_move: chess.Move | None = None

    def sizeHint(self) -> QSize:
        return QSize(400, 400)

    def set_position(self, board: chess.BaseBoard) -> None:
        self._board = board.copy()
        self.update()

    def set_arrow(self, move: chess.Move | None) -> None:
        """Draw the engine's best move as an arrow (None removes it)."""
        self._arrow = move
        self.update()

    def set_last_move(self, move: chess.Move | None) -> None:
        """Tint the from and to squares of the game's last move (None removes it)."""
        self._last_move = move
        self.update()

    def set_orientation(self, orientation: Orientation) -> None:
        self._orientation = orientation
        self.update()

    def _board_rect(self) -> QRectF:
        side = min(self.width(), self.height()) - 2
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def _cell_rect(self, row: int, col: int) -> QRectF:
        b = self._board_rect()
        s = b.width() / 8
        return QRectF(b.x() + col * s, b.y() + row * s, s, s)

    def _square_at(self, pos: QPointF) -> chess.Square | None:
        b = self._board_rect()
        if not b.contains(pos):
            return None
        s = b.width() / 8
        col = min(7, int((pos.x() - b.x()) / s))
        row = min(7, int((pos.y() - b.y()) / s))
        return screen_cell_to_square(row, col, self._orientation)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        label_font = QFont(self.font())
        label_font.setPixelSize(max(8, int(self._board_rect().width() / 8 * 0.17)))
        for row in range(8):
            for col in range(8):
                rect = self._cell_rect(row, col)
                light = (row + col) % 2 == 0
                p.fillRect(rect, LIGHT_SQUARE if light else DARK_SQUARE)
                square = screen_cell_to_square(row, col, self._orientation)
                if self._last_move is not None and square in (self._last_move.from_square, self._last_move.to_square):
                    p.fillRect(rect, LAST_MOVE_TINT)
                p.setFont(label_font)
                p.setPen(DARK_SQUARE if light else LIGHT_SQUARE)
                inner = rect.adjusted(3, 1, -3, -1)
                if col == 0:
                    p.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, chess.RANK_NAMES[chess.square_rank(square)])
                if row == 7:
                    p.drawText(inner, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, chess.FILE_NAMES[chess.square_file(square)])
                piece = self._board.piece_at(square)
                if piece is not None:
                    draw_piece(p, rect, piece)
        if self._arrow is not None:
            self._paint_arrow(p, self._arrow)
        if self.hasFocus():
            row, col = square_to_screen_cell(self._cursor, self._orientation)
            p.setPen(QPen(QColor(20, 110, 220), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self._cell_rect(row, col).adjusted(1.5, 1.5, -1.5, -1.5))

    def _paint_arrow(self, p: QPainter, move: chess.Move) -> None:
        start = self._cell_rect(*square_to_screen_cell(move.from_square, self._orientation)).center()
        end = self._cell_rect(*square_to_screen_cell(move.to_square, self._orientation)).center()
        cell = self._board_rect().width() / 8
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / length, dy / length
        head = cell * 0.38
        shaft_end = QPointF(end.x() - ux * head * 0.8, end.y() - uy * head * 0.8)
        colour = QColor(21, 120, 27, 190)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(colour, cell * 0.16)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(start, shaft_end)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        left = QPointF(end.x() - ux * head - uy * head * 0.6, end.y() - uy * head + ux * head * 0.6)
        right = QPointF(end.x() - ux * head + uy * head * 0.6, end.y() - uy * head - ux * head * 0.6)
        p.drawPolygon(QPolygonF([end, left, right]))
        p.restore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        square = self._square_at(event.position())
        if square is None:
            return
        self._cursor = square
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.update()
        self.square_clicked.emit(square, event.button())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key, text = event.key(), event.text()
        moves = {Qt.Key.Key_Left: (0, -1), Qt.Key.Key_Right: (0, 1), Qt.Key.Key_Up: (-1, 0), Qt.Key.Key_Down: (1, 0)}
        if key in moves:
            row, col = square_to_screen_cell(self._cursor, self._orientation)
            dr, dc = moves[key]
            self._cursor = screen_cell_to_square(min(7, max(0, row + dr)), min(7, max(0, col + dc)), self._orientation)
            self.update()
        elif text and text in "KQRBNPkqrbnp":
            self.square_typed.emit(self._cursor, chess.Piece.from_symbol(text))
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) or text == ".":
            self.square_typed.emit(self._cursor, None)
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.square_clicked.emit(self._cursor, Qt.MouseButton.LeftButton)
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self.update()
        super().focusOutEvent(event)


class PiecePalette(QWidget):
    """Picks what a click puts on a square: one of the 12 pieces, or the eraser."""

    def __init__(self) -> None:
        super().__init__()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._tools: dict[int, chess.Piece | None] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        order = [chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]
        for row, color in enumerate((chess.WHITE, chess.BLACK)):
            for col, piece_type in enumerate(order):
                self._add(grid, row, col, chess.Piece(piece_type, color))
        self._add(grid, 0, 6, None, rows=2)
        grid.setColumnStretch(7, 1)  # keep the buttons compact; spare width goes to the right
        self.select(chess.Piece(chess.PAWN, chess.WHITE))

    def _add(self, grid: QGridLayout, row: int, col: int, piece: chess.Piece | None, rows: int = 1) -> None:
        button = QToolButton()
        button.setCheckable(True)
        button.setIcon(piece_icon(piece))
        button.setIconSize(QSize(32, 32))
        if piece is None:
            name, key = "Eraser", "Delete"
        else:
            name = f"{'White' if piece.color else 'Black'} {chess.piece_name(piece.piece_type)}"
            key = piece.symbol()
        button.setToolTip(f"{name} (key: {key})")
        button.setAccessibleName(name)
        if rows > 1:
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._group.addButton(button, len(self._tools))
        self._tools[len(self._tools)] = piece
        grid.addWidget(button, row, col, rows, 1)

    def current(self) -> chess.Piece | None:
        return self._tools[self._group.checkedId()]

    def select(self, piece: chess.Piece | None) -> None:
        for button_id, tool in self._tools.items():
            if tool == piece:
                self._group.button(button_id).setChecked(True)
