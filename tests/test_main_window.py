"""Dashboard end to end, rendered offscreen and fed from a recorded video of a synthetic phone screen."""

import json
import sys
from pathlib import Path

import chess
import cv2
import numpy as np
import pytest
from conftest import dispose, process_until
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from synthetic import mirror_window, phone_screen, render_board

from chesslens.config import Config
from chesslens.domain.models import Orientation
from chesslens.vision.piece_classifier import CLASSES, BoardObservation

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="dashboard lists Win32 windows")

if sys.platform == "win32":
    from chesslens.ui.main_window import MainWindow


def phone_recording(path: Path) -> tuple[Path, list[tuple[float, float]]]:
    """A short 1084×680 recording of a phone showing the start position; returns the board's true corners.

    (MJPG needs even dimensions, so this is a pixel shorter than AirScreen's 681.)
    """
    screen, (bx, by) = phone_screen(render_board(square=50, letters=True))
    frame, scale, (px, py) = mirror_window(screen, height=680)
    x0, y0, side = px + bx * scale, py + by * scale, 400 * scale
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (frame.shape[1], frame.shape[0]))
    for _ in range(10):
        writer.write(frame)
    writer.release()
    return path, [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]


@pytest.fixture
def running(qapp, tmp_path):
    video, corners = phone_recording(tmp_path / "phone.avi")
    win = MainWindow(Config(), video=video)
    win.resize(1360, 820)
    win.show()
    win._start_btn.click()
    process_until(qapp, lambda: win._calibration is not None)
    yield win, corners
    dispose(win)


def widget_point(win, x: float, y: float) -> QPoint:
    return win._preview._to_widget(x, y).toPoint()


def cell_centre(corners, row: int, col: int) -> tuple[float, float]:
    (x0, y0), (x1, _), _, (_, y3) = corners
    return x0 + (col + 0.5) * (x1 - x0) / 8, y0 + (row + 0.5) * (y3 - y0) / 8


def test_capture_trims_view_and_finds_board(running):
    win, corners = running
    assert win._last_frame.size == (1084, 680)
    assert win._capture_lamp.text() == "▶ Playing video file"
    assert win._roi is not None  # zoomed to the phone screen
    cal = win._calibration
    assert cal.method == "auto" and cal.confidence >= 0.95
    assert np.abs(cal.corner_array - np.float32(corners)).max() < 2
    assert win._orientation is Orientation.WHITE_BOTTOM
    assert "found automatically" in win._board_label.text()


def test_click_on_captured_square_edits_position(running, qapp):
    win, corners = running
    win._panel.palette.select(chess.Piece(chess.QUEEN, chess.WHITE))
    QTest.mouseClick(win._preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget_point(win, *cell_centre(corners, 4, 3)))
    assert win._panel.board.piece_at(chess.D4) == chess.Piece(chess.QUEEN, chess.WHITE)
    QTest.mouseClick(win._preview, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, widget_point(win, *cell_centre(corners, 6, 4)))
    assert win._panel.board.piece_at(chess.E2) is None


def test_flip_changes_which_square_a_click_hits(running):
    win, corners = running
    win._set_orientation(Orientation.BLACK_BOTTOM)
    assert win._orientation_combo.currentData() is Orientation.BLACK_BOTTOM
    win._panel.palette.select(None)
    QTest.mouseClick(win._preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget_point(win, *cell_centre(corners, 0, 0)))
    assert win._panel.board.piece_at(chess.H1) is None


