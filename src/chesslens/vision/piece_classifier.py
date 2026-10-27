"""Read every square of a straightened board against a learned piece set (spec §6.3).

Each square is compared with every example of every class (12 pieces and
empty), allowing a pixel of misalignment, and the distances become a
probability distribution per square rather than one hard answer.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess
import numpy as np

from chesslens.chess.validations import placement_problems
from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import screen_cell_to_square
from chesslens.vision.piece_set import EMPTY, PIECE_SYMBOLS, PieceSet
from chesslens.vision.square_features import BACKGROUND, NORM, board_visible, empty_square, normalize_board

CLASSES = (EMPTY, *PIECE_SYMBOLS)
# Distances are mean squared differences on a 0..1 scale. On real Chess.com captures a square
# matches its own class at about 0.000-0.006 and the next class at least 0.003 further away.
TEMPERATURE = 0.0008
# A square this far from every example looks like none of them (an overlay, a promotion picker,
# an unlearned piece set): "unrecognised" competes for the probability, so confidence drops.
UNRECOGNISED_DISTANCE = 0.012
CONFIDENT = 0.9  # a square below this is reported as uncertain
# Below CONFIDENT but at least this: "half-sure". The best guess is plausible (e.g. a king read 50/50
# against a knight), so game tracking may accept it when exactly one legal move fits. Anything lower
# is unreadable: an overlay, a menu, or a piece the set hasn't learned.
HALF_SURE = 0.4
NO_BOARD = "No chessboard is visible where the board was (phone disconnected, another screen open, or something covering it)."
_SHIFT = 1  # px of misalignment tolerated, in normalised pixels


@dataclass(frozen=True, eq=False)
class BoardObservation:
    """One reading of the board: class probabilities per screen cell (rows top to bottom)."""

    probabilities: np.ndarray  # (8, 8, len(CLASSES)); may sum to less than 1 (the rest is "unrecognised")
    orientation: Orientation
    tinted: np.ndarray  # (8, 8) squares the app highlighted, e.g. the last move
    calibration_version: int = 0
    frame_seq: int = 0
    unlearned: tuple[str, ...] = ()  # pieces the set had no example of; such a piece may be misread as another
    squares: np.ndarray | None = None  # (8, 8, NORM, NORM, 3) the normalised square images that were read
    board_visible: bool = True  # False when the region no longer looks like a chessboard at all

    @property
    def best(self) -> np.ndarray:
        return self.probabilities.argmax(axis=2)

    @property
    def confidence(self) -> np.ndarray:
        return self.probabilities.max(axis=2)

    def symbol_at(self, row: int, col: int) -> str:
        return CLASSES[int(self.best[row, col])]

    def placement(self) -> chess.BaseBoard:
        board = chess.BaseBoard(None)
        for r in range(8):
            for c in range(8):
                symbol = self.symbol_at(r, c)
                if symbol != EMPTY:
                    board.set_piece_at(screen_cell_to_square(r, c, self.orientation), chess.Piece.from_symbol(symbol))
        return board

    def uncertain_cells(self, threshold: float = CONFIDENT) -> list[tuple[int, int]]:
        return [(int(r), int(c)) for r, c in np.argwhere(self.confidence < threshold)]

    def placement_problems(self) -> list[str]:
        """Why this reading can't be right as a whole: no board visible, an incomplete piece set, or an
        impossible placement."""
        if not self.board_visible:
            return [NO_BOARD]
        problems = placement_problems(self.placement())
        if self.unlearned:
            problems.insert(0, f"The piece set hasn't learned {' '.join(self.unlearned)} yet.")
        return problems

    def uncertain_squares(self, threshold: float = CONFIDENT) -> list[chess.Square]:
        return [screen_cell_to_square(r, c, self.orientation) for r, c in self.uncertain_cells(threshold)]

    def unreadable_squares(self) -> frozenset[chess.Square]:
        return frozenset(self.uncertain_squares(HALF_SURE))

    def half_sure_squares(self) -> frozenset[chess.Square]:
        return frozenset(self.uncertain_squares(CONFIDENT)) - self.unreadable_squares()

    def problems(self, threshold: float = CONFIDENT) -> list[str]:
        """Every reason not to trust this reading automatically: placement problems plus unsure squares."""
        problems = self.placement_problems()
        uncertain = self.uncertain_squares(threshold)
        if uncertain:
            problems.append(f"Not sure about {', '.join(chess.square_name(s) for s in uncertain)}.")
        return problems

    def last_move_squares(self) -> frozenset[chess.Square]:
        """The two squares of the app's last-move highlight, when exactly two are marked."""
        marked = [screen_cell_to_square(int(r), int(c), self.orientation) for r, c in np.argwhere(self.tinted)]
        return frozenset(marked) if len(marked) == 2 else frozenset()

    def side_to_move_hint(self) -> chess.Color | None:
        """The app's last-move highlight marks two squares and the moved piece stands on one of them;
        the other side is to move. None when that pattern isn't there."""
        marked = [tuple(rc) for rc in np.argwhere(self.tinted)]
        if len(marked) != 2:
            return None
        movers = [self.symbol_at(r, c) for r, c in marked if self.symbol_at(r, c) != EMPTY]
        if len(movers) != 1:
            return None
        return chess.BLACK if movers[0].isupper() else chess.WHITE


