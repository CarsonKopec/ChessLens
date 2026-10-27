from pathlib import Path

import cv2
import numpy as np
import pytest

from chesslens.capture import SourceLostError, SourceState, VideoFileSource


@pytest.fixture
def ramp_video(tmp_path: Path) -> Path:
    """10 frames whose grey level steps by 20, so each frame is identifiable after lossy encoding."""
    path = tmp_path / "ramp.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (32, 24))
    for i in range(10):
        writer.write(np.full((24, 32, 3), 20 * i + 10, np.uint8))
    writer.release()
    return path


def level(frame: np.ndarray) -> int:
    return round((float(frame.mean()) - 10) / 20)


def test_reads_every_frame_in_order_then_ends(ramp_video):
    source = VideoFileSource(ramp_video)
    source.start()
    levels = []
    while (frame := source.read()) is not None:
        levels.append(level(frame))
    source.stop()

    assert levels == list(range(10))
    assert source.status.state is SourceState.ENDED


def test_loop_restarts_from_first_frame(ramp_video):
    source = VideoFileSource(ramp_video, loop=True)
    source.start()
    levels = [level(source.read()) for _ in range(13)]
    source.stop()

    assert levels == list(range(10)) + [0, 1, 2]
    assert source.status.ok


def test_realtime_skips_frames_to_keep_pace(ramp_video, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("chesslens.capture.recorded_video.time.monotonic", lambda: clock[0])
    source = VideoFileSource(ramp_video, realtime=True)
    source.start()
    assert level(source.read()) == 0
    clock[0] += 0.5  # 10 fps video: half a second later we should be on frame 5
    assert level(source.read()) == 5
    source.stop()


def test_missing_file_is_reported_as_lost(tmp_path):
    source = VideoFileSource(tmp_path / "nope.mp4")
    with pytest.raises(SourceLostError):
        source.start()
    assert source.status.state is SourceState.LOST
