"""Polish: source switching, outages, privacy controls, history, explanations, menus, selftest."""

import sys
from pathlib import Path

import chess
import numpy as np
import pytest
from conftest import dispose
from PySide6.QtWidgets import QMessageBox
from test_game_tracking_ui import after, reading_of

from chesslens.capture.base import SourceState, SourceStatus
from chesslens.config import Config
from chesslens.engine.results import AnalysisResult, EngineLine
from chesslens.persistence.sqlite_store import SessionStore, sessions_path
from chesslens.user_settings import privacy_setting, save_user_settings
from chesslens.vision.calibration import BoardCalibration
from chesslens.vision.piece_set import PieceSet, list_piece_sets

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="dashboard lists Win32 windows")

if sys.platform == "win32":
    from chesslens.ui.main_window import MainWindow
    from chesslens.ui.settings_dialog import PrivacyDialog


@pytest.fixture
def win(qapp, monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm_tracking_start", lambda self, board: True)
    monkeypatch.setattr(MainWindow, "_confirm_new_game", lambda self: True)
    w = MainWindow(Config(), video=Path("one.avi"))
    w._videos.append(Path("two.avi"))
    w._populate_sources()
    w.show()
    yield w
    dispose(w)


def see(w, placement, **kw):
    w._apply_observation(reading_of(placement, **kw), into_editor=True)


# -- source switching and outages ----------------------------------------------------


def test_switching_source_forgets_crop_and_board(win):
    win._set_calibration(BoardCalibration.from_rect(10, 10, 300, 300, frame_size=(640, 480)))
    win._set_roi(None)
    win._source_combo.setCurrentIndex(win._source_combo.findData(Path("two.avi")))
    assert win._calibration is None and win._roi is None and win._auto_detect


def test_outage_pauses_following_and_rereads_when_back(win, monkeypatch):
    win._follow_box.setChecked(True)
    see(win, after("e4"))
    gen = win._generation
    win._on_status(gen, SourceStatus(SourceState.MINIMIZED, "the mirroring window is minimized"))
    assert "Following is paused; the game so far is kept." in win._game_panel.notice.text()
    rereads = []
    monkeypatch.setattr(win, "_configure_reader", lambda reread=True: rereads.append(reread))
    win._on_status(gen, SourceStatus(SourceState.OK))
    assert win._game_panel.notice.text() == "The screen is back. Checking the board against the game…"
    assert rereads == [True]
    assert [san for _, san, _ in win._tracker.move_list()] == ["e4"]


def test_no_board_on_screen_pauses_without_touching_the_game(win):
    win._follow_box.setChecked(True)
    see(win, after("e4"))
    hidden = reading_of(chess.BaseBoard(None))
    object.__setattr__(hidden, "board_visible", False)
    win._apply_observation(hidden, into_editor=True)
    assert "No chessboard is visible" in win._game_panel.notice.text()
    assert [san for _, san, _ in win._tracker.move_list()] == ["e4"]


# -- privacy: history and learning ------------------------------------------------------


def test_game_history_is_only_kept_when_turned_on(win):
    win._follow_box.setChecked(True)
    see(win, after("e4"))
    assert not sessions_path().exists()
    save_user_settings(save_games=True)
    see(win, after("e4", "c5"))
    store = SessionStore()
    try:
        games = store.games()
        assert len(games) == 1 and "1. e4 c5" in games[0][2]
        assert [m.san for m in store.moves(games[0][0])] == ["c5"]  # recorded from when it was turned on
    finally:
        store.close()


def test_history_follows_new_games_and_retention(win, isolated_appdata):
    save_user_settings(save_games=True, retention_days=1)
    win._follow_box.setChecked(True)
    see(win, after("d4"))
    win._game_panel.new_game.click()
    see(win, after("e4"))
    win._close_history()
    store = SessionStore()
    assert store.counts() == (2, 2)
    store._db.execute("UPDATE games SET updated_at = 0")
    store._db.commit()
    store.close()
    win._prune_history()
    store = SessionStore()
    assert store.counts() == (0, 0)
    store.close()


def test_keep_learning_can_be_switched_off(win):
    win._pieces = PieceSet("Chess.com", {"P": [np.zeros((48, 48, 3), np.uint8)]})
    win._follow_box.setChecked(True)
    see(win, after("e4"))
    save_user_settings(keep_learning=False)
    see(win, after("e4"), unsure=(chess.E1,), confidence=0.55, runner_up="Q")
    assert "K" not in win._pieces.examples


def test_privacy_dialog_deletes_and_switches(qapp, monkeypatch):
    PieceSet("Chess.com", {"P": [np.zeros((48, 48, 3), np.uint8)]}).save()
    store = SessionStore()
    store.new_game()
    store.close()
    monkeypatch.setattr("chesslens.ui.settings_dialog.QMessageBox.question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog = PrivacyDialog()
    try:
        assert dialog.piece_list.item(0).text().startswith("Chess.com  (1 examples)")
        assert dialog.history_label.text() == "1 game, 0 moves saved."
        deleted = []
        dialog.pieces_deleted.connect(lambda: deleted.append(1))
        dialog.piece_list.setCurrentRow(0)
        dialog._delete_piece_set()
        assert list_piece_sets() == [] and deleted
        dialog._delete_history()
        assert dialog.history_label.text() == "0 games, 0 moves saved."
        dialog.save_games.setChecked(True)
        dialog.retention.setValue(7)
        dialog.keep_learning.setChecked(False)
        assert privacy_setting("save_games") is True and privacy_setting("retention_days") == 7
        assert privacy_setting("keep_learning") is False
    finally:
        dispose(dialog)


def test_privacy_defaults():
    assert privacy_setting("save_games") is False  # history is opt-in
    assert privacy_setting("keep_learning") is True
    assert privacy_setting("retention_days") == 30


# -- analysis polish ---------------------------------------------------------------------


def best_line(uci: str, san: str, cp: int) -> EngineLine:
    return EngineLine(1, uci, san, cp, None, (uci,), f"1. {san}")


def test_explanation_shows_and_can_be_switched_off(win):
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6"):
        board.push_san(san)
    result = AnalysisResult(1, board.fen(), 12, 1000, 100, (best_line("f1b5", "Bb5", 30),), True)
    win._analysis.show_result(result)
    text = win._analysis.explanation.text()
    assert "Develops the bishop." in text and "Rules of thumb, not the engine's reasoning." in text
    win._analysis.explain_box.setChecked(False)
    assert win._analysis.explanation.text() == ""


def test_finished_analysis_annotates_the_followed_game(win):
    win._follow_box.setChecked(True)
    see(win, after("e4"))
    win._coordinator.enabled = True
    fen = win._tracker.board.fen()
    result = AnalysisResult(win._coordinator.position_version, fen, 20, 10**6, 900, (best_line("c7c5", "c5", 25),), True)
    win._on_engine_result(result)
    assert "1. e4 { [%eval 0.25,20] }" in win._tracker.pgn()


def test_menus(win):
    titles = [a.text() for a in win.menuBar().actions()]
    assert titles == ["&File", "&Settings", "&Help"]
    entries = [a.text() for m in win.menuBar().actions() for a in m.menu().actions() if a.text()]
    assert "&Privacy && data…" in entries and "&About ChessLens" in entries and "Save games as &PGN…" in entries


def test_selftest_passes(capsys):
    from chesslens.selftest import run_selftest

    assert run_selftest(Config()) == 0
    out = capsys.readouterr().out
    assert "All checks passed." in out and "board finding and reading: board found" in out
