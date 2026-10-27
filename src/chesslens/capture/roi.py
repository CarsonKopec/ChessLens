"""Region-of-interest crop applied to captured frames (spec §4.1 step 4, §4.2)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Roi:
    """Crop rectangle as fractions of the frame, so it survives uniform rescaling of the window."""

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.x < 1.0 and 0.0 <= self.y < 1.0):
            raise ValueError(f"ROI origin outside frame: {self}")
        if self.w <= 0.0 or self.h <= 0.0 or self.x + self.w > 1.0 + 1e-9 or self.y + self.h > 1.0 + 1e-9:
            raise ValueError(f"ROI extent outside frame: {self}")

    @classmethod
    def from_pixels(cls, x: int, y: int, w: int, h: int, frame_w: int, frame_h: int) -> Roi:
        """Build from a pixel rectangle, clipped to the frame."""
        x0, y0 = max(0, min(x, x + w)), max(0, min(y, y + h))
        x1, y1 = min(frame_w, max(x, x + w)), min(frame_h, max(y, y + h))
        if x1 - x0 < 1 or y1 - y0 < 1:
            raise ValueError("ROI is empty after clipping to the frame")
        return cls(x0 / frame_w, y0 / frame_h, (x1 - x0) / frame_w, (y1 - y0) / frame_h)

    def to_pixels(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        x0, y0 = round(self.x * frame_w), round(self.y * frame_h)
        x1, y1 = round((self.x + self.w) * frame_w), round((self.y + self.h) * frame_h)
        x1, y1 = min(max(x1, x0 + 1), frame_w), min(max(y1, y0 + 1), frame_h)
        return x0, y0, x1 - x0, y1 - y0

    def crop(self, image: np.ndarray) -> np.ndarray:
        x, y, w, h = self.to_pixels(image.shape[1], image.shape[0])
        return image[y : y + h, x : x + w]


def auto_trim(image: np.ndarray, tolerance: int = 16, min_fraction: float = 0.05) -> Roi | None:
    """Find the content box inside uniform letterbox/pillarbox bars.

    Mirroring apps usually show a portrait phone screen centred in a wider
    window; this trims the solid bars around it. Returns None when there is
    nothing to trim or the result would be implausibly small.
    """
    h, w = image.shape[:2]
    border = np.concatenate([image[0], image[-1], image[:, 0], image[:, -1]]).astype(np.int16)
    background = np.median(border, axis=0)
    differs = np.abs(image.astype(np.int16) - background).max(axis=2) > tolerance
    # Ignore rows/cols with only a few stray differing pixels (compression noise, cursors), and keep
    # only the widest block of content, so an overlay button floating in a bar (e.g. the mirroring
    # app's own settings icon) doesn't stretch the box.
    cols = _widest_span(differs.mean(axis=0) > 0.02, max_gap=max(2, w // 50))
    if cols is None:
        return None
    x0, x1 = cols
    rows = _widest_span(differs[:, x0:x1].mean(axis=1) > 0.02, max_gap=max(2, h // 50))
    if rows is None:
        return None
    y0, y1 = rows
    if (x1 - x0) * (y1 - y0) >= 0.99 * w * h:
        return None
    if (x1 - x0) < min_fraction * w or (y1 - y0) < min_fraction * h:
        return None
    return Roi.from_pixels(int(x0), int(y0), int(x1 - x0), int(y1 - y0), w, h)


def _widest_span(flags: np.ndarray, max_gap: int) -> tuple[int, int] | None:
    """[start, end) of the widest run of True values, bridging gaps of up to `max_gap` False values."""
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return None
    runs = np.split(idx, np.flatnonzero(np.diff(idx) > max_gap + 1) + 1)
    widest = max(runs, key=lambda r: r[-1] - r[0])
    return int(widest[0]), int(widest[-1]) + 1
