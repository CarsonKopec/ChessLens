"""Replay a recorded video as a FrameSource, for deterministic tests and offline debugging (spec §4.3)."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from chesslens.capture.base import SourceLostError, SourceState, SourceStatus


class VideoFileSource:
    """Frames from a video file.

    With realtime=False every frame is returned in order, one per read(), so
    tests are deterministic. With realtime=True frames are skipped to keep
    pace with the wall clock, as a live mirroring window would.
    """

    def __init__(self, path: Path | str, loop: bool = False, realtime: bool = False) -> None:
        self.path = Path(path)
        self.loop = loop
        self.realtime = realtime
        self.source_id = f"video:{self.path.name}"
        self.status = SourceStatus(SourceState.IDLE)
        self._cap: cv2.VideoCapture | None = None
        self._video_fps = 30.0
        self._pos = 0  # index of the frame the next cap.read() returns
        self._t0 = 0.0

    def start(self) -> None:
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            cap.release()
            self.status = SourceStatus(SourceState.LOST, f"cannot open {self.path}")
            raise SourceLostError(self.status.detail)
        self._cap = cap
        self._video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._rewind()

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            raise SourceLostError("video source not started")
        if self.realtime:
            target = int((time.monotonic() - self._t0) * self._video_fps)
            while self._pos < target and self._cap.grab():
                self._pos += 1
        ok, frame = self._cap.read()
        if not ok and self.loop and self._pos > 0:
            self._rewind()
            ok, frame = self._cap.read()
        if not ok:
            self.status = SourceStatus(SourceState.ENDED, f"end of {self.path.name}")
            return None
        self._pos += 1
        self.status = SourceStatus(SourceState.OK)
        return frame

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _rewind(self) -> None:
        assert self._cap is not None
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self._pos = 0
        self._t0 = time.monotonic()
