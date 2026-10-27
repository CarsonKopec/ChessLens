"""Follow a game from a stream of board readings (spec §6.4).

Each still reading of the screen must be explained by the game so far:
the same position, a step back or forward through it (Chess.com's review
arrows), or legal moves from the current position, one or, when a reply came
too fast to be seen, two. A change is only accepted when exactly one
explanation fits and every square it touches was read with confidence;
otherwise the tracker waits or reports that it can't explain the screen,
rather than inventing moves. Castling, en passant and promotion come out of
the legal-move matching, and a flipped board is recognised as not a move.

The game is kept as a PGN tree, so stepping back and playing something else
adds a variation instead of throwing moves away.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

import chess
import chess.engine
import chess.pgn

_PIECE_BITBOARDS = ("pawns", "knights", "bishops", "rooks", "queens", "kings")


class TransitionKind(enum.Enum):
    NO_CHANGE = "no change"
    MOVES = "moves"  # new moves were played (more than one if they came faster than the screen settled)
    NAVIGATED = "navigated"  # the screen shows another moment of this game
    FLIPPED = "flipped"  # the same position seen from the other side: not a move
    NEW_GAME = "new game"
    WAITING = "waiting"  # unclear for now: unsure squares, or several explanations
    UNEXPLAINED = "unexplained"  # nothing legal explains the screen


@dataclass(frozen=True)
class Transition:
    kind: TransitionKind
    sans: tuple[str, ...] = ()
    detail: str = ""
    flipped: bool = False  # the board is now shown the other way round (switch orientation)


# Catch-up after a gap: bots can reply within the half second the reader waits for a still board, and a
# premove can go off at once, so up to this many moves may land between two readings.
CATCH_UP_PLIES = 4
SEARCH_BUDGET = 60_000  # positions tried in one catch-up at most
# How far back the screen may turn out to have gone on from: an Undo that never sat still on screen, or a
# catch-up guess that the screen has since contradicted.
RESYNC_PLIES = 4


class GameTracker:
    def __init__(self, board: chess.Board | None = None, max_plies: int = 2) -> None:
        self.max_plies = max_plies
        self.archived: list[chess.pgn.Game] = []  # earlier games of this session
        self.game = chess.pgn.Game()
        self.node: chess.pgn.GameNode = self.game
        self._hint: tuple[frozenset[chess.Square], chess.Color | None] = (frozenset(), None)  # see observe()
        self.start(board or chess.Board())

    # -- state ---------------------------------------------------------------

    def start(self, board: chess.Board) -> None:
        """Follow from `board`'s full state (side to move, castling, en passant, clocks).

        Any moves in its stack become the start of the game. A game already in
        progress is kept in the session's archive.
        """
        if self.game.variations:
            self.archived.append(self.game)
        self.game = chess.pgn.Game()
        self.game.headers["Event"] = "ChessLens session"
        self.game.headers["Date"] = date.today().strftime("%Y.%m.%d")
        self.game.setup(board.root())
        self.node = self.game
        for move in board.move_stack:
            self.node = self.node.add_main_variation(move)

    @property
    def board(self) -> chess.Board:
        """The current position, with the moves that led to it."""
        return self.node.board()

    @property
    def last_move(self) -> chess.Move | None:
        return self.node.move if self.node.parent is not None else None

    def undo(self) -> str | None:
        """Take back the current move if nothing follows it (e.g. it was misread). Returns its SAN."""
        parent = self.node.parent
        if parent is None or self.node.variations:
            return None
        san = self.node.san()
        parent.remove_variation(self.node.move)
        self.node = parent
        return san

    def new_game(self) -> None:
        """Start again from the starting position, keeping the game so far in the session's archive."""
        self.start(chess.Board())

    def clear(self) -> None:
        """Forget every game of this session and start again from the starting position."""
        self.archived.clear()
        self.game = chess.pgn.Game()  # nothing left for start() to archive
        self.start(chess.Board())

    @property
    def has_moves(self) -> bool:
        return bool(self.game.variations) or any(g.variations for g in self.archived)

    def pgn(self) -> str:
        """Every game of the session, oldest first, with variations."""
        games = [*self.archived, self.game] if self.game.variations or not self.archived else self.archived
        out = []
        for g in games:
            g.headers["Result"] = _result(g.end().board())
            out.append(str(g))
        return "\n\n".join(out) + "\n"

    def annotate(self, fen: str, cp_white: int | None, mate_white: int | None, depth: int) -> bool:
        """Attach an engine evaluation ([%eval ...] in the PGN) to the move that reached the current
        position, if `fen` is that position. Returns whether it was attached."""
        if self.node.parent is None or self.board.fen() != fen:
            return False
        score = chess.engine.Mate(mate_white) if mate_white is not None else chess.engine.Cp(cp_white or 0)
        self.node.set_eval(chess.engine.PovScore(score, chess.WHITE), depth)
        return True

    def current_pgn(self) -> str:
        """The game being followed now, as PGN."""
        self.game.headers["Result"] = _result(self.game.end().board())
        return str(self.game) + "\n"

    def last_moves(self, count: int) -> list[chess.pgn.GameNode]:
        """The nodes of the last `count` moves up to the current position, oldest first."""
        nodes = []
        node = self.node
        while node.parent is not None and len(nodes) < count:
            nodes.append(node)
            node = node.parent
        return nodes[::-1]

    def move_list(self) -> list[tuple[int, str, bool]]:
        """(ply, SAN, is the current move) along the line through the current position, to its end."""
        path = []
        n = self.node
        while n.parent is not None:
            path.append(n)
            n = n.parent
        path.reverse()
        tail = self.node
        while tail.variations:
            tail = tail.variations[0]
            path.append(tail)
        return [(n.ply(), n.san(), n is self.node) for n in path]

    # -- matching ------------------------------------------------------------

    def observe(
        self,
        placement: chess.BaseBoard,
        unsure: frozenset[chess.Square] = frozenset(),
        half_sure: frozenset[chess.Square] = frozenset(),
        last_move: frozenset[chess.Square] = frozenset(),
        last_mover: chess.Color | None = None,
    ) -> Transition:
        """Explain a still reading of the screen, updating the game if it is clear.

        `unsure` squares couldn't be read (an overlay, a menu): they are
        ignored when comparing, and a move that changes one is not accepted.
        `half_sure` squares were read, but not confidently: they may stay as
        they were whatever the reading says, and a move may change one only to
        exactly what the reading's best guess there is; legality and the
        rest of the board then settle the doubt.

        `last_move` and `last_mover` come from the app's last-move highlight
        when it is clear: the two marked squares and the colour of the piece
        on them. Several moves seen at once must end with that colour moving
        (the screen can't tell four moves from five otherwise), and an order
        ending on the marked squares is preferred.
        """
        doubt = _Doubt(_bitboard(unsure), _bitboard(half_sure) & ~_bitboard(unsure))
        current = self.board
        self._hint = (last_move, last_mover)
        if _fits(current, placement, doubt, current):
            # Nothing confidently changed; say so if part of the board couldn't be read or reads
            # differently from the game (e.g. a promotion picker).
            disagreeing = {s for s in half_sure if placement.piece_at(s) != current.piece_at(s)}
            return Transition(TransitionKind.NO_CHANGE, detail=_unclear(set(unsure) | disagreeing))

        # One step back or forward through this game (review arrows). A move can't undo the
        # opponent's last move, so a step back never competes with a new move.
        for neighbour in [self.node.parent, *self.node.variations]:
            if neighbour is not None and _fits(neighbour.board(), placement, doubt, current):
                self.node = neighbour
                return Transition(TransitionKind.NAVIGATED, detail=_where(neighbour))

        transition = self._play(current, self._explanations_from(current, placement, doubt, current, self.max_plies))
        if transition is not None:
            return transition

        rotated = placement.transform(chess.flip_vertical).transform(chess.flip_horizontal)
        if current.board_fen() != chess.STARTING_BOARD_FEN:
            # The starting position appearing (a step back to the very start was handled above) is a new
            # game; turned round, it's a new game with the other colour at the bottom (e.g. now playing Black).
            for shown, flipped in ((placement, False), (rotated, True)):
                if shown.board_fen() == chess.STARTING_BOARD_FEN:
                    self.new_game()
                    return Transition(TransitionKind.NEW_GAME, flipped=flipped)

        # A jump through this game (review arrows) before guessing at several new moves.
        target = self._further_in_game(placement, doubt, current)
        if target is not None:
            self.node = target
            return Transition(TransitionKind.NAVIGATED, detail=_where(target))

        transition = self._play(current, self._catch_up(current, placement, doubt))
        if transition is not None:
            return transition

        rotated_doubt = _Doubt(_bitboard({s ^ 63 for s in unsure}), _bitboard({s ^ 63 for s in half_sure}))
        if _fits(current, rotated, rotated_doubt, current):
            return Transition(TransitionKind.FLIPPED, flipped=True)

        if not unsure:
            transition = self._from_earlier(placement, doubt)
            if transition is not None:
                return transition

        if unsure or half_sure:
            return Transition(TransitionKind.WAITING, detail=f"Can't tell what changed yet. {_unclear(set(unsure) | set(half_sure))}")
        detail = _describe_difference(current, placement)
        other_side = current.copy(stack=False)
        other_side.turn = not current.turn
        other_side.ep_square = None
        if self._explanations_from(other_side, placement, doubt, current, plies=1):
            mover = "White" if other_side.turn == chess.WHITE else "Black"
            detail += f" It looks like {mover} moved, but the game has {'Black' if mover == 'White' else 'White'} to move: check side to move."
        return Transition(TransitionKind.UNEXPLAINED, detail=detail)

    def _play(self, current: chess.Board, sequences: list[tuple[chess.Move, ...]]) -> Transition | None:
        """Add the moves if every explanation ends in the same position; wait if they disagree; None if there are none.

        Moves can sometimes be played in either order (two of your moves around a quick reply).
        The position is the same either way, so one order is taken and the transition says so.
        """
        last_move, _ = self._hint
        if last_move:
            # The app highlights the last move: keep the explanations ending with it, if any do.
            ending_there = [seq for seq in sequences if {seq[-1].from_square, seq[-1].to_square} == set(last_move)]
            sequences = ending_there or sequences
        endings = {_ending(current, seq) for seq in sequences}
        if not endings:
            return None
        if len(endings) > 1:
            options = "; ".join(" ".join(_san_line(current, seq)) for seq in sequences[:4])
            return Transition(TransitionKind.WAITING, detail=f"Several moves fit ({options}); waiting for a clearer view.")
        sans = []
        for move in sequences[0]:
            sans.append(self.node.board().san(move))
            child = self.node.variation(move) if self.node.has_variation(move) else self.node.add_variation(move)
            self.node.promote_to_main(move)  # what was played last becomes the main line
            self.node = child
        order = "" if len(sequences) == 1 else "The screen doesn't show which order these were played in; this is one that fits."
        return Transition(TransitionKind.MOVES, sans=tuple(sans), detail=order)

    def _explanations_from(self, start, placement, doubt, current, plies) -> list[tuple[chess.Move, ...]]:
        board = start.copy(stack=False)
        for depth in range(1, plies + 1):
            found: list[tuple[chess.Move, ...]] = []
            self._search(board, depth, (), placement, doubt, current, found)
            found = [seq for seq in found if self._agrees_with_highlight(start, seq)]
            if found:
                return found
        return []

    def _agrees_with_highlight(self, start: chess.Board, seq: tuple[chess.Move, ...]) -> bool:
        """Several moves at once must end with the colour the last-move highlight shows moving."""
        _, last_mover = self._hint
        if last_mover is None or len(seq) < 2:
            return True
        ended_by = start.turn if len(seq) % 2 == 1 else not start.turn
        return ended_by == last_mover

    def _catch_up(self, current: chess.Board, placement: chess.BaseBoard, doubt: _Doubt) -> list[tuple[chess.Move, ...]]:
        """Longer gaps, e.g. a bot replying instantly or a premove going off before the board settled.

        Up to CATCH_UP_PLIES moves, trying only moves that start or end on a
        square that differs (or is half-sure) and stopping after
        SEARCH_BUDGET positions, which keeps it to a fraction of a second.
        Not while part of the board can't be read (a piece being dragged
        across it, an animation): with squares hidden, a few moves can be
        made to fit almost anything, e.g. a queen held over the board
        "explained" as captured by a knight that went out and came back.
        """
        if doubt.unsure:
            return []
        relevant = _difference(current, placement) | doubt.half
        if chess.popcount(_difference(current, placement)) > 3 * CATCH_UP_PLIES:
            return []  # more changed than a few moves could change: not a gap, something else
        board = current.copy(stack=False)
        budget = [SEARCH_BUDGET]
        for depth in range(self.max_plies + 1, CATCH_UP_PLIES + 1):
            found: list[tuple[chess.Move, ...]] = []
            self._search(board, depth, (), placement, doubt, current, found, relevant, budget)
            found = [seq for seq in found if self._agrees_with_highlight(current, seq) and _leaves_traces(current, seq)]
            if found or budget[0] <= 0:
                return found
        return []

    def _search(self, board, depth, line, placement, doubt, current, found, relevant=None, budget=None) -> None:
        for move in list(board.legal_moves):
            if relevant is not None and not (chess.BB_SQUARES[move.from_square] | chess.BB_SQUARES[move.to_square]) & relevant:
                continue
            if budget is not None:
                budget[0] -= 1
                if budget[0] <= 0:
                    return
            board.push(move)
            if depth == 1:
                if _fits(board, placement, doubt, current):
                    found.append((*line, move))
            elif not board.is_game_over():
                self._search(board, depth - 1, (*line, move), placement, doubt, current, found, relevant, budget)
            board.pop()

    def _from_earlier(self, placement: chess.BaseBoard, doubt: _Doubt) -> Transition | None:
        """Moves played from one of the last few positions instead of the current one: an Undo whose
        position never sat still on screen, or a guess the screen has since contradicted. The nearest
        position with an explanation wins; it must be the only one there and end on the highlighted move.
        The moves left behind stay in the game as a side line.
        """
        last_move, _ = self._hint
        node = self.node
        for back in range(1, RESYNC_PLIES + 1):
            node = node.parent
            if node is None:
                return None
            start = node.board()
            sequences = [
                seq
                for seq in self._explanations_from(start, placement, doubt, start, self.max_plies)
                if not last_move or {seq[-1].from_square, seq[-1].to_square} == set(last_move)
            ]
            if not sequences:
                continue
            if len({_ending(start, seq) for seq in sequences}) > 1:
                return None
            self.node = node
            played = self._play(start, sequences)
            left = "move ChessLens had after it is" if back == 1 else f"{back} moves ChessLens had after it are"
            detail = f"The game went on from {_where(node)}; the {left} kept as a side line. {played.detail}"
            return Transition(TransitionKind.MOVES, played.sans, detail.strip())
        return None

    def _further_in_game(self, placement, doubt, current) -> chess.pgn.GameNode | None:
        """The nearest earlier position on the path back to the start, or later one along the line ahead."""
        back, ahead = self.node.parent, self.node.variations[0] if self.node.variations else None
        while back is not None or ahead is not None:
            for candidate in (back, ahead):
                if candidate is not None and _fits(candidate.board(), placement, doubt, current):
                    return candidate
            back = back.parent if back is not None else None
            ahead = ahead.variations[0] if ahead is not None and ahead.variations else None
        return None


