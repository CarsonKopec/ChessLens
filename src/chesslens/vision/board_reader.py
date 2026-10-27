"""Reads the board from the newest frame on a worker thread, once the picture has held still (spec §9 steps 3–5).

Frames are offered from the capture side and kept in a one-frame slot, so a
slow read never builds a backlog. The straightened board is compared with
the previous one; while it changes (a piece sliding, the board flipping)
nothing is read, and a reading is made once it has been still for
`stable_frames` frames. A still board is read once, not on every frame.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from chesslens.capture.base import Frame
from chesslens.capture.manager import LatestFrameSlot
from chesslens.domain.models import Orientation
from chesslens.vision.calibration import BoardCalibration
from chesslens.vision.piece_classifier import BoardObservation, read_board
from chesslens.vision.piece_set import PieceSet

log = logging.getLogger(__name__)

# Mean absolute grey-level change within any one square of the 64×64 thumbnail that counts as movement.
# Per square, because a move changes only two of the 64; video noise stays around 1-2.
CHANGE_THRESHOLD = 8.0


@dataclass(frozen=True)
class ReaderSetup:
    calibration: BoardCalibration
    pieces: PieceSet
    orientation: Orientation


class BoardReader:
    def __init__(self, on_observation: Callable[[BoardObservation], None], stable_frames: int = 2) -> None:
        self._on_observation = on_observation
        self._stable_frames = stable_frames
        self._slot = LatestFrameSlot()
        self._setup: ReaderSetup | None = None
        self._lock = threading.Lock()
        self._reset = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def configure(self, setup: ReaderSetup | None, reset: bool = True) -> None:
        """New calibration, piece set or orientation. With reset, start over and read the board again once
        still; without, keep going and use the new setup from the next reading on."""
        with self._lock:
            self._setup = setup
            self._reset = self._reset or reset

    def offer(self, frame: Frame) -> None:
        self._slot.put(frame)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="board-reader", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        previous: np.ndarray | None = None
        still = 0
        read_this_state = False
        while not self._stop.is_set():
            frame = self._slot.get(timeout=0.2)
            if frame is None:
                continue
            with self._lock:
                setup, reset = self._setup, self._reset
                self._reset = False
            if setup is None:
                continue
            if reset:
                previous, still, read_this_state = None, 0, False
            try:
                if setup.calibration.frame_size != frame.size:
                    continue  # the window changed size; wait for the new calibration
                board = setup.calibration.warp(frame.image)
                thumb = cv2.cvtColor(cv2.resize(board, (64, 64), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                thumb = thumb.astype(np.int16)
                moving = previous is not None and _largest_square_change(thumb, previous) > CHANGE_THRESHOLD
                previous = thumb
                if moving:
                    still, read_this_state = 0, False
                    continue
                still += 1
                if still < self._stable_frames or read_this_state:
                    continue
                observation = read_board(board, setup.pieces, setup.orientation, setup.calibration.version, frame.seq)
                read_this_state = True
                self._on_observation(observation)
            except Exception:
                log.exception("reading the board failed")


def _largest_square_change(a: np.ndarray, b: np.ndarray) -> float:
    """Largest mean absolute difference over the 64 squares of two 64×64 thumbnails."""
    per_square = np.abs(a - b).reshape(8, 8, 8, 8).mean(axis=(1, 3))
    return float(per_square.max())
