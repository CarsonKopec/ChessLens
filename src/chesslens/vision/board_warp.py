"""Map between the board as captured and the canonical 8×8 grid (spec §6.2).

Corners are always ordered top-left, top-right, bottom-right, bottom-left as
seen on screen. "Screen cells" are (row, col) with row 0 at the top of the
screen; which chess square a cell holds depends on the orientation.
"""

from __future__ import annotations

from collections.abc import Sequence

import chess
import cv2
import numpy as np

from chesslens.domain.models import Orientation

CANONICAL_SIZE = 800  # warped board edge in px, so each square is 100×100

Point = tuple[float, float]


def homography(corners: Sequence[Point], size: float = CANONICAL_SIZE) -> np.ndarray:
    """Frame → canonical-board perspective transform."""
    dst = np.float32([[0, 0], [size, 0], [size, size], [0, size]])
    return cv2.getPerspectiveTransform(np.float32(corners), dst)


def warp_board(image: np.ndarray, corners: Sequence[Point], size: int = CANONICAL_SIZE) -> np.ndarray:
    """The board straightened into a size×size image, oriented as on screen."""
    return cv2.warpPerspective(image, homography(corners, size), (size, size), flags=cv2.INTER_LINEAR)


def to_board_coords(corners: Sequence[Point], points: Sequence[Point]) -> np.ndarray:
    """Frame points → board coordinates measured in squares (0..8 across, 0..8 down the screen)."""
    pts = np.float32(points).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, homography(corners, 8)).reshape(-1, 2)


def from_board_coords(corners: Sequence[Point], points: Sequence[Point]) -> np.ndarray:
    """Board coordinates in squares → frame points."""
    pts = np.float32(points).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, np.linalg.inv(homography(corners, 8))).reshape(-1, 2)


def grid_lines(corners: Sequence[Point]) -> list[tuple[Point, Point]]:
    """The 9 vertical then 9 horizontal grid lines in frame coordinates, for drawing an overlay."""
    ends = [p for k in range(9) for p in ((k, 0), (k, 8))]
    ends += [p for k in range(9) for p in ((0, k), (8, k))]
    pts = from_board_coords(corners, ends)
    return [(tuple(pts[i]), tuple(pts[i + 1])) for i in range(0, len(pts), 2)]


def screen_cell_to_square(row: int, col: int, orientation: Orientation) -> chess.Square:
    if orientation is Orientation.WHITE_BOTTOM:
        return chess.square(col, 7 - row)
    return chess.square(7 - col, row)


def square_to_screen_cell(square: chess.Square, orientation: Orientation) -> tuple[int, int]:
    file, rank = chess.square_file(square), chess.square_rank(square)
    if orientation is Orientation.WHITE_BOTTOM:
        return 7 - rank, file
    return rank, 7 - file


def square_images(board_image: np.ndarray, orientation: Orientation) -> dict[chess.Square, np.ndarray]:
    """Split a warped board into per-square crops keyed by chess square (input for piece recognition)."""
    cell = board_image.shape[0] // 8
    return {
        screen_cell_to_square(r, c, orientation): board_image[r * cell : (r + 1) * cell, c * cell : (c + 1) * cell]
        for r in range(8)
        for c in range(8)
    }