def _unclear(unsure) -> str:
    if not unsure:
        return ""
    names = ", ".join(chess.square_name(s) for s in sorted(unsure))
    return f"{names} {'is' if len(unsure) == 1 else 'are'} unclear on screen."


def _bitboard(squares) -> int:
    bb = 0
    for s in squares:
        bb |= chess.BB_SQUARES[s]
    return bb


def _difference(a: chess.BaseBoard, b: chess.BaseBoard) -> int:
    """Squares whose contents differ between two placements."""
    diff = (a.occupied_co[chess.WHITE] ^ b.occupied_co[chess.WHITE]) | (a.occupied_co[chess.BLACK] ^ b.occupied_co[chess.BLACK])
    for name in _PIECE_BITBOARDS:
        diff |= getattr(a, name) ^ getattr(b, name)
    return diff


def _leaves_traces(board: chess.Board, moves) -> bool:
    """Does every move start or end on a square that looks different afterwards?

    Unseen moves can only be worked out from what they changed. A piece that
    goes out and comes back leaves no sign of having moved, and allowing such
    detours lets a few moves explain nearly any reading.
    """
    probe = board.copy(stack=False)
    for move in moves:
        probe.push(move)
    changed = _difference(board, probe)
    return all((chess.BB_SQUARES[m.from_square] | chess.BB_SQUARES[m.to_square]) & changed for m in moves)


