"""Board calibration: where the board sits in the frame, plus an orientation guess (spec §6.2)."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import Point, warp_board

Corners = tuple[Point, Point, Point, Point]


@dataclass(frozen=True)
class BoardCalibration:
    """Board corners in frame pixels (top-left, top-right, bottom-right, bottom-left as on screen).

    `version` is assigned by whoever owns the current calibration and goes up
    with every change, so downstream results can tell which geometry they used.
    """

    corners: Corners
    frame_size: tuple[int, int]
    method: str = "manual"  # "auto" or "manual"
    confidence: float = 1.0
    version: int = 0

    @classmethod
    def from_rect(cls, x: float, y: float, w: float, h: float, frame_size: tuple[int, int]) -> BoardCalibration:
        return cls(corners=((x, y), (x + w, y), (x + w, y + h), (x, y + h)), frame_size=frame_size)

    @property
    def corner_array(self) -> np.ndarray:
        return np.float32(self.corners)

    def with_corner(self, index: int, point: Point) -> BoardCalibration:
        """Move one corner (a manual adjustment). Raises ValueError if the board would fold over."""
        corners = list(self.corners)
        corners[index] = (float(point[0]), float(point[1]))
        if not is_convex(corners):
            raise ValueError("corners must form a convex quadrilateral in clockwise order")
        return replace(self, corners=tuple(corners), method="manual", confidence=1.0)

    def rescaled(self, frame_size: tuple[int, int]) -> BoardCalibration:
        """Scale the corners to a resized frame; only right if the window was scaled uniformly."""
        sx, sy = frame_size[0] / self.frame_size[0], frame_size[1] / self.frame_size[1]
        corners = tuple((x * sx, y * sy) for x, y in self.corners)
        return replace(self, corners=corners, frame_size=frame_size)

    def warp(self, image: np.ndarray, size: int = 800) -> np.ndarray:
        return warp_board(image, self.corners, size)


def is_convex(corners) -> bool:
    """True when the four points turn the same way at every corner (clockwise on screen) with real area."""
    pts = np.float64(corners)
    edges = np.roll(pts, -1, axis=0) - pts
    cross = edges[:, 0] * np.roll(edges, -1, axis=0)[:, 1] - edges[:, 1] * np.roll(edges, -1, axis=0)[:, 0]
    return bool(np.all(cross > 1e-6))


def piece_colours(board_image: np.ndarray) -> np.ndarray:
    """Rough per-cell census of a warped board: 0 empty, 1 light piece, -1 dark piece (screen layout).

    Not piece recognition: just enough to guess which side sits at the
    bottom. A cell is occupied when part of its interior differs from the
    square's own colour (read from its corners) and that part is tall: apps
    draw legal-move dots as small centred discs, while even a pawn spans most
    of the square's height. The piece is dark when the pixels that set it
    apart from the square are mostly dark: light pieces only have a thin dark
    outline. Only the piece's pixels count, not the square's, since some
    boards' dark squares (e.g. Chess.com's wood) are darker than that.
    """
    cell = board_image.shape[0] // 8
    pad, patch, margin = max(1, cell // 30), max(2, cell // 10), cell // 8
    out = np.zeros((8, 8), np.int8)
    for r in range(8):
        for c in range(8):
            sq = board_image[r * cell : (r + 1) * cell, c * cell : (c + 1) * cell].astype(np.int16)
            corner_means = [
                sq[y : y + patch, x : x + patch].reshape(-1, 3).mean(axis=0)
                for y in (pad, cell - pad - patch)
                for x in (pad, cell - pad - patch)
            ]
            base = np.median(corner_means, axis=0)
            inner = sq[margin:-margin, margin:-margin]
            differs = np.abs(inner - base).max(axis=2) > 48
            if differs.mean() < 0.03:
                continue
            rows = np.flatnonzero(differs.sum(axis=1) >= 3)
            if rows.size == 0 or (rows[-1] - rows[0] + 1) < 0.5 * inner.shape[0]:
                continue
            # A lower bar than for occupancy, so a white body only a little brighter than a light
            # square still outweighs its outline. Real pieces: light 150-250, dark 40-85.
            piece = np.abs(inner - base).max(axis=2) > 25
            out[r, c] = -1 if np.median(inner.mean(axis=2)[piece]) < 120 else 1
    return out


def guess_orientation(board_image: np.ndarray) -> tuple[Orientation | None, float]:
    """Guess which side is at the bottom from where light and dark pieces sit.

    Each side's pieces are mostly on its own half in openings and many
    middlegames. Returns (None, 0.0) when the evidence is balanced; the user
    can always override.
    """
    census = piece_colours(board_image)
    top, bottom = census[:4], census[4:]
    score = int((bottom == 1).sum() - (top == 1).sum() + (top == -1).sum() - (bottom == -1).sum())
    pieces = int((census != 0).sum())
    if pieces < 2 or score == 0:
        return None, 0.0
    return (Orientation.WHITE_BOTTOM if score > 0 else Orientation.BLACK_BOTTOM), abs(score) / pieces
