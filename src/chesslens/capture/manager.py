"""Capture thread: paces a FrameSource, stamps frames, keeps only the newest, recovers from loss (spec §6.1)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import numpy as np

from chesslens.capture.base import Frame, FrameSource, SourceLostError, SourceState, SourceStatus
from chesslens.capture.roi import Roi

log = logging.getLogger(__name__)

MIN_FPS, MAX_FPS = 0.2, 60.0


class LatestFrameSlot:
    """Single-slot mailbox: a new frame replaces an unread one, so a slow consumer never builds a backlog."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        self.dropped = 0

    def put(self, frame: Frame) -> None:
        with self._cond:
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame
            self._cond.notify_all()

    def get(self, timeout: float | None = None) -> Frame | None:
        """Take the newest unread frame, waiting up to `timeout` seconds; None if nothing arrived."""
        with self._cond:
            self._cond.wait_for(lambda: self._frame is not None, timeout)
            frame, self._frame = self._frame, None
            return frame


class CaptureManager:
    """Runs one FrameSource on a background thread.

    Frames go to `latest` (for the vision pipeline) and to `on_frame`; status
    changes go to `on_status`. Both callbacks run on the capture thread.
    When the source is lost it is stopped and restarted with bounded
    exponential backoff until stop() is called.
    """

    def __init__(
        self,
        source: FrameSource,
        fps: float = 5.0,
        on_frame: Callable[[Frame], None] | None = None,
        on_status: Callable[[SourceStatus], None] | None = None,
        initial_backoff: float = 0.5,
        max_backoff: float = 5.0,
    ) -> None:
        self.source = source
        self.fps = fps
        self.roi: Roi | None = None
        self.paused = False
        self.latest = LatestFrameSlot()
        self.measured_fps = 0.0
        self._on_frame = on_frame
        self._on_status = on_status
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = SourceStatus(SourceState.IDLE)
        self._seq = 0
        self._last_frame_at: float | None = None

    @property
    def fps(self) -> float:
        return self._fps

    @fps.setter
    def fps(self, value: float) -> None:
        self._fps = min(max(float(value), MIN_FPS), MAX_FPS)

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"capture[{self.source.source_id}]", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        backoff = self._initial_backoff
        started = False
        try:
            while not self._stop.is_set():
                tick = time.monotonic()
                if not started:
                    try:
                        self.source.start()
                        started = True
                    except Exception as exc:
                        self._report_loss(exc, backoff)
                        self._stop.wait(backoff)
                        backoff = min(backoff * 2, self._max_backoff)
                        continue
                if not self.paused:
                    try:
                        image = self.source.read()
                    except Exception as exc:
                        self._report_loss(exc, backoff)
                        self._stop_source()
                        started = False
                        self._stop.wait(backoff)
                        backoff = min(backoff * 2, self._max_backoff)
                        continue
                    backoff = self._initial_backoff
                    self._report(self.source.status)
                    if image is None:
                        self._last_frame_at = None
                    else:
                        self._deliver(image)
                self._stop.wait(max(0.0, tick + 1.0 / self._fps - time.monotonic()))
        finally:
            if started:
                self._stop_source()
            self._report(SourceStatus(SourceState.IDLE, "capture stopped"))

    def _deliver(self, image: np.ndarray) -> None:
        now = time.monotonic()
        if self._last_frame_at is not None and now > self._last_frame_at:
            rate = 1.0 / (now - self._last_frame_at)
            self.measured_fps = rate if self.measured_fps == 0.0 else 0.8 * self.measured_fps + 0.2 * rate
        self._last_frame_at = now
        self._seq += 1
        frame = Frame(
            image=image,
            seq=self._seq,
            timestamp=now,
            wall_time=time.time(),
            source_id=self.source.source_id,
            roi=self.roi,
        )
        self.latest.put(frame)
        if self._on_frame is not None:
            try:
                self._on_frame(frame)
            except Exception:
                log.exception("on_frame callback failed")

    def _report_loss(self, exc: Exception, retry_in: float) -> None:
        if not isinstance(exc, SourceLostError):
            log.warning("capture source %s failed", self.source.source_id, exc_info=exc)
        self._last_frame_at = None
        self._report(SourceStatus(SourceState.LOST, f"{exc} — retrying in {retry_in:.1f}s"))

    def _report(self, status: SourceStatus) -> None:
        if status == self._status:
            return
        self._status = status
        log.info("capture %s: %s", self.source.source_id, status)
        if self._on_status is not None:
            try:
                self._on_status(status)
            except Exception:
                log.exception("on_status callback failed")

    def _stop_source(self) -> None:
        try:
            self.source.stop()
        except Exception:
            log.exception("stopping capture source %s failed", self.source.source_id)