class _Doubt(NamedTuple):
    unsure: int  # bitboard of squares that couldn't be read
    half: int  # bitboard of squares read, but not confidently


def _fits(candidate: chess.BaseBoard, placement: chess.BaseBoard, doubt: _Doubt, current: chess.BaseBoard) -> bool:
    """Could the screen be showing `candidate`, reached from `current`?

    It must agree with the reading on every confident square, change no
    unsure square, and change a half-sure square only to the reading's best
    guess there.
    """
    vs_reading = _difference(candidate, placement)
    vs_current = _difference(candidate, current)
    return not (
        vs_reading & ~(doubt.unsure | doubt.half)
        or vs_current & doubt.unsure
        or vs_current & vs_reading & doubt.half
    )


def _ending(board: chess.Board, moves) -> str:
    """The position after playing `moves`: pieces, side to move, castling and en passant. Not the move
    clocks, which depend on the order and can't be seen on screen anyway."""
    probe = board.copy(stack=False)
    for move in moves:
        probe.push(move)
    return " ".join(probe.fen().split()[:4])


def _san_line(board: chess.Board, moves) -> list[str]:
    probe = board.copy(stack=False)
    out = []
    for move in moves:
        out.append(probe.san(move))
        probe.push(move)
    return out


def _where(node: chess.pgn.GameNode) -> str:
    if node.parent is None:
        return "the start of the game"
    board = node.parent.board()
    number = board.fullmove_number
    return f"after {number}{'.' if board.turn == chess.WHITE else '...'} {node.san()}"


def _describe_difference(current: chess.BaseBoard, placement: chess.BaseBoard) -> str:
    squares = [chess.square_name(s) for s in chess.scan_forward(_difference(current, placement))]
    shown = ", ".join(squares[:8]) + (", …" if len(squares) > 8 else "")
    return f"No legal move explains the board on screen (it differs on {shown})."


def _result(board: chess.Board) -> str:
    outcome = board.outcome()
    return outcome.result() if outcome else "*"
