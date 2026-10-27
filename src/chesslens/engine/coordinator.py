"""Versions positions, gates what reaches the engine, and filters stale results (spec §9 steps 6–9)."""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass

import chess

from chesslens.chess.validations import position_problems
from chesslens.engine.results import AnalysisResult
from chesslens.engine.stockfish_service import EngineService


class AnalysisState(enum.Enum):
    OFF = "off"
    NO_ENGINE = "no engine"
    ILLEGAL = "illegal position"
    GAME_OVER = "game over"
    THINKING = "thinking"


@dataclass(frozen=True)
class AnalysisStatus:
    state: AnalysisState
    detail: str = ""


def describe_outcome(outcome: chess.Outcome) -> str:
    if outcome.termination is chess.Termination.CHECKMATE:
        return f"Checkmate: {'White' if outcome.winner else 'Black'} wins."
    if outcome.termination is chess.Termination.STALEMATE:
        return "Stalemate: the game is drawn."
    if outcome.termination is chess.Termination.INSUFFICIENT_MATERIAL:
        return "Draw: neither side can checkmate."
    return f"Draw ({outcome.termination.name.replace('_', ' ').lower()})."


class AnalysisCoordinator:
    """Every position change gets a new version. Only legal, unfinished positions are sent to the
    engine, and accept() lets through only results for the current version.

    Not thread-safe: call it from one thread (the UI thread); engine results
    must be handed to accept() on that thread too.
    """

    def __init__(self, on_status: Callable[[AnalysisStatus], None] | None = None) -> None:
        self.position_version = 0
        self.enabled = False
        self.engine: EngineService | None = None
        self._board: chess.Board | None = None
        self._on_status = on_status

    def set_engine(self, engine: EngineService | None) -> None:
        if self.engine is not None and self.engine is not engine:
            self.engine.close()
        self.engine = engine
        self._dispatch()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._dispatch()

    def set_position(self, board: chess.Board) -> int:
        """Record a new confirmed position and return its version."""
        self.position_version += 1
        self._board = board.copy(stack=False)
        self._dispatch()
        return self.position_version

    def refresh(self) -> None:
        """Re-send the current position, e.g. after the search settings changed."""
        self.position_version += 1
        self._dispatch()

    def accept(self, result: AnalysisResult) -> bool:
        return self.enabled and result.position_version == self.position_version

    def close(self) -> None:
        if self.engine is not None:
            self.engine.close()
            self.engine = None

    def _dispatch(self) -> None:
        if not self.enabled:
            if self.engine is not None:
                self.engine.cancel()
            return self._report(AnalysisState.OFF)
        if self.engine is None:
            return self._report(AnalysisState.NO_ENGINE)
        board = self._board
        if board is None:
            return
        problems = position_problems(board)
        if problems:
            self.engine.cancel()
            return self._report(AnalysisState.ILLEGAL, " ".join(problems))
        outcome = board.outcome()
        if outcome is not None:
            self.engine.cancel()
            return self._report(AnalysisState.GAME_OVER, describe_outcome(outcome))
        self.engine.submit(self.position_version, board)
        self._report(AnalysisState.THINKING)

    def _report(self, state: AnalysisState, detail: str = "") -> None:
        if self._on_status is not None:
            self._on_status(AnalysisStatus(state, detail))
