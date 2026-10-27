"""Opt-in local history of followed games (spec §9 step 10, FR-12, §11 privacy).

Off unless the user turns it on in Privacy & data. Each confirmed move is
kept with when it was played, the position after it and how sure the
screen reading was, plus each game's PGN. No images are stored. Games older
than the chosen retention period are deleted at start-up, and everything
can be deleted on request.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    pgn TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS moves (
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER NOT NULL,
    san TEXT NOT NULL,
    uci TEXT NOT NULL,
    fen TEXT NOT NULL,
    confidence REAL,
    played_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS moves_by_game ON moves(game_id, ply);
"""


def sessions_path() -> Path:
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home()) / "ChessLens" / "sessions.sqlite3"


@dataclass(frozen=True)
class StoredMove:
    ply: int
    san: str
    uci: str
    fen: str  # after the move
    confidence: float | None  # lowest square confidence of the reading that confirmed it


class SessionStore:
    """One SQLite file; use from a single thread."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or sessions_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    def new_game(self, at: float | None = None) -> int:
        now = at or time.time()
        with self._db:
            return self._db.execute("INSERT INTO games (started_at, updated_at) VALUES (?, ?)", (now, now)).lastrowid

    def record_moves(self, game_id: int, moves: list[StoredMove], at: float | None = None) -> None:
        now = at or time.time()
        with self._db:
            self._db.executemany(
                "INSERT INTO moves (game_id, ply, san, uci, fen, confidence, played_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(game_id, m.ply, m.san, m.uci, m.fen, m.confidence, now) for m in moves],
            )
            self._db.execute("UPDATE games SET updated_at = ? WHERE id = ?", (now, game_id))

    def save_pgn(self, game_id: int, pgn: str) -> None:
        with self._db:
            self._db.execute("UPDATE games SET pgn = ?, updated_at = ? WHERE id = ?", (pgn, time.time(), game_id))

    def prune(self, retention_days: float, now: float | None = None) -> int:
        """Delete games last touched more than `retention_days` ago. Returns how many."""
        cutoff = (now or time.time()) - retention_days * 86400
        with self._db:
            return self._db.execute("DELETE FROM games WHERE updated_at < ?", (cutoff,)).rowcount

    def delete_all(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM games")
        self._db.execute("VACUUM")  # actually drop the data from the file

    def counts(self) -> tuple[int, int]:
        games = self._db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        moves = self._db.execute("SELECT COUNT(*) FROM moves").fetchone()[0]
        return games, moves

    def games(self) -> list[tuple[int, float, str]]:
        """(id, started_at, pgn) oldest first."""
        return self._db.execute("SELECT id, started_at, pgn FROM games ORDER BY started_at").fetchall()

    def moves(self, game_id: int) -> list[StoredMove]:
        rows = self._db.execute(
            "SELECT ply, san, uci, fen, confidence FROM moves WHERE game_id = ? ORDER BY rowid", (game_id,)
        ).fetchall()
        return [StoredMove(*row) for row in rows]

    def close(self) -> None:
        self._db.close()
