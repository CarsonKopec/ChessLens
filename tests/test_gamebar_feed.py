"""The analysis file ChessLens keeps in the Xbox Game Bar widget's app-data folder."""

import json
import sys
from pathlib import Path

import pytest
from conftest import dispose, process_until

from chesslens import gamebar
from chesslens.config import Config
from chesslens.engine.coordinator import AnalysisState, AnalysisStatus
from chesslens.engine.results import AnalysisResult, EngineLine
from chesslens.gamebar import FEED_FILE, HEARTBEAT_S, GameBarFeed

FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def result(final=True) -> AnalysisResult:
    lines = (
        EngineLine(1, "c7c5", "c5", -24, None, ("c7c5", "g1f3"), "1... c5 2. Nf3"),
        EngineLine(2, "e7e5", "e5", -31, None, ("e7e5",), "1... e5"),
    )
    return AnalysisResult(7, FEN, 18, 1_000_000, 1000, lines, final)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def widget(isolated_appdata) -> Path:
    """The folder Windows gives the installed widget once it has run."""
    folder = isolated_appdata / "Packages" / f"{gamebar.WIDGET_PACKAGE}_8wekyb3d8bbwe" / "LocalState"
    folder.mkdir(parents=True)
    return folder


def read(folder: Path) -> dict:
    return json.loads((folder / FEED_FILE).read_text(encoding="utf-8"))


def test_nothing_is_written_without_the_widget(isolated_appdata):
    feed = GameBarFeed()
    feed.show_analysis(result())
    assert not feed.tick()
    assert not (isolated_appdata / "Packages").exists()


def test_analysis_reaches_the_widget(widget):
    feed = GameBarFeed()
    feed.show_analysis(result(), "Following: 1 move, Black to move.")
    assert feed.tick()
    doc = read(widget)
    assert doc["format"] == 1 and doc["state"] == "analysis" and doc["fen"] == FEN
    assert doc["turn"] == "Black to move" and doc["eval"] == "-0.24" and 0 < doc["white_share"] < 0.5
    assert [line["move"] for line in doc["lines"]] == ["c5", "e5"] and doc["lines"][0]["pv"] == "1... c5 2. Nf3"
    assert doc["game"] == "Following: 1 move, Black to move." and "allowed" in doc["fair_play"]
    assert not (widget / f"{FEED_FILE}.tmp").exists()


def test_writes_only_on_change_or_heartbeat(widget):
    clock = Clock()
    feed = GameBarFeed(clock=clock)
    feed.show_analysis(result())
    assert feed.tick()
    clock.now += 1
    assert not feed.tick()  # nothing new
    clock.now += HEARTBEAT_S
    assert feed.tick() and read(widget)["written_at"] == clock.now  # still alive
    feed.show_analysis(result(final=False))
    assert feed.tick()


@pytest.mark.parametrize(
    "status, shown",
    [
        (AnalysisStatus(AnalysisState.OFF), "Engine analysis is off in ChessLens."),
        (AnalysisStatus(AnalysisState.GAME_OVER, "Checkmate: White wins."), "Checkmate: White wins."),
        (AnalysisStatus(AnalysisState.ILLEGAL, "Black has no king."), "Black has no king."),
    ],
)
def test_no_analysis_says_why(widget, status, shown):
    feed = GameBarFeed()
    feed.show_analysis(result())
    feed.show_status(status)
    feed.tick()
    doc = read(widget)
    assert doc["state"] == "message" and doc["message"] == shown and "lines" not in doc


def test_a_new_position_clears_the_old_lines(widget):
    feed = GameBarFeed()
    feed.show_analysis(result())
    feed.show_status(AnalysisStatus(AnalysisState.THINKING))
    feed.tick()
    assert read(widget)["state"] == "thinking" and "lines" not in read(widget)


def test_closing_says_so(widget):
    feed = GameBarFeed()
    feed.show_analysis(result())
    feed.close()
    assert read(widget)["state"] == "closed"


def test_a_write_blocked_by_the_reader_is_retried(widget, monkeypatch):
    feed = GameBarFeed()
    feed.show_analysis(result())
    real_replace = gamebar.os.replace

    def busy(src, dst):
        raise PermissionError("the widget has the file open")

    monkeypatch.setattr(gamebar.os, "replace", busy)
    assert not feed.tick()
    monkeypatch.setattr(gamebar.os, "replace", real_replace)
    assert feed.tick() and read(widget)["state"] == "analysis"


def test_widget_installed_later_is_found(isolated_appdata):
    clock = Clock()
    feed = GameBarFeed(clock=clock)
    feed.show_analysis(result())
    assert not feed.tick()
    folder = isolated_appdata / "Packages" / f"{gamebar.WIDGET_PACKAGE}_abc" / "LocalState"
    folder.mkdir(parents=True)
    clock.now += HEARTBEAT_S
    assert feed.tick() and read(folder)["state"] == "analysis"


@pytest.mark.skipif(sys.platform != "win32", reason="dashboard lists Win32 windows")
def test_dashboard_feeds_the_widget(widget, qapp, monkeypatch, tmp_path):
    from chesslens.ui.main_window import MainWindow

    monkeypatch.setenv("FAKE_UCI_LOG", str(tmp_path / "uci.log"))
    monkeypatch.setattr(MainWindow, "_confirm_fair_play", lambda self: True)
    config = Config()
    config.engine.think_time_s = 0.3
    win = MainWindow(config, engine_command=[sys.executable, str(Path(__file__).with_name("fake_uci.py"))])
    try:
        process_until(qapp, lambda: (widget / FEED_FILE).exists())
        assert read(widget)["message"] == "Engine analysis is off in ChessLens."
        win._analysis.toggle.setChecked(True)
        process_until(qapp, lambda: read(widget).get("final"))
        doc = read(widget)
        assert doc["state"] == "analysis" and len(doc["lines"]) == 3 and doc["game"]
        win.close()
        assert read(widget)["state"] == "closed"
    finally:
        dispose(win)
