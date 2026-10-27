"""Find a flat 2D chessboard in a captured frame (spec §6.2).

Mirrored chess apps draw an unrotated board, so its square boundaries are
long straight edges spaced exactly one square apart. We collect long vertical
and horizontal edges, look for runs of at least 7 equally spaced ones (the
inner lines are always visible; the outer edge may blend into the
background), and keep the grid whose squares best alternate between two
colours. That last check also settles which equally spaced lines are the
board's outer edges.
"""

from __future__ import annotations

import cv2
import numpy as np

from chesslens.vision.calibration import BoardCalibration

EDGE_THRESHOLD = 20  # max per-channel step between neighbouring pixels that counts as an edge
MIN_CHECKER_SCORE = 0.85  # share of squares that must match the light/dark alternation

_PARITY = (np.add.outer(np.arange(8), np.arange(8)) % 2).astype(bool)  # True on dark squares


def detect_board(
    image: np.ndarray,
    region: tuple[int, int, int, int] | None = None,
    min_square_px: int = 12,
) -> BoardCalibration | None:
    """Locate the board inside `region` (x, y, w, h; default whole frame). Corners are full-frame pixels."""
    h, w = image.shape[:2]
    rx, ry, rw, rh = region or (0, 0, w, h)
    roi = np.ascontiguousarray(image[ry : ry + rh, rx : rx + rw])
    if min(rw, rh) < 8 * min_square_px:
        return None
    pixels = roi.astype(np.int16)
    vertical = np.abs(np.diff(pixels, axis=1)).max(axis=2) > EDGE_THRESHOLD
    horizontal = np.abs(np.diff(pixels, axis=0)).max(axis=2) > EDGE_THRESHOLD
    min_run = 4 * min_square_px
    xs = _line_positions(_longest_runs(vertical), min_run)
    ys = _line_positions(_longest_runs(horizontal.T), min_run)
    max_square = min(rw, rh) / 8

    integral = cv2.integral(roi)
    best: tuple[float, float, float, float, float] | None = None
    for sx, x0 in _grid_origins(xs, min_square_px, max_square):
        for sy, y0 in _grid_origins(ys, min_square_px, max_square):
            if abs(sx - sy) > 0.05 * max(sx, sy):
                continue
            if x0 < -1 or y0 < -1 or x0 + 8 * sx > rw + 1 or y0 + 8 * sy > rh + 1:
                continue
            score = _checker_score(integral, x0, y0, sx, sy)
            if best is None or score > best[0]:
                best = (score, x0, y0, sx, sy)
    if best is None or best[0] < MIN_CHECKER_SCORE:
        return None
    score, x0, y0, sx, sy = best
    x0, y0 = max(x0, 0.0) + rx, max(y0, 0.0) + ry
    x1, y1 = min(x0 + 8 * sx, rx + rw), min(y0 + 8 * sy, ry + rh)
    return BoardCalibration(
        corners=((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
        frame_size=(w, h),
        method="auto",
        confidence=round(score, 3),
    )


def _longest_runs(mask: np.ndarray) -> np.ndarray:
    """Longest run of consecutive True values down each column of `mask`."""
    best = np.zeros(mask.shape[1], np.int32)
    run = np.zeros(mask.shape[1], np.int32)
    for row in mask:
        run = np.where(row, run + 1, 0)
        np.maximum(best, run, out=best)
    return best


def _line_positions(runs: np.ndarray, min_run: int) -> np.ndarray:
    """Sub-pixel positions of long edges. Edge index i lies between pixels i and i+1, i.e. at i + 1.

    An anti-aliased boundary can light up two neighbouring indices, so
    adjacent indices are merged into one line at their run-weighted centre.
    """
    idx = np.flatnonzero(runs >= min_run)
    if idx.size == 0:
        return idx.astype(np.float64)
    groups = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
    return np.array([np.average(g, weights=runs[g]) + 1.0 for g in groups])


def _grid_origins(lines: np.ndarray, min_s: float, max_s: float) -> list[tuple[float, float]]:
    """(square size, board start) candidates from runs of ≥7 equally spaced lines.

    A run of m lines could be any m consecutive of the board's 9 lines, so
    every placement is offered and the colour check picks the right one.
    """
    found: dict[float, tuple[float, float]] = {}
    for i, a in enumerate(lines):
        for b in lines[i + 1 :]:
            s = b - a
            if s < min_s:
                continue
            if s > max_s * 1.02:
                break
            tol = max(1.5, 0.04 * s)
            positions = [a, b]
            while len(positions) < 9:
                # Predict from a fit of every line found so far, not from the first gap alone: measured
                # line positions wobble by a few tenths of a pixel, and a spacing taken from one gap
                # drifts by over a pixel after six squares.
                step, origin = np.polyfit(np.arange(len(positions)), positions, 1)
                target = origin + len(positions) * step
                nearest = lines[np.argmin(np.abs(lines - target))]
                if abs(nearest - target) > tol:
                    break
                positions.append(nearest)
            if len(positions) < 7:
                continue
            fit_s, fit_a = np.polyfit(np.arange(len(positions)), positions, 1)
            for before in range(9 - len(positions) + 1):
                start = fit_a - before * fit_s
                found.setdefault(round(start * 2) / 2, (float(fit_s), float(start)))
    return list(found.values())


def _checker_score(integral: np.ndarray, x0: float, y0: float, sx: float, sy: float) -> float:
    """Share of the 64 squares whose colour sits nearer their own parity's colour than the other's.

    Each square's colour is the median of four small patches near its
    corners, which pieces rarely cover; a coordinate label in one corner or a
    highlighted square only costs that square.
    """
    h, w = integral.shape[0] - 1, integral.shape[1] - 1
    patch = max(1.0, 0.12 * min(sx, sy))
    inset = max(1.0, 0.06 * min(sx, sy))
    cols, rows = np.arange(8), np.arange(8)
    lefts = x0 + cols * sx
    tops = y0 + rows * sy
    px = np.stack([lefts + inset, lefts + sx - inset - patch])  # (2, 8)
    py = np.stack([tops + inset, tops + sy - inset - patch])
    x1 = np.clip(np.round(px), 0, w - 1).astype(int)
    y1 = np.clip(np.round(py), 0, h - 1).astype(int)
    x2 = np.clip(np.round(px + patch), 1, w).astype(int)
    y2 = np.clip(np.round(py + patch), 1, h).astype(int)
    # Broadcast to (rows, cols, corner_y, corner_x).
    Y1, Y2 = y1.T[:, None, :, None], y2.T[:, None, :, None]
    X1, X2 = x1.T[None, :, None, :], x2.T[None, :, None, :]
    sums = integral[Y2, X2] - integral[Y1, X2] - integral[Y2, X1] + integral[Y1, X1]
    area = np.maximum((Y2 - Y1) * (X2 - X1), 1)[..., None]
    colours = np.median((sums / area).reshape(8, 8, 4, -1), axis=2)  # (8, 8, channels)

    light = np.median(colours[~_PARITY], axis=0)
    dark = np.median(colours[_PARITY], axis=0)
    if np.abs(light - dark).max() < 25:
        return 0.0
    to_light = np.linalg.norm(colours - light, axis=2)
    to_dark = np.linalg.norm(colours - dark, axis=2)
    matches = np.where(_PARITY, to_dark < to_light, to_light < to_dark)
    return float(matches.mean())
