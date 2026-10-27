"""Following a game in the dashboard: the capture → read → track pipeline, and the game controls."""

import json
import sys
from pathlib import Path

import chess
import cv2
import numpy as np
import pytest
from conftest import dispose, process_until
from synthetic import mirror_window, phone_screen, render_board

from chesslens.config import Config
from chesslens.domain.models import Orientation
from chesslens.vision.piece_classifier import CLASSES, BoardObservation
from chesslens.vision.piece_set import PieceSet

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="dashboard lists Win32 windows")

if sys.platform == "win32":
    from chesslens.ui.main_window import MainWindow


def game_recording(
    path: Path, sans: list[str], seconds_each: float = 1.5, hold_last: float = 30.0, durations: list[float] | None = None
) -> Path:
    """A 1084×680 recording of a phone showing a game move by move, last move highlighted as Chess.com does.

    `durations` (seconds per position, starting with the initial one) overrides `seconds_each`.
    """
    board = chess.Board()
    positions = [(board.board_fen(), ())]
    for san in sans:
        move = board.push_san(san)
        positions.append((board.board_fen(), (move.from_square, move.to_square)))
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (1084, 680))
    for i, (fen, highlight) in enumerate(positions):
        frame, _, _ = mirror_window(phone_screen(render_board(fen, square=50, letters=True, highlight=highlight))[0], height=680)
        seconds = hold_last if i == len(positions) - 1 else (durations[i] if durations else seconds_each)
        for _ in range(max(1, int(10 * seconds))):
            writer.write(frame)
    writer.release()
    return path


