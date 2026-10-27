"""Small value types shared across capture, vision, chess and UI layers."""

from __future__ import annotations

import enum


class Orientation(enum.Enum):
    """Which side's pieces start at the bottom of the screen (spec §6.2)."""

    WHITE_BOTTOM = "white"
    BLACK_BOTTOM = "black"

    @property
    def label(self) -> str:
        return "White at bottom" if self is Orientation.WHITE_BOTTOM else "Black at bottom"

    def flipped(self) -> Orientation:
        return Orientation.BLACK_BOTTOM if self is Orientation.WHITE_BOTTOM else Orientation.WHITE_BOTTOM
