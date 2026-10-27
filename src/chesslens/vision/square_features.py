"""Cut each square's piece out of its background so pieces compare the same on any square colour.

Works on a straightened board (board_warp.warp_board): 8×8 cells, rows top
to bottom as on screen. A square's background is its board colour, or the
app's highlight tint when it is marked (e.g. Chess.com's last-move yellow).
"""

from __future__ import annotations

import cv2
import numpy as np

NORM = 48  # normalised square edge in px
BACKGROUND = 128  # grey written over everything that isn't piece
MASK_THRESHOLD = 22  # max per-channel step from the background that counts as piece
EDGE_MARGIN = 0.05  # warp blends neighbouring squares into the outer few px
LABEL_BOX = 0.18  # apps print coordinates in the top-left / bottom-right corners
HIGHLIGHT_DISTANCE = 30  # a square's border this far from the board colour means it is tinted
BORDER = (0.03, 0.10)  # the band along a square's edges that its colour is read from, as shares of its side

_PARITY_DARK = (np.add.outer(np.arange(8), np.arange(8)) % 2).astype(bool)


def cells(board_image: np.ndarray) -> np.ndarray:
    """(8, 8, cell, cell, 3) view of a straightened board."""
    n = board_image.shape[0] // 8
    return board_image[: 8 * n, : 8 * n].reshape(8, n, 8, n, -1).swapaxes(1, 2)


def square_backgrounds(board_image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-square background colour (8, 8, 3) and which squares are tinted (8, 8).

    Each square's colour is the median of a band along its edges (pieces
    rarely reach it); the board's own light and dark colours are the medians
    over all squares of each colour. A square whose colour strays from its
    board colour is tinted, and then its own colour is its background. A
    whole band rather than a few corner patches, because on a wood-grain board
    a dark streak through a corner looked like a highlight.
    """
    colours = _border_colours(board_image)
    board_colour = np.where(
        _PARITY_DARK[..., None],
        np.median(colours[_PARITY_DARK], axis=0),
        np.median(colours[~_PARITY_DARK], axis=0),
    )
    tinted = np.abs(colours - board_colour).max(axis=2) > HIGHLIGHT_DISTANCE
    return np.where(tinted[..., None], colours, board_colour), tinted


def board_visible(board_image: np.ndarray, min_share: float = 0.75) -> bool:
    """Does the region still show a chessboard: two clearly different square colours alternating?

    False for a mirroring app's idle screen, another app, or a banner over
    much of the board. Highlighted squares only cost themselves.
    """
    corners = _corner_colours(board_image)
    light = np.median(corners[~_PARITY_DARK], axis=0)
    dark = np.median(corners[_PARITY_DARK], axis=0)
    if np.abs(light - dark).max() < 25:
        return False
    to_light = np.linalg.norm(corners - light, axis=2)
    to_dark = np.linalg.norm(corners - dark, axis=2)
    matches = np.where(_PARITY_DARK, to_dark < to_light, to_light < to_dark)
    return float(matches.mean()) >= min_share


def _corner_colours(board_image: np.ndarray) -> np.ndarray:
    """(8, 8, 3) median colour of each square's four corner patches, which pieces rarely reach."""
    sq = cells(board_image).astype(np.float32)
    n = sq.shape[2]
    pad, patch = max(1, int(n * 0.06)), max(2, n // 10)
    lo, hi = pad, n - pad - patch
    patches = [sq[:, :, y : y + patch, x : x + patch].mean(axis=(2, 3)) for y in (lo, hi) for x in (lo, hi)]
    return np.median(np.stack(patches), axis=0)


def _border_colours(board_image: np.ndarray) -> np.ndarray:
    """(8, 8, 3) median colour of each square's edge band, leaving out the coordinate-label corners."""
    sq = cells(board_image)
    n = sq.shape[2]
    outer, inner, lb = int(n * BORDER[0]), int(n * BORDER[1]), int(n * LABEL_BOX)
    band = np.zeros((n, n), bool)
    band[outer : n - outer, outer : n - outer] = True
    band[inner : n - inner, inner : n - inner] = False
    band[:lb, :lb] = band[-lb:, -lb:] = False
    return np.median(sq[:, :, band], axis=2).astype(np.float32)


def coverage(square: np.ndarray) -> float:
    """Share of a normalised square that is piece rather than background grey."""
    return float((square != BACKGROUND).any(axis=2).mean())


def normalize_square(cell: np.ndarray, background: np.ndarray) -> np.ndarray:
    """The piece on grey, NORM×NORM: background, edge fringe and coordinate-label corners become grey."""
    n = cell.shape[0]
    mask = (np.abs(cell.astype(np.int16) - background.astype(np.int16)).max(axis=2) > MASK_THRESHOLD).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))  # video noise speckle
    m, lb = max(1, int(n * EDGE_MARGIN)), int(n * LABEL_BOX)
    mask[:m] = mask[-m:] = 0
    mask[:, :m] = mask[:, -m:] = 0
    mask[:lb, :lb] = 0
    mask[-lb:, -lb:] = 0
    out = np.where(mask[..., None] > 0, cell, np.uint8(BACKGROUND))
    return cv2.resize(out, (NORM, NORM), interpolation=cv2.INTER_AREA)


def normalize_board(board_image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """All 64 squares normalised, (8, 8, NORM, NORM, 3), plus the tinted-square map."""
    backgrounds, tinted = square_backgrounds(board_image)
    sq = cells(board_image)
    out = np.empty((8, 8, NORM, NORM, 3), np.uint8)
    for r in range(8):
        for c in range(8):
            out[r, c] = normalize_square(np.ascontiguousarray(sq[r, c]), backgrounds[r, c])
    return out, tinted


def empty_square() -> np.ndarray:
    return np.full((NORM, NORM, 3), BACKGROUND, np.uint8)
