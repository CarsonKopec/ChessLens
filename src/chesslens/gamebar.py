"""Feed the analysis to the Xbox Game Bar widget (gamebar-widget/ in the repository).

Game Bar only hosts widgets that are UWP apps, so the widget is a small app of
its own. Such an app can't open other programs' files or, by default, reach
the network loopback, but it can read its own app-data folder, and ChessLens
may write there. So ChessLens keeps one small JSON file in the widget's
LocalState folder up to date, and the widget reads it a few times a second.

Nothing is written unless the widget is installed and has run once (its
folder exists). Only what the dashboard shows is written: evaluation, lines,
game status and the fair-play reminder. No images, nothing about the player.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from chesslens.engine.coordinator import AnalysisState, AnalysisStatus
from chesslens.engine.results import AnalysisResult, bar_fraction

WIDGET_PACKAGE = "ChessLens.GameBarWidget"  # Identity Name in gamebar-widget/Package.appxmanifest
FEED_FILE = "analysis.json"
FEED_FORMAT = 1
HEARTBEAT_S = 5.0  # rewritten at least this often; the widget calls ChessLens closed after three missed
FAIR_PLAY = "Engine help only where it's allowed: bots, puzzles, study, finished games."

_STATUS_MESSAGES = {
    AnalysisState.OFF: "Engine analysis is off in ChessLens.",
    AnalysisState.NO_ENGINE: "Stockfish wasn't found. In ChessLens: Settings > Locate Stockfish.",
}


def packages_dir() -> Path | None:
    """Where Windows keeps each installed app's data (%LOCALAPPDATA%\\Packages)."""
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Packages" if base else None


def widget_folder() -> Path | None:
    """The widget's LocalState folder, once it is installed and has run; else None."""
    packages = packages_dir()
    if packages is None or not packages.is_dir():
        return None
    for package in sorted(packages.glob(f"{WIDGET_PACKAGE}_*")):
        if (package / "LocalState").is_dir():
            return package / "LocalState"
    return None


class GameBarFeed:
    """What the widget should show, written to its folder by tick() when it changed or is due a heartbeat.

    Call the show_* methods from the UI thread as things change and tick()
    a few times a second. A write that fails (the widget reading at that very
    moment) is tried again at the next tick.
    """

    def __init__(self, find_folder: Callable[[], Path | None] = widget_folder, clock: Callable[[], float] = time.time) -> None:
        self._find_folder = find_folder
        self._clock = clock
        self._folder: Path | None = None
        self._looked_at = float("-inf")
        self._written_at = float("-inf")
        self._dirty = True
        self._snapshot: dict = {"state": "message", "message": "ChessLens is running. Turn on Engine analysis there."}

    @property
    def snapshot(self) -> dict:
        return dict(self._snapshot)

    def show_analysis(self, result: AnalysisResult, game: str = "") -> None:
        best = result.best
        board_turn = result.fen.split()[1] if len(result.fen.split()) > 1 else "w"
        self._set(
            state="analysis",
            fen=result.fen,
            turn="White to move" if board_turn == "w" else "Black to move",
            eval=best.score_text if best else "",
            white_share=round(bar_fraction(best), 3),
            depth=result.depth,
            final=result.final,
            lines=[{"rank": line.rank, "move": line.san, "eval": line.score_text, "pv": line.pv_san} for line in result.lines],
            game=game,
        )

    def show_status(self, status: AnalysisStatus, game: str = "") -> None:
        if status.state is AnalysisState.THINKING:
            # A new position: the old lines no longer apply, and results follow within a second.
            self._set(state="thinking", message="Thinking…", game=game)
        else:
            self._set(state="message", message=_STATUS_MESSAGES.get(status.state, status.detail or status.state.value), game=game)

    def close(self) -> None:
        """ChessLens is closing: say so, so the widget doesn't keep showing an old position."""
        self._set(state="closed", message="ChessLens is closed.")
        self.tick()

    def tick(self) -> bool:
        """Write the snapshot if it changed or a heartbeat is due. Returns whether it was written."""
        now = self._clock()
        if not self._dirty and now - self._written_at < HEARTBEAT_S:
            return False
        folder = self._widget_folder(now)
        if folder is None:
            return False
        document = {"format": FEED_FORMAT, "written_at": now, "heartbeat_s": HEARTBEAT_S, "fair_play": FAIR_PLAY, **self._snapshot}
        temp = folder / f"{FEED_FILE}.tmp"
        try:
            temp.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, folder / FEED_FILE)  # all at once, so the widget never reads half a file
        except OSError:  # the widget had the file open (retried next tick), or it was uninstalled
            if not folder.is_dir():
                self._folder = None
            return False
        self._written_at, self._dirty = now, False
        return True

    def _set(self, **fields) -> None:
        self._snapshot = fields
        self._dirty = True

    def _widget_folder(self, now: float) -> Path | None:
        if self._folder is not None and self._folder.is_dir():
            return self._folder
        if now - self._looked_at >= HEARTBEAT_S:  # installing the widget later is noticed within a heartbeat
            self._looked_at = now
            self._folder = self._find_folder()
        return self._folder
