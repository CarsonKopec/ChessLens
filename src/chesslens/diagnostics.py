"""A short memory of recent readings while following a game, to show what ChessLens saw when it lost track.

Kept in memory only, and saved only when the user asks (File → Save tracking
log…). It holds the board-square images that were read (48×48 each, nothing
else from the screen) and what the tracker made of each reading.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import chess
import cv2
import numpy as np

from chesslens.chess.tracker import Transition
from chesslens.vision.piece_classifier import BoardObservation

LOG_SIZE = 40


@dataclass
class LogEntry:
    at: str
    result: str
    moves: list[str]
    detail: str
    tracked: str  # FEN the tracker holds after this reading
    read: str  # piece placement read from the screen (as oriented when read)
    orientation: str
    lowest_confidence: float
    unreadable: list[str]
    half_sure: list[str]
    highlighted: list[str]
    image: str = ""  # file name of the square mosaic, once saved


class TrackingLog:
    def __init__(self, size: int = LOG_SIZE) -> None:
        self._entries: deque[tuple[LogEntry, np.ndarray | None]] = deque(maxlen=size)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def add(self, observation: BoardObservation, transition: Transition, tracked_fen: str) -> None:
        def names(squares) -> list[str]:
            return sorted(chess.square_name(s) for s in squares)

        entry = LogEntry(
            at=datetime.now().isoformat(timespec="milliseconds"),
            result=transition.kind.value,
            moves=list(transition.sans),
            detail=transition.detail,
            tracked=tracked_fen,
            read=observation.placement().board_fen(),
            orientation=observation.orientation.value,
            lowest_confidence=round(float(observation.confidence.min()), 3),
            unreadable=names(observation.unreadable_squares()),
            half_sure=names(observation.half_sure_squares()),
            highlighted=names(observation.last_move_squares()),
        )
        mosaic = None
        if observation.squares is not None:
            s = observation.squares
            mosaic = s.transpose(0, 2, 1, 3, 4).reshape(s.shape[0] * s.shape[2], s.shape[1] * s.shape[3], 3)
        self._entries.append((entry, mosaic))

    def save(self, folder: Path) -> Path:
        """Write the entries to a new sub-folder of `folder`: one PNG of the read squares per reading
        (as on screen, after removing each square's background) and log.json. Returns the sub-folder."""
        out = folder / f"chesslens-tracking-{time.strftime('%Y%m%d-%H%M%S')}"
        out.mkdir(parents=True, exist_ok=True)
        records = []
        for i, (entry, mosaic) in enumerate(self._entries):
            if mosaic is not None:
                entry.image = f"{i:03d}.png"
                ok, buf = cv2.imencode(".png", mosaic)
                if ok:
                    buf.tofile(out / entry.image)
            records.append(asdict(entry))
        (out / "log.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        return out
