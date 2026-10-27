"""FEN import/export for the position editor (spec FR-10). PGN arrives with move tracking."""

from __future__ import annotations

import chess


def parse_fen(text: str) -> chess.Board:
    """Board from a full FEN or only its piece-placement field.

    Missing fields default to White to move, no castling, no en passant.
    Raises ValueError with a readable message; the result may still be an
    illegal position (see validations.position_problems).
    """
    text = text.strip()
    if not text:
        raise ValueError("The FEN is empty.")
    try:
        return chess.Board(text)
    except ValueError as exc:
        raise ValueError(f"Not a valid FEN: {exc}") from exc


def export_fen(board: chess.Board) -> str:
    """Full FEN; the en passant square appears only when a capture there is legal, as engines expect."""
    return board.fen()
