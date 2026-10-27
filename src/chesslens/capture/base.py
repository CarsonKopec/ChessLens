"""Frame-source contract shared by every capture backend (spec §4.3).

Capture code must never import the chess engine or UI (spec §11).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from chesslens.capture.roi import Roi


class SourceState(enum.Enum):
    IDLE = "idle"
    OK = "ok"
    MINIMIZED = "minimized"
    OCCLUDED = "occluded"
    HIDDEN = "hidden"  # cloaked, off-screen, or the desktop could not be read
    BLANK = "blank"  # uniform frame: stream dropped or protected content
    LOST = "lost"  # window closed / file missing; the manager retries with backoff
    ENDED = "ended"  # a finite source (video file) has no more frames


@dataclass(frozen=True)
class SourceStatus:
    state: SourceState
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state is SourceState.OK

    def __str__(self) -> str:
        return f"{self.state.value}: {self.detail}" if self.detail else self.state.value


class SourceLostError(RuntimeError):
    """The source is gone. The manager calls stop(), then retries start() with backoff."""


class FrameSource(Protocol):
    """A producer of BGR frames.

    start(), read() and stop() are always called from the same thread, because
    the Win32/GDI handles behind screen capture are thread-bound.
    """

    source_id: str
    status: SourceStatus

    def start(self) -> None: ...

    def read(self) -> np.ndarray | None:
        """Return the current BGR frame, or None when no valid frame is available (see status)."""
        ...

    def stop(self) -> None: ...


@dataclass(frozen=True, eq=False)
class Frame:
    image: np.ndarray  # full BGR frame as delivered by the source
    seq: int
    timestamp: float  # time.monotonic() when captured
    wall_time: float  # time.time() when captured
    source_id: str
    roi: Roi | None = None

    @property
    def size(self) -> tuple[int, int]:
        return self.image.shape[1], self.image.shape[0]

    @property
    def roi_image(self) -> np.ndarray:
        """The cropped region the vision pipeline should use (whole frame when no ROI is set)."""
        return self.roi.crop(self.image) if self.roi else self.image
