"""Analysis in the dashboard, against the scripted UCI engine, rendered offscreen."""

import sys
from pathlib import Path

import chess
import pytest
from conftest import dispose, process_until

from chesslens.config import Config
from chesslens.domain.models import Orientation
from chesslens.engine.coordinator import AnalysisState
from chesslens.engine.results import EngineLine
from chesslens.ui.analysis_panel import EvalBar, bar_fraction

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="dashboard lists Win32 windows")

if sys.platform == "win32":
    from chesslens.ui import main_window
    from chesslens.ui.main_window import MainWindow

FAKE = [sys.executable, str(Path(__file__).with_name("fake_uci.py"))]


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_UCI_LOG", str(tmp_path / "uci.log"))
    monkeypatch.setattr(MainWindow, "_confirm_fair_play", lambda self: True)
    config = Config()
    config.engine.think_time_s = 0.3
    win = MainWindow(config, engine_command=FAKE)
    win.resize(1560, 860)
    win.show()
    shown = []
    original = win._analysis.show_result

    def recording_show(result):
        shown.append((result, win._coordinator.position_version))
        original(result)

    monkeypatch.setattr(win._analysis, "show_result", recording_show)
    yield win, shown, tmp_path / "uci.log"
    dispose(win)


def go_count(log: Path) -> int:
    return sum(1 for line in log.read_text().splitlines() if line.startswith("go")) if log.exists() else 0


def test_turning_on_analysis_needs_the_fair_play_reminder(qapp, monkeypatch):
    asked = []
    monkeypatch.setattr(MainWindow, "_confirm_fair_play", lambda self: asked.append(1) or False)
    win = MainWindow(Config(), engine_command=FAKE)
    try:
        win._analysis.toggle.setChecked(True)
        assert asked and not win._analysis.toggle.isChecked()
        assert not win._coordinator.enabled and win._coordinator.engine is None
    finally:
        dispose(win)


def test_analysis_shows_lines_eval_and_best_move_arrow(window, qapp):
    win, shown, _ = window
    win._analysis.toggle.setChecked(True)
    process_until(qapp, lambda: shown and shown[-1][0].final)
    result = shown[-1][0]
    assert len(result.lines) == 3
    first = win._analysis._line_labels[0].text()
    assert result.best.san in first and result.best.score_text in first
    assert win._analysis.status.text().startswith("Done")
    assert win._panel.editor._arrow == chess.Move.from_uci(result.best.uci)
    assert win._analysis.eval_bar._line == result.best
    process_until(qapp, lambda: "FakeFish" in win._analysis.engine_label.text())


def test_reminder_is_shown_once_per_session(window, qapp, monkeypatch):
    win, _, _ = window
    calls = []
    monkeypatch.setattr(MainWindow, "_confirm_fair_play", lambda self: calls.append(1) or True)
    win._analysis.toggle.setChecked(True)
    win._analysis.toggle.setChecked(False)
    win._analysis.toggle.setChecked(True)
    assert len(calls) == 1


def test_edits_never_show_analysis_of_an_older_position(window, qapp):
    win, shown, _ = window
    win._analysis._think.setValue(2.0)  # long enough that every edit lands mid-search
    win._analysis.toggle.setChecked(True)
    process_until(qapp, lambda: shown)
    for square in (chess.A2, chess.B2, chess.C2, chess.D2):  # rapid corrections while it thinks
        win._panel.set_piece(square, None)
        for _ in range(5):
            qapp.processEvents()
    process_until(qapp, lambda: shown[-1][0].final and shown[-1][1] == win._coordinator.position_version, timeout=8)
    # Every analysis that reached the screen belonged to the position on the board at that moment.
    assert all(result.position_version == version for result, version in shown)
    assert chess.Board(shown[-1][0].fen).piece_at(chess.D2) is None


def test_illegal_position_is_explained_and_not_sent(window, qapp):
    win, shown, log = window
    win._analysis.toggle.setChecked(True)
    process_until(qapp, lambda: shown and shown[-1][0].final)
    sent = go_count(log)
    win._panel.set_piece(chess.E8, None)
    assert "Black has no king." in win._analysis.status.text()
    assert win._panel.editor._arrow is None
    for _ in range(20):
        qapp.processEvents()
    assert go_count(log) == sent


def test_checkmate_needs_no_engine(window, qapp):
    win, _, _ = window
    win._analysis.toggle.setChecked(True)
    win._panel.load_fen("R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1")
    assert win._analysis.status.text() == "Checkmate: White wins. Nothing to analyse."


def test_missing_stockfish_shows_install_help(qapp, monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm_fair_play", lambda self: True)
    monkeypatch.setattr(main_window, "find_stockfish", lambda *a: None)
    win = MainWindow(Config())
    win.show()
    try:
        win._analysis.toggle.setChecked(True)
        assert win._analysis._install.isVisible()
        assert "winget install --id Stockfish.Stockfish --exact" in win._analysis._install.text()
        assert win._analysis.status.text() == "No engine."
    finally:
        dispose(win)


def test_eval_bar_fill_and_orientation():
    line = EngineLine(1, "e2e4", "e4", 0, None, ("e2e4",), "1. e4")
    assert bar_fraction(None) == 0.5 and bar_fraction(line) == 0.5
    assert bar_fraction(EngineLine(1, "a", "a", 900, None, (), "")) > 0.95
    assert bar_fraction(EngineLine(1, "a", "a", None, -3, (), "")) == 0.0
    bar = EvalBar()
    bar.set_orientation(Orientation.BLACK_BOTTOM)
    bar.set_line(line)
    assert bar.accessibleDescription() == "Evaluation +0.00"


def test_coordinator_state_after_toggle_off(window, qapp):
    win, _, _ = window
    states = []
    win._coordinator._on_status = lambda s: states.append(s.state)
    win._analysis.toggle.setChecked(True)
    win._analysis.toggle.setChecked(False)
    assert states[-1] is AnalysisState.OFF
    assert win._panel.editor._arrow is None
