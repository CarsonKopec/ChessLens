"""The background reader: reads only once the board holds still, and once per still position."""

import threading
import time

import chess
import cv2
import numpy as np
import pytest
from conftest import wait_for
from synthetic import render_board

from chesslens.capture.base import Frame
from chesslens.domain.models import Orientation
from chesslens.vision.board_reader import BoardReader, ReaderSetup
from chesslens.vision.calibration import BoardCalibration
from chesslens.vision.piece_set import PieceSet

FRAME = (640, 480)
CAL = BoardCalibration.from_rect(120, 40, 400, 400, frame_size=FRAME)


def frame(placement: str, seq: int) -> Frame:
    image = np.full((FRAME[1], FRAME[0], 3), (43, 46, 49), np.uint8)
    image[40:440, 120:520] = render_board(placement, square=50, letters=True)
    return Frame(image=image, seq=seq, timestamp=time.monotonic(), wall_time=time.time(), source_id="test")


@pytest.fixture(scope="module")
def pieces():
    p = PieceSet("reader")
    p.learn(cv2.resize(render_board(square=50, letters=True), (800, 800)), chess.BaseBoard(), Orientation.WHITE_BOTTOM)
    return p


@pytest.fixture
def reader(pieces):
    seen = []
    lock = threading.Lock()

    def on_observation(obs):
        with lock:
            seen.append(obs)

    r = BoardReader(on_observation, stable_frames=2)
    r.configure(ReaderSetup(CAL, pieces, Orientation.WHITE_BOTTOM))
    r.start()
    yield r, seen
    r.stop()


def feed(r: BoardReader, placement: str, count: int, start_seq: int = 0) -> None:
    for i in range(count):
        r.offer(frame(placement, start_seq + i))
        time.sleep(0.06)  # one frame at a time, as capture at ~15 fps would


def test_reads_a_still_board_once(reader):
    r, seen = reader
    feed(r, chess.STARTING_BOARD_FEN, 6)
    wait_for(lambda: seen)
    time.sleep(0.2)
    assert len(seen) == 1
    assert seen[0].placement() == chess.BaseBoard()
    assert seen[0].calibration_version == CAL.version


def test_a_new_position_is_read_after_it_settles(reader):
    r, seen = reader
    feed(r, chess.STARTING_BOARD_FEN, 4)
    after_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"
    feed(r, after_e4, 4, start_seq=10)
    wait_for(lambda: len(seen) >= 2)
    assert seen[-1].placement() == chess.BaseBoard(after_e4)


def test_nothing_is_read_while_the_board_keeps_changing(reader):
    r, seen = reader
    other = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"
    for i in range(8):  # alternating frames: a piece mid-animation, never still
        feed(r, chess.STARTING_BOARD_FEN if i % 2 else other, 1, start_seq=i)
    time.sleep(0.2)
    assert seen == []


def test_new_setup_reads_again_and_none_stops_reading(reader, pieces):
    r, seen = reader
    feed(r, chess.STARTING_BOARD_FEN, 4)
    wait_for(lambda: seen)
    r.configure(ReaderSetup(CAL, pieces, Orientation.BLACK_BOTTOM))
    feed(r, chess.STARTING_BOARD_FEN, 4, start_seq=10)
    wait_for(lambda: len(seen) >= 2)
    assert seen[-1].orientation is Orientation.BLACK_BOTTOM
    r.configure(None)
    count = len(seen)
    feed(r, "8/8/8/8/8/8/8/4K2k", 4, start_seq=20)
    time.sleep(0.2)
    assert len(seen) == count


def test_frames_of_another_size_are_ignored(reader):
    r, seen = reader
    for i in range(4):
        big = np.zeros((600, 800, 3), np.uint8)
        r.offer(Frame(image=big, seq=i, timestamp=time.monotonic(), wall_time=time.time(), source_id="test"))
        time.sleep(0.06)
    time.sleep(0.2)
    assert seen == []
