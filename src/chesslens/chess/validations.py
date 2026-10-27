"""Plain-language checks on an edited position before it may reach the engine (spec FR-07)."""

from __future__ import annotations

import chess

_MESSAGES: dict[chess.Status, str] = {
    chess.STATUS_NO_WHITE_KING: "White has no king.",
    chess.STATUS_NO_BLACK_KING: "Black has no king.",
    chess.STATUS_TOO_MANY_KINGS: "A side has more than one king.",
    chess.STATUS_TOO_MANY_WHITE_PAWNS: "White has more than 8 pawns.",
    chess.STATUS_TOO_MANY_BLACK_PAWNS: "Black has more than 8 pawns.",
    chess.STATUS_PAWNS_ON_BACKRANK: "There are pawns on the first or last rank.",
    chess.STATUS_TOO_MANY_WHITE_PIECES: "White has more than 16 pieces.",
    chess.STATUS_TOO_MANY_BLACK_PIECES: "Black has more than 16 pieces.",
    chess.STATUS_BAD_CASTLING_RIGHTS: "A castling right doesn't match where the king and rooks are.",
    chess.STATUS_INVALID_EP_SQUARE: "The en passant square isn't possible here.",
    chess.STATUS_OPPOSITE_CHECK: "The side that just moved is in check.",
    chess.STATUS_TOO_MANY_CHECKERS: "The king is attacked by more pieces than one move could allow.",
    chess.STATUS_IMPOSSIBLE_CHECK: "This check couldn't come from a single legal move.",
}


def position_problems(board: chess.Board) -> list[str]:
    """Why `board` is not a legal standard-chess position; empty when it is."""
    status = board.status()
    if status & chess.STATUS_EMPTY:
        return ["The board is empty."]
    return [message for flag, message in _MESSAGES.items() if status & flag]


def placement_problems(board: chess.BaseBoard) -> list[str]:
    """Why a piece placement read from the screen can't come from a real game (spec §6.3).

    Unlike position_problems this needs no side to move, castling or en
    passant, which a screenshot doesn't show.
    """
    problems = []
    for color, side in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
        count = {t: len(board.pieces(t, color)) for t in chess.PIECE_TYPES}
        if count[chess.KING] != 1:
            problems.append(f"{side} has {count[chess.KING] or 'no'} king{'s' if count[chess.KING] > 1 else ''}.")
        if count[chess.PAWN] > 8:
            problems.append(f"{side} has {count[chess.PAWN]} pawns.")
        if sum(count.values()) > 16:
            problems.append(f"{side} has {sum(count.values())} pieces.")
        # Every piece beyond the starting set needs a promoted pawn.
        promoted = (
            max(0, count[chess.QUEEN] - 1)
            + max(0, count[chess.ROOK] - 2)
            + max(0, count[chess.BISHOP] - 2)
            + max(0, count[chess.KNIGHT] - 2)
        )
        if count[chess.PAWN] <= 8 and promoted > 8 - count[chess.PAWN]:
            problems.append(f"{side} has more extra pieces than its missing pawns could have promoted to.")
    if board.pieces(chess.PAWN, chess.WHITE) & chess.BB_BACKRANKS or board.pieces(chess.PAWN, chess.BLACK) & chess.BB_BACKRANKS:
        problems.append("There are pawns on the first or last rank.")
    white_king, black_king = board.king(chess.WHITE), board.king(chess.BLACK)
    if (
        white_king is not None
        and black_king is not None
        and board.is_attacked_by(chess.BLACK, white_king)
        and board.is_attacked_by(chess.WHITE, black_king)
    ):
        problems.append("Both kings are in check.")
    return problems


def possible_castling_rights(board: chess.Board) -> chess.Bitboard:
    """Castling rights the piece placement allows (king and rook still on their home squares)."""
    probe = board.copy(stack=False)
    probe.castling_rights = chess.BB_CORNERS
    return probe.clean_castling_rights()


def en_passant_candidates(board: chess.Board) -> list[chess.Square]:
    """Squares where the side to move could capture en passant if the last move was a double pawn push."""
    rank = 5 if board.turn == chess.WHITE else 2
    found = []
    for file in range(8):
        probe = board.copy(stack=False)
        probe.ep_square = chess.square(file, rank)
        # has_legal_en_passant() alone doesn't check that an enemy pawn could have just double-pushed past it.
        if probe.has_legal_en_passant() and not probe.status() & chess.STATUS_INVALID_EP_SQUARE:
            found.append(probe.ep_square)
    return found
