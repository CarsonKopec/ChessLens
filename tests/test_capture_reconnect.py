import threading
import time

import numpy as np
from conftest import wait_for

from chesslens.capture import CaptureManager, LatestFrameSlot, Roi, SourceLostError, SourceState, SourceStatus
from chesslens.capture.base import Frame


class ScriptedSource:
    """FrameSource that plays a script of read() outcomes: "frame", "none", "lost" or "boom"."""

    def __init__(self, script: list[str], start_failures: int = 0) -> None:
        self.source_id = "scripted"
        self.status = SourceStatus(SourceState.IDLE)
        self.script = list(script)
        self.start_failures = start_failures
        self.calls: list[tuple[str, int]] = []

    def start(self) -> None:
        self.calls.append(("start", threading.get_ident()))
        if self.start_failures:
            self.start_failures -= 1
            raise SourceLostError("window not open yet")

    def read(self) -> np.ndarray | None:
        self.calls.append(("read", threading.get_ident()))
        action = self.script.pop(0) if self.script else "none"
        if action == "lost":
            raise SourceLostError("window closed")
        if action == "boom":
            raise RuntimeError("driver hiccup")
        if action == "none":
            self.status = SourceStatus(SourceState.OCCLUDED, "covered")
            return None
        self.status = SourceStatus(SourceState.OK)
        return np.zeros((4, 4, 3), np.uint8)

    def stop(self) -> None:
        self.calls.append(("stop", threading.get_ident()))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def run(source: ScriptedSource, **kwargs):
    frames: list[Frame] = []
    statuses: list[SourceStatus] = []
    kwargs = {"fps": 200.0, "initial_backoff": 0.01, "max_backoff": 0.04} | kwargs
    manager = CaptureManager(source, on_frame=frames.append, on_status=statuses.append, **kwargs)
    return manager, frames, statuses


def test_frames_are_stamped_sequenced_and_carry_roi():
    source = ScriptedSource(["frame"] * 5)
    manager, frames, _ = run(source)
    manager.roi = Roi(0.1, 0.1, 0.5, 0.5)
    manager.start()
    wait_for(lambda: len(frames) >= 5)
    manager.stop()

    assert [f.seq for f in frames[:5]] == [1, 2, 3, 4, 5]
    assert all(a.timestamp < b.timestamp for a, b in zip(frames, frames[1:]))
    assert {f.source_id for f in frames} == {"scripted"}
    assert frames[0].roi == Roi(0.1, 0.1, 0.5, 0.5)


def test_lost_source_is_stopped_and_restarted():
    source = ScriptedSource(["frame", "lost", "frame"])
    manager, frames, statuses = run(source)
    manager.start()
    wait_for(lambda: len(frames) >= 2)
    manager.stop()

    assert source.names()[:6] == ["start", "read", "read", "stop", "start", "read"]
    assert any(s.state is SourceState.LOST and "window closed" in s.detail for s in statuses)
    assert frames[1].seq == 2  # sequence continues across the reconnect


def test_start_is_retried_with_bounded_backoff_until_source_appears():
    source = ScriptedSource(["frame"], start_failures=4)
    manager, frames, statuses = run(source, initial_backoff=0.1, max_backoff=0.4)
    manager.start()
    wait_for(lambda: frames)
    manager.stop()

    assert source.names().count("start") == 5
    # Backoff doubles and then holds at the cap; the repeated 0.4s status is collapsed.
    assert [s.detail for s in statuses if s.state is SourceState.LOST] == [
        "window not open yet — retrying in 0.1s",
        "window not open yet — retrying in 0.2s",
        "window not open yet — retrying in 0.4s",
    ]
    assert manager.status.state is SourceState.IDLE


def test_unexpected_error_does_not_kill_capture_thread():
    source = ScriptedSource(["boom", "frame"])
    manager, frames, _ = run(source)
    manager.start()
    wait_for(lambda: frames)
    assert manager.running
    manager.stop()


def test_source_methods_all_run_on_one_background_thread():
    source = ScriptedSource(["frame", "lost", "frame"])
    manager, frames, _ = run(source)
    manager.start()
    wait_for(lambda: len(frames) >= 2)
    manager.stop()

    threads = {ident for _, ident in source.calls}
    assert len(threads) == 1
    assert threads != {threading.get_ident()}


def test_stop_releases_source_and_reports_idle():
    source = ScriptedSource(["frame"] * 3)
    manager, frames, statuses = run(source)
    manager.start()
    wait_for(lambda: frames)
    manager.stop()

    assert not manager.running
    assert source.names()[-1] == "stop"
    assert statuses[-1].state is SourceState.IDLE


def test_status_is_reported_only_when_it_changes():
    source = ScriptedSource(["none", "none", "none", "frame"])
    manager, frames, statuses = run(source)
    manager.start()
    wait_for(lambda: frames)
    manager.stop()

    assert [s.state for s in statuses[:2]] == [SourceState.OCCLUDED, SourceState.OK]


def test_paused_manager_does_not_read():
    source = ScriptedSource(["frame"] * 3)
    manager, frames, _ = run(source)
    manager.paused = True
    manager.start()
    time.sleep(0.1)
    assert "read" not in source.names()
    manager.paused = False
    wait_for(lambda: frames)
    manager.stop()


def test_fps_is_clamped():
    manager, _, _ = run(ScriptedSource([]))
    manager.fps = 0
    assert manager.fps > 0
    manager.fps = 10_000
    assert manager.fps <= 60


def test_latest_slot_keeps_only_newest_frame():
    slot = LatestFrameSlot()
    image = np.zeros((2, 2, 3), np.uint8)
    for seq in (1, 2, 3):
        slot.put(Frame(image=image, seq=seq, timestamp=seq, wall_time=seq, source_id="t"))

    assert slot.get(timeout=0.01).seq == 3
    assert slot.dropped == 2
    assert slot.get(timeout=0.01) is None


def test_latest_slot_get_wakes_when_frame_arrives():
    slot = LatestFrameSlot()
    frame = Frame(image=np.zeros((2, 2, 3), np.uint8), seq=1, timestamp=0, wall_time=0, source_id="t")
    threading.Timer(0.05, slot.put, args=(frame,)).start()
    assert slot.get(timeout=2.0) is frame
