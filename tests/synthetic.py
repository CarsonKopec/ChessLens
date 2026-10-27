"""Synthetic boards and phone screens, drawn to look like a 2D chess app captured from a mirror."""

from __future__ import annotations

import chess
import cv2
import numpy as np

from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import screen_cell_to_square

LIGHT = (210, 238, 238)  # BGR, Chess.com green theme as measured from a real capture
DARK = (86, 150, 119)
LIGHT_HIGHLIGHT = (130, 246, 245)
DARK_HIGHLIGHT = (67, 202, 185)
WOOD = ((115, 167, 199), (56, 92, 130))  # BGR light / dark, Chess.com's wood board in a browser
UI_BACKGROUND = (43, 46, 49)


def render_board(
    placement: str = chess.STARTING_BOARD_FEN,
    square: int = 40,
    orientation: Orientation = Orientation.WHITE_BOTTOM,
    highlight: tuple[chess.Square, ...] = (),
    dots: tuple[chess.Square, ...] = (),
    letters: bool = False,
    colours: tuple[tuple[int, int, int], tuple[int, int, int]] = (LIGHT, DARK),
) -> np.ndarray:
    """An 8×8 board image. Pieces are tall ellipses: white = light fill with a thin dark
    outline, black = dark fill. `dots` draws legal-move markers like Chess.com's.
    `letters` gives every piece type its own silhouette and letter, as a real set has.
    `colours` are the light and dark squares (BGR)."""
    board = chess.BaseBoard(placement)
    img = np.zeros((8 * square, 8 * square, 3), np.uint8)
    for row in range(8):
        for col in range(8):
            sq = screen_cell_to_square(row, col, orientation)
            light = (row + col) % 2 == 0
            colour = (LIGHT_HIGHLIGHT if light else DARK_HIGHLIGHT) if sq in highlight else colours[0 if light else 1]
            x, y = col * square, row * square
            img[y : y + square, x : x + square] = colour
            centre = (x + square // 2, y + square // 2)
            piece = board.piece_at(sq)
            if piece is not None:
                if letters:
                    _draw_distinct_piece(img, centre, square, piece)
                else:
                    axes = (int(square * 0.28), int(square * 0.36))
                    if piece.color == chess.WHITE:
                        cv2.ellipse(img, centre, axes, 0, 0, 360, (250, 250, 250), -1)
                        cv2.ellipse(img, centre, axes, 0, 0, 360, (40, 40, 40), max(1, square // 50))
                    else:
                        cv2.ellipse(img, centre, axes, 0, 0, 360, (60, 60, 60), -1)
            elif sq in dots:
                cv2.circle(img, centre, int(square * 0.12), (150, 160, 160), -1)
    return img


def _draw_distinct_piece(img: np.ndarray, centre: tuple[int, int], s: int, piece: chess.Piece) -> None:
    """A silhouette per piece type plus its letter, white = light with a dark outline, black = dark."""
    x, y = centre
    fill, line = ((250, 250, 250), (40, 40, 40)) if piece.color == chess.WHITE else ((60, 60, 60), (60, 60, 60))
    t = piece.piece_type

    def pts(*fractions):
        return np.array([[x + int(fx * s), y + int(fy * s)] for fx, fy in fractions], np.int32)

    if t == chess.PAWN:
        shape = cv2.ellipse2Poly((x, y + int(0.06 * s)), (int(0.2 * s), int(0.27 * s)), 0, 0, 360, 10)
    elif t == chess.KNIGHT:
        shape = pts((-0.28, 0.36), (0.28, 0.36), (0.22, -0.1), (-0.05, -0.38), (-0.26, -0.18))
    elif t == chess.BISHOP:
        shape = cv2.ellipse2Poly((x, y), (int(0.17 * s), int(0.38 * s)), 0, 0, 360, 10)
    elif t == chess.ROOK:
        shape = pts((-0.26, -0.32), (0.26, -0.32), (0.26, 0.36), (-0.26, 0.36))
    elif t == chess.QUEEN:
        shape = cv2.ellipse2Poly((x, y), (int(0.36 * s), int(0.36 * s)), 0, 0, 360, 10)
    else:
        shape = pts((0, -0.4), (0.34, 0), (0, 0.4), (-0.34, 0))
    cv2.fillPoly(img, [shape], fill, cv2.LINE_AA)
    cv2.polylines(img, [shape], True, line, max(1, s // 50), cv2.LINE_AA)
    text = piece.symbol().upper()
    scale, thickness = s / 110, max(1, s // 45)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, thickness)
    colour = (40, 40, 40) if piece.color == chess.WHITE else (225, 225, 225)
    cv2.putText(img, text, (x - tw // 2, y + th // 2), cv2.FONT_HERSHEY_DUPLEX, scale, colour, thickness, cv2.LINE_AA)


def phone_screen(board: np.ndarray, height_ratio: float = 2.2) -> tuple[np.ndarray, tuple[int, int]]:
    """Put the board across a portrait app screen with UI around it. Returns (screen, board top-left)."""
    side = board.shape[1]
    height = int(side * height_ratio)
    screen = np.full((height, side, 3), UI_BACKGROUND, np.uint8)
    top = int(height * 0.28)
    screen[top : top + board.shape[0]] = board
    # Status bar, move list, player bars and toolbar: assorted rectangles, like text and icons.
    rng = np.random.default_rng(7)
    for y in (10, 50, top - 60, top + board.shape[0] + 20, height - 40):
        for x in range(10, side - 40, 45):
            w, h = rng.integers(12, 36), rng.integers(8, 22)
            screen[y : y + h, x : x + w] = rng.integers(90, 255, 3)
    cv2.line(screen, (0, top - 12), (side - 1, top - 12), (70, 70, 70), 1)
    return screen, (0, top)


def mirror_window(screen: np.ndarray, width: int = 1084, height: int = 681) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Scale a phone screen into a landscape window with black bars, as AirScreen shows it.

    Returns (frame, scale, phone top-left in the frame).
    """
    scale = height / screen.shape[0]
    phone = cv2.resize(screen, (round(screen.shape[1] * scale), height), interpolation=cv2.INTER_AREA)
    frame = np.zeros((height, width, 3), np.uint8)
    left = (width - phone.shape[1]) // 2
    frame[:, left : left + phone.shape[1]] = phone
    return frame, scale, (left, 0)