def test_drag_corner_adjusts_board_by_hand(running, qapp):
    win, corners = running
    version = win._calibration.version
    start = widget_point(win, *corners[2])
    end = start - QPoint(12, 9)  # inward: the board already touches the edge of the trimmed view
    QTest.mousePress(win._preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(win._preview, end)
    QTest.mouseRelease(win._preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    cal = win._calibration
    assert cal.method == "manual" and cal.version > version
    assert cal.corners[2][0] < corners[2][0] - 3 and cal.corners[2][1] < corners[2][1] - 3
    assert np.allclose(cal.corners[0], corners[0], atol=2)  # other corners untouched
    # Dragging past the edge of the view stops at the edge.
    QTest.mousePress(win._preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    QTest.mouseMove(win._preview, end + QPoint(400, 0))
    QTest.mouseRelease(win._preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end + QPoint(400, 0))
    vx, _, vw, _ = win._roi.to_pixels(*win._last_frame.size)
    assert win._calibration.corners[2][0] == pytest.approx(vx + vw)
    # A new frame of the same size keeps the hand-set board.
    process_until(qapp, lambda: win._last_frame is not None and win._calibration.method == "manual")


def test_drag_rectangle_marks_board_and_clear_stops_auto_detection(running, qapp):
    win, corners = running
    win._clear_board()
    assert win._calibration is None
    for _ in range(30):  # several frames arrive; nothing re-detects behind the user's back
        qapp.processEvents()
        QTest.qWait(10)
    assert win._calibration is None
    a, b = widget_point(win, *corners[0]), widget_point(win, *corners[2])
    QTest.mousePress(win._preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(win._preview, (a + b) / 2)
    QTest.mouseMove(win._preview, b)
    QTest.mouseRelease(win._preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    cal = win._calibration
    assert cal is not None and cal.method == "manual"
    assert np.abs(cal.corner_array - np.float32(corners)).max() < 3  # within a widget pixel or so


def test_save_labeled_board(running, tmp_path, monkeypatch):
    win, _ = running
    target = tmp_path / "sample.png"
    monkeypatch.setattr("chesslens.ui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), ""))
    win._panel.set_piece(chess.E2, None)
    win._save_labeled_board()
    image = cv2.imread(str(target))
    assert image.shape == (800, 800, 3)
    label = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
    assert label["fen"] == win._panel.fen() and "4P3" not in label["fen"].split()[0].split("/")[6]
    assert label["orientation"] == "white" and label["calibration"]["method"] == "auto"


@pytest.fixture
def consenting(monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm_learning", lambda self, folder: True)


def test_learn_pieces_then_read_board_into_editor(running, consenting):
    win, _ = running
    assert "isn't learned yet" in win._recognition_label.text()
    win._learn_pieces()  # the editor starts at the start position, as the phone shows
    assert win._pieces is not None and win._pieces.missing == []
    assert "examples learned" in win._recognition_label.text()
    win._panel.load_fen("4k3/8/8/8/8/8/8/4K3 b - - 0 1")  # editor now disagrees with the screen
    win._read_board_now()
    assert win._panel.board.board_fen() == chess.STARTING_BOARD_FEN
    assert win._recognition_label.text().startswith("Updated the editor: 32 pieces.")
    assert win._panel.board.turn == chess.BLACK  # no last-move highlight, so the side to move is kept


def test_learning_checks_the_editor_matches_the_screen(running, consenting, monkeypatch):
    win, _ = running
    asked = []
    monkeypatch.setattr(MainWindow, "_confirm_mismatch", lambda self, squares: asked.append(squares) or False)
    win._panel.set_piece(chess.E2, None)
    win._learn_pieces()
    assert asked == [[chess.E2]]
    assert win._pieces is None


def reading_of(placement: chess.BaseBoard, unsure: tuple[chess.Square, ...] = ()) -> BoardObservation:
    """A hand-made reading of `placement` (White at the bottom) that is unsure about `unsure`."""
    probs = np.zeros((8, 8, len(CLASSES)), np.float32)
    for sq in chess.SQUARES:
        piece = placement.piece_at(sq)
        r, c = 7 - chess.square_rank(sq), chess.square_file(sq)
        probs[r, c, CLASSES.index(piece.symbol() if piece else ".")] = 0.6 if sq in unsure else 1.0
    return BoardObservation(probs, Orientation.WHITE_BOTTOM, np.zeros((8, 8), bool))


def test_unsure_squares_block_following_but_not_an_asked_for_reading(running):
    win, _ = running
    target = chess.BaseBoard("4k3/8/8/8/8/8/8/R3K3")
    win._apply_observation(reading_of(target, unsure=(chess.A1,)), into_editor=True)
    assert win._panel.board.board_fen() == chess.STARTING_BOARD_FEN
    assert win._recognition_label.text() == "Reading not used: Not sure about a1."
    assert win._preview._uncertain == [(7, 0, "R")]
    win._apply_observation(reading_of(target, unsure=(chess.A1,)), into_editor=True, asked=True)
    assert win._panel.board.board_fen() == target.board_fen()
    assert "Check a1 (marked)" in win._recognition_label.text()


def test_impossible_reading_never_reaches_the_editor(running):
    win, _ = running
    two_kings = chess.BaseBoard("4k3/8/8/8/8/8/8/K3K3")
    win._apply_observation(reading_of(two_kings), into_editor=True, asked=True)
    assert win._panel.board.board_fen() == chess.STARTING_BOARD_FEN
    assert win._recognition_label.text() == "Reading not used: White has 2 kings."


def test_learned_pieces_are_there_next_time(running, consenting, qapp):
    win, _ = running
    win._learn_pieces()
    again = MainWindow(Config())
    try:
        assert again._pieces is not None and again._pieces.name == "Chess.com"
        assert again._pieces.example_count == win._pieces.example_count
    finally:
        dispose(again)


def test_refresh_keeps_capturing_the_same_source(running):
    win, _ = running
    manager = win._manager
    win._populate_sources()
    assert win._manager is manager and manager.running
    win._start_btn.click()
    assert win._manager is None
    assert win._capture_lamp.text() == "○ Not capturing"
