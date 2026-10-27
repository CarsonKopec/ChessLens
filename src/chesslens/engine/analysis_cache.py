"""Finished analyses keyed by the complete position and engine settings (spec §6.5)."""

from __future__ import annotations

import threading
from collections import OrderedDict

import chess

from chesslens.engine.results import AnalysisResult


def state_key(board: chess.Board, settings: dict) -> tuple:
    """Everything that changes the engine's answer: placement, side to move, castling, en passant,
    clocks (the 50-move rule), and the search settings."""
    return (board.fen(en_passant="fen"), tuple(sorted(settings.items())))


class AnalysisCache:
    """Small thread-safe LRU of final results."""

    def __init__(self, capacity: int = 256) -> None:
        self._capacity = capacity
        self._items: OrderedDict[tuple, AnalysisResult] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> AnalysisResult | None:
        with self._lock:
            result = self._items.get(key)
            if result is not None:
                self._items.move_to_end(key)
            return result

    def put(self, key: tuple, result: AnalysisResult) -> None:
        if not result.final:
            return
        with self._lock:
            self._items[key] = result
            self._items.move_to_end(key)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)