@pytest.fixture
def no_dialogs(monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm_learning", lambda self, folder: True)
    monkeypatch.setattr(MainWindow, "_confirm_tracking_start", lambda self, board: True)


def test_follows_a_game_from_the_screen(qapp, tmp_path, no_dialogs):
    video = game_recording(tmp_path / "game.avi", ["e4", "e5", "Nf3"])
    win = MainWindow(Config(), video=video)
    win.resize(1560, 900)
    win.show()
    try:
        win._start_btn.click()
        process_until(qapp, lambda: win._calibration is not None)
        win._learn_pieces()  # the recording opens on the start position, as the editor does
        win._follow_box.setChecked(True)
        process_until(qapp, lambda: win._tracker.board.ply() == 3, timeout=15)
        assert [san for _, san, _ in win._tracker.move_list()] == ["e4", "e5", "Nf3"]
        board = win._panel.board
        assert board.turn == chess.BLACK and board.castling_rights == chess.BB_CORNERS
        assert win._panel.editor._last_move == chess.Move.from_uci("g1f3")
        assert "1. e4 e5 2." in win._game_panel.moves.toPlainText()
        assert win._game_panel.status.text() == "Following: 3 moves, Black to move."
    finally:
        dispose(win)


def test_follows_a_fast_bot_game(qapp, tmp_path, no_dialogs):
    # The "bot" answers 0.2 s after each move and once a premove fires at once: positions that
    # never stay still long enough to be read on their own.
    sans = ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"]
    durations = [2.0, 0.2, 1.5, 0.2, 1.5, 0.2, 0.1, 1.5, 0.2, 1.5]
    video = game_recording(tmp_path / "bot.avi", sans, durations=durations)
    win = MainWindow(Config(), video=video)
    win.resize(1560, 900)
    win.show()
    try:
        win._start_btn.click()
        process_until(qapp, lambda: win._calibration is not None)
        win._learn_pieces()
        win._follow_box.setChecked(True)
        process_until(qapp, lambda: len(win._tracker.move_list()) == len(sans), timeout=25)
        assert [san for _, san, _ in win._tracker.move_list()] == sans  # the highlights fix the order
        played = chess.Board()
        for san in sans:
            played.push_san(san)
        assert win._panel.board.fen().split()[:4] == played.fen().split()[:4]
    finally:
        dispose(win)


# -- controls, driven with hand-made readings --------------------------------------


def reading_of(
    placement: chess.BaseBoard, orientation=Orientation.WHITE_BOTTOM, unsure=(), confidence=0.5, runner_up=None
) -> BoardObservation:
    """A reading of `placement`; `unsure` squares get `confidence` (and `runner_up`, if given, the rest)."""
    probs = np.zeros((8, 8, len(CLASSES)), np.float32)
    for sq in chess.SQUARES:
        piece = placement.piece_at(sq)
        if orientation is Orientation.WHITE_BOTTOM:
            r, c = 7 - chess.square_rank(sq), chess.square_file(sq)
        else:
            r, c = chess.square_rank(sq), 7 - chess.square_file(sq)
        probs[r, c, CLASSES.index(piece.symbol() if piece else ".")] = confidence if sq in unsure else 1.0
        if sq in unsure and runner_up:
            probs[r, c, CLASSES.index(runner_up)] = 1.0 - confidence - 0.01
    squares = np.full((8, 8, 48, 48, 3), 128, np.uint8)
    for r in range(8):
        for c in range(8):
            squares[r, c, 10:38, 14:34] = (r * 8 + c) * 3  # a distinct picture per square
    return BoardObservation(probs, orientation, np.zeros((8, 8), bool), squares=squares)


def after(*sans: str) -> chess.BaseBoard:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return chess.BaseBoard(board.board_fen())


@pytest.fixture
def following(qapp, no_dialogs):
    win = MainWindow(Config())
    win.show()
    win._follow_box.setChecked(True)
    yield win
    dispose(win)


def see(win, placement, **kw):
    win._apply_observation(reading_of(placement, **kw), into_editor=True)


def test_moves_update_editor_side_to_move_and_list(following):
    win = following
    see(win, after("d4"))
    see(win, after("d4", "d5"))
    # Clocks and move number come from the game; no en passant shown as no capture is possible.
    assert win._panel.fen() == "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2"
    assert win._game_panel.moves.toPlainText().split() == ["1.", "d4", "d5"]


def test_unexplained_screen_offers_the_screen_position(following):
    win = following
    see(win, after("e4"))
    odd = chess.BaseBoard("4k3/8/8/8/8/8/8/4K2R")
    see(win, odd)
    assert win._tracker.board.ply() == 1  # nothing invented
    assert "No legal move explains the board" in win._game_panel.notice.text()
    assert win._game_panel.use_screen.isVisibleTo(win)
    win._game_panel.use_screen.click()
    assert win._panel.board.board_fen() == odd.board_fen()
    assert win._tracker.board.board_fen() == odd.board_fen() and not win._tracker.board.move_stack
    assert "e4" in win._tracker.pgn()  # the game before it is kept


def test_hand_correction_restarts_following_from_it(following):
    win = following
    see(win, after("e4"))
    win._panel.set_piece(chess.H2, None)  # the user fixes something by hand
    assert win._tracker.board.board_fen() == win._panel.board.board_fen()
    assert "corrected position" in win._game_panel.notice.text()
    see(win, chess.BaseBoard("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PP1/RNBQKBNR"))
    assert [san for _, san, _ in win._tracker.move_list()] == ["e5"]


def test_undo(following):
    win = following
    see(win, after("e4"))
    see(win, after("e4", "e5"))
    win._game_panel.undo.click()
    assert win._panel.board.board_fen() == after("e4").board_fen() and win._panel.board.turn == chess.BLACK


def test_new_game_clears_everything_about_the_old_game(following, monkeypatch):
    win = following
    monkeypatch.setattr(MainWindow, "_confirm_new_game", lambda self: True)
    win._tracker.start(chess.Board())  # an earlier game of the session...
    see(win, after("e4"))
    win._tracker.new_game()
    see(win, after("d4"))  # ...and the current one
    see(win, chess.BaseBoard("4k3/8/8/8/8/8/8/4K2R"))  # an unexplained reading, offering the screen position
    win._panel.editor.set_arrow(chess.Move.from_uci("g1f3"))
    win._analysis._line_labels[0].setText("+0.30 Nf3")
    win._game_panel.new_game.click()
    assert win._panel.board.fen() == chess.STARTING_FEN and win._panel.editor._last_move is None
    assert win._panel.editor._arrow is None and win._analysis._line_labels[0].text() == ""
    assert not win._tracker.has_moves and win._tracker.archived == []
    assert "e4" not in win._tracker.pgn() and "d4" not in win._tracker.pgn()
    assert win._game_panel.moves.toPlainText() == "No moves yet."
    assert win._game_panel.notice.text() == "New game from the starting position."
    assert not win._game_panel.use_screen.isVisibleTo(win) and win._unexplained is None


def test_new_game_asks_before_discarding_moves(following, monkeypatch):
    win = following
    asked = []
    monkeypatch.setattr(MainWindow, "_confirm_new_game", lambda self: asked.append(1) or False)
    see(win, after("e4"))
    win._game_panel.new_game.click()
    assert asked and [san for _, san, _ in win._tracker.move_list()] == ["e4"]  # cancelled: nothing cleared
    asked.clear()
    win._tracker.clear()
    win._game_panel.new_game.click()
    assert not asked  # nothing to lose, no question


def test_flipped_board_switches_orientation_not_the_game(following):
    win = following
    see(win, after("e4"))
    see(win, after("e4"), orientation=Orientation.WHITE_BOTTOM)  # unchanged
    rotated = after("e4").transform(chess.flip_vertical).transform(chess.flip_horizontal)
    see(win, rotated)
    assert win._orientation is Orientation.BLACK_BOTTOM
    assert win._tracker.board.ply() == 1


def test_waiting_on_unreadable_squares(following):
    win = following
    see(win, after("e4"), unsure=(chess.E4,), confidence=0.3)  # below half-sure: e.g. something covers e4
    assert win._tracker.board.ply() == 0
    assert "unclear" in win._game_panel.notice.text()


def test_half_sure_landing_square_is_settled_by_the_only_legal_move(following):
    win = following
    see(win, after("e4"), unsure=(chess.E4,), confidence=0.5, runner_up="B")  # pawn 50/49 against a bishop
    assert [san for _, san, _ in win._tracker.move_list()] == ["e4"]


@pytest.fixture
def with_pieces(following):
    win = following
    win._pieces = PieceSet("Chess.com", {"P": [np.zeros((48, 48, 3), np.uint8)]})
    return win


def test_half_sure_squares_are_learned_once_the_game_confirms_them(with_pieces, isolated_appdata):
    win = with_pieces
    see(win, after("e4"))
    see(win, after("e4"), unsure=(chess.E1,), confidence=0.55, runner_up="Q")  # the king read 55/44 against a queen
    assert len(win._pieces.examples["K"]) == 1  # learned as the king the game says is there
    assert (isolated_appdata / "ChessLens" / "pieces" / "Chess.com.npz").exists()
    see(win, after("e4"), unsure=(chess.E1,), confidence=0.55, runner_up="Q")
    assert len(win._pieces.examples["K"]) == 1  # the same picture isn't learned twice


def test_unreadable_piece_is_learned_when_it_was_a_close_call(with_pieces):
    # As on the live d8 king: best guess a knight at 0.38, the king the game confirms at 0.33.
    win = with_pieces
    see(win, after("e4"))
    reading = reading_of(after("e4"), unsure=(chess.E8,), confidence=0.33)
    reading.probabilities[0, 4, CLASSES.index("n")] = 0.38
    win._apply_observation(reading, into_editor=True)
    assert len(win._pieces.examples["k"]) == 1


def test_overlay_like_or_far_off_squares_are_never_learned(with_pieces):
    win = with_pieces
    see(win, after("e4"))
    see(win, after("e4"), unsure=(chess.D1,), confidence=0.1)  # unreadable, the queen barely considered: an overlay
    assert "Q" not in win._pieces.examples
    see(win, after("e4"), unsure=(chess.E4,), confidence=0.3)  # an empty square is never learned from an unreadable one
    assert "." not in win._pieces.examples
    # Half-sure, but the reading gave the queen the game says is on d1 almost no chance.
    probs_off = reading_of(after("e4"), unsure=(chess.D1,), confidence=0.5, runner_up="K")
    probs_off.probabilities[7, 3, CLASSES.index("Q")] = 0.05
    probs_off.probabilities[7, 3, CLASSES.index("K")] = 0.5
    win._apply_observation(probs_off, into_editor=True)
    assert "Q" not in win._pieces.examples


def test_nothing_is_learned_when_the_screen_is_unexplained(with_pieces):
    win = with_pieces
    see(win, chess.BaseBoard("4k3/8/8/8/8/8/8/4K2R"), unsure=(chess.E1,), confidence=0.6)
    assert "K" not in win._pieces.examples


def test_several_moves_at_once_are_caught_up_and_explained(following):
    win = following
    see(win, after("e4", "e5", "Nf3"))  # no highlight in this reading, so the order is a guess
    assert len(win._tracker.move_list()) == 3
    assert win._panel.board.board_fen() == after("e4", "e5", "Nf3").board_fen() and win._panel.board.turn == chess.BLACK
    notice = win._game_panel.notice.text()
    assert notice.startswith("3 moves came faster than the screen settled") and "doesn't show which order" in notice


def test_new_game_turned_round_switches_orientation(following):
    win = following
    see(win, after("e4"))
    see(win, after("e4", "e5"))
    turned = chess.BaseBoard().transform(chess.flip_vertical).transform(chess.flip_horizontal)
    see(win, turned)  # read with White at the bottom, but the phone now shows Black at the bottom
    assert win._orientation is Orientation.BLACK_BOTTOM
    assert win._tracker.board.fen() == chess.STARTING_FEN
    assert "with the board turned round" in win._game_panel.notice.text()


def test_tracking_log_saves_what_was_seen_and_new_game_clears_it(following, tmp_path, monkeypatch):
    win = following
    see(win, after("e4"))
    see(win, chess.BaseBoard("4k3/8/8/8/8/8/8/4K2R"))  # the moment it lost track
    assert len(win._tracking_log) == 2
    monkeypatch.setattr("chesslens.ui.main_window.QFileDialog.getExistingDirectory", lambda *a, **k: str(tmp_path))
    win._save_tracking_log()
    folder = next(tmp_path.glob("chesslens-tracking-*"))
    records = json.loads((folder / "log.json").read_text(encoding="utf-8"))
    assert [r["result"] for r in records] == ["moves", "unexplained"]
    assert records[0]["moves"] == ["e4"] and records[1]["read"] == "4k3/8/8/8/8/8/8/4K2R"
    assert (folder / records[1]["image"]).exists()
    monkeypatch.setattr(MainWindow, "_confirm_new_game", lambda self: True)
    win._game_panel.new_game.click()
    assert len(win._tracking_log) == 0


def test_following_from_a_middlegame_asks_first(qapp, monkeypatch):
    asked = []
    monkeypatch.setattr(MainWindow, "_confirm_tracking_start", lambda self, board: asked.append(board.fen()) or False)
    win = MainWindow(Config())
    try:
        win._panel.load_fen("4k3/8/8/8/8/8/8/R3K3 b Q - 0 30")
        win._follow_box.setChecked(True)
        assert asked == ["4k3/8/8/8/8/8/8/R3K3 b Q - 0 30"]
        assert not win._follow_box.isChecked() and win._tracker is None
    finally:
        dispose(win)


def test_save_pgn(following, tmp_path, monkeypatch):
    win = following
    see(win, after("e4"))
    see(win, after("e4", "c5"))
    target = tmp_path / "games.pgn"
    monkeypatch.setattr("chesslens.ui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), ""))
    win._save_pgn()
    text = target.read_text(encoding="utf-8")
    assert '[Event "ChessLens session"]' in text and "1. e4 c5 *" in text
