"""Plain-language notes on an engine move from rules of thumb (spec §6.6).

Deterministic and offline. Every sentence is a fact checked on the board:
a capture, a check, castling, development, a threat, a pin, escaping an
attack, or the material change along the engine's line. They describe what
the move does, not why the engine chose it; the engine's evaluation and line
are shown separately, and a sacrifice is flagged rather than explained.
"""

from __future__ import annotations

import chess

from chesslens.engine.results import EngineLine

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
CENTRE = chess.BB_D4 | chess.BB_E4 | chess.BB_D5 | chess.BB_E5
HOME_MINORS = {
    chess.WHITE: chess.BB_B1 | chess.BB_C1 | chess.BB_F1 | chess.BB_G1,
    chess.BLACK: chess.BB_B8 | chess.BB_C8 | chess.BB_F8 | chess.BB_G8,
}
MAX_NOTES = 4


def explain(board: chess.Board, line: EngineLine) -> list[str]:
    """Up to MAX_NOTES short sentences about the line's first move, most important first."""
    move = chess.Move.from_uci(line.uci)
    if move not in board.legal_moves:
        return []
    mover = board.turn
    piece = board.piece_at(move.from_square)
    name = chess.piece_name(piece.piece_type)
    after = board.copy(stack=False)
    after.push(move)
    notes: list[str] = []

    if line.mate_white is not None:
        winning = (line.mate_white > 0) == (mover == chess.WHITE)
        moves = abs(line.mate_white)
        if after.is_checkmate():
            notes.append("Checkmate.")
        elif winning:
            notes.append(f"Forces checkmate in {moves}.")
        else:
            notes.append(f"Every move loses to a forced mate; this one lasts longest ({moves} move{'' if moves == 1 else 's'}).")

    if board.is_castling(move):
        side = "kingside" if chess.square_file(move.to_square) > chess.square_file(move.from_square) else "queenside"
        notes.append(f"Castles {side}: the king gets safer and the rook comes into play.")
    if move.promotion:
        notes.append(f"Promotes the pawn to a {chess.piece_name(move.promotion)}.")
    if board.is_capture(move):
        captured = chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
        how = " en passant" if board.is_en_passant(move) else ""
        recapture = board.move_stack and board.peek().to_square == move.to_square and board.is_capture(board.peek())
        verb = "Recaptures" if recapture else "Captures"
        notes.append(f"{verb} the {chess.piece_name(captured)} on {chess.square_name(move.to_square)}{how}.")
    if after.is_check() and not after.is_checkmate():
        notes.append("Gives check.")

    threat = _new_threat(board, after, move, mover)
    if threat:
        notes.append(threat)
    pin = _new_pin(board, after, mover)
    if pin:
        notes.append(pin)
    if _escapes(board, after, move, mover):
        notes.append(f"Moves the attacked {name} to safety.")
    if (
        piece.piece_type in (chess.KNIGHT, chess.BISHOP)
        and HOME_MINORS[mover] & chess.BB_SQUARES[move.from_square]
        and board.fullmove_number <= 15
    ):
        notes.append(f"Develops the {name}.")
    elif CENTRE & chess.BB_SQUARES[move.to_square] and not board.is_capture(move):
        notes.append(f"Takes a central square ({chess.square_name(move.to_square)}).")
    if _offered(after, move, mover) and line.mate_white is None:
        notes.append(f"Leaves the {name} where it can be taken; the engine's line shows why that's fine.")

    swing = 0 if move.promotion else _material_swing(board, line, mover)  # a promotion is noted already
    if swing >= 2 and not board.is_capture(move):
        notes.append(f"Wins material: {swing} pawns' worth by the end of the line.")
    elif swing <= -2:
        notes.append(f"Gives up {-swing} pawns' worth of material in the line, for the position or attack.")
    return notes[:MAX_NOTES]


def describe_eval(line: EngineLine) -> str:
    """The evaluation in words, from White's point of view. A centipawn score is not a win chance."""
    if line.mate_white is not None:
        side = "White" if line.mate_white > 0 else "Black"
        return f"{side} has a forced mate in {abs(line.mate_white)}."
    cp = line.score_cp_white or 0
    side = "White" if cp > 0 else "Black"
    pawns = abs(cp) / 100
    if pawns < 0.3:
        return "About equal."
    if pawns < 1.0:
        return f"{side} is slightly better."
    if pawns < 2.5:
        return f"{side} is clearly better."
    return f"{side} is winning."


def _new_threat(board: chess.Board, after: chess.Board, move: chess.Move, mover: chess.Color) -> str | None:
    """The most valuable enemy piece the moved piece now attacks that it didn't before,
    if that piece is worth more than the attacker or undefended."""
    attacker_value = VALUES[after.piece_type_at(move.to_square)] or 100
    already = board.attacks(move.from_square)  # what the piece attacked from where it stood
    best = None
    for square in chess.SquareSet(int(after.attacks(move.to_square)) & after.occupied_co[not mover] & ~int(already)):
        target = after.piece_type_at(square)
        if target == chess.KING:
            continue
        undefended = not after.is_attacked_by(not mover, square)
        if VALUES[target] > attacker_value or (undefended and VALUES[target] >= 1):
            if best is None or VALUES[target] > VALUES[best[1]]:
                best = (square, target)
    if best is None:
        return None
    square, target = best
    return f"Attacks the {chess.piece_name(target)} on {chess.square_name(square)}."


def _new_pin(board: chess.Board, after: chess.Board, mover: chess.Color) -> str | None:
    for square in chess.SquareSet(after.occupied_co[not mover] & ~after.kings):
        if after.is_pinned(not mover, square) and not board.is_pinned(not mover, square):
            return f"Pins the {chess.piece_name(after.piece_type_at(square))} on {chess.square_name(square)} to the king."
    return None


def _escapes(board: chess.Board, after: chess.Board, move: chess.Move, mover: chess.Color) -> bool:
    """The piece was en prise before (attacked by something cheaper, or attacked and undefended) and isn't now."""
    piece_type = board.piece_type_at(move.from_square)
    if piece_type in (chess.PAWN, chess.KING):
        return False
    attackers = board.attackers(not mover, move.from_square)
    if not attackers:
        return False
    cheapest = min(VALUES[board.piece_type_at(s)] or 100 for s in attackers)
    was_en_prise = cheapest < VALUES[piece_type] or not board.is_attacked_by(mover, move.from_square)
    return was_en_prise and not after.is_attacked_by(not mover, move.to_square)


def _offered(after: chess.Board, move: chess.Move, mover: chess.Color) -> bool:
    """The moved piece (not a pawn or king) stands attacked and undefended after the move."""
    piece_type = after.piece_type_at(move.to_square)
    if piece_type in (chess.PAWN, chess.KING):
        return False
    return after.is_attacked_by(not mover, move.to_square) and not after.is_attacked_by(mover, move.to_square)


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(VALUES[t] * len(board.pieces(t, color)) for t in VALUES)


def _material_swing(board: chess.Board, line: EngineLine, mover: chess.Color) -> int:
    """Change in (mover's material - opponent's) from now to the end of the engine's line."""
    probe = board.copy(stack=False)
    start = _material(probe, mover) - _material(probe, not mover)
    for uci in line.pv_uci:
        move = chess.Move.from_uci(uci)
        if move not in probe.legal_moves:
            break
        probe.push(move)
    return _material(probe, mover) - _material(probe, not mover) - start