def read_board(
    board_image: np.ndarray,
    pieces: PieceSet,
    orientation: Orientation,
    calibration_version: int = 0,
    frame_seq: int = 0,
) -> BoardObservation:
    normalized, tinted = normalize_board(board_image)
    distances = _class_distances(normalized.reshape(64, NORM, NORM, 3), pieces)
    with_unrecognised = np.concatenate([distances, np.full((64, 1), UNRECOGNISED_DISTANCE, np.float32)], axis=1)
    logits = -with_unrecognised / TEMPERATURE
    logits -= logits.max(axis=1, keepdims=True)
    weights = np.exp(logits)
    probabilities = weights[:, :-1] / weights.sum(axis=1, keepdims=True)
    return BoardObservation(
        probabilities.reshape(8, 8, -1),
        orientation,
        tinted,
        calibration_version,
        frame_seq,
        tuple(pieces.missing),
        normalized,
        board_visible(board_image),
    )


def _class_distances(squares: np.ndarray, pieces: PieceSet) -> np.ndarray:
    """(64, len(CLASSES)) smallest mean squared difference to any example of each class, over small shifts."""
    labels, templates = [], []
    for k, symbol in enumerate(CLASSES):
        examples = list(pieces.examples.get(symbol, []))
        if symbol == EMPTY:
            examples.append(empty_square())
        for image in examples:
            labels.append(k)
            templates.append(image)
    t = np.stack(templates).reshape(len(templates), -1).astype(np.float32) / 255  # (T, D)

    pad = _SHIFT
    padded = np.pad(squares, ((0, 0), (pad, pad), (pad, pad), (0, 0)), constant_values=BACKGROUND)
    shifted = np.stack(
        [padded[:, dy : dy + NORM, dx : dx + NORM] for dy in range(2 * pad + 1) for dx in range(2 * pad + 1)], axis=1
    )  # (64, S, NORM, NORM, 3)
    s = shifted.reshape(64 * shifted.shape[1], -1).astype(np.float32) / 255  # (64·S, D)

    # |s - t|² = |s|² + |t|² - 2 s·t, averaged over D.
    d = ((s * s).sum(1)[:, None] + (t * t).sum(1)[None, :] - 2 * s @ t.T) / s.shape[1]
    d = d.reshape(64, -1, len(templates)).min(axis=1)  # best shift: (64, T)

    out = np.full((64, len(CLASSES)), np.inf, np.float32)
    labels = np.array(labels)
    for k in range(len(CLASSES)):
        if (labels == k).any():
            out[:, k] = d[:, labels == k].min(axis=1)
    return np.maximum(out, 0)
