"""A learned piece set: example images of each piece, taken from the user's own captures (spec §6.3).

Templates come from the screen of the person using ChessLens and are stored
only on their computer (%APPDATA%\\ChessLens\\pieces), never bundled: chess
apps' piece artwork is copyrighted (spec §7).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import chess
import numpy as np

from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import screen_cell_to_square
from chesslens.vision.calibration import piece_colours
from chesslens.vision.square_features import NORM, coverage, normalize_board

PIECE_SYMBOLS = ("P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k")
EMPTY = "."
MAX_EXAMPLES = 12  # per class
DUPLICATE_DISTANCE = 0.0008  # mean squared difference (0..1) below which an example adds nothing
# How much of a normalised square real examples cover: pieces 0.24 (pawns) to 0.53, empty squares
# 0 to 0.2 (wood grain). Outside these limits an example was learned from a position that didn't
# match the screen, e.g. a blank square taught as a bishop, which then made every empty square a
# 50/50 call.
MIN_PIECE_COVERAGE = 0.1
MAX_EMPTY_COVERAGE = 0.3


def pieces_dir() -> Path:
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home()) / "ChessLens" / "pieces"


def list_piece_sets() -> list[str]:
    folder = pieces_dir()
    return sorted(p.stem for p in folder.glob("*.npz")) if folder.is_dir() else []


def occupancy_mismatches(board_image: np.ndarray, placement: chess.BaseBoard, orientation: Orientation) -> list[chess.Square]:
    """Squares where a rough look at the screen disagrees with `placement` about whether a piece is
    there and which colour it is: a guard against learning from a position that doesn't match."""
    census = piece_colours(board_image)
    out = []
    for r in range(8):
        for c in range(8):
            square = screen_cell_to_square(r, c, orientation)
            piece = placement.piece_at(square)
            expected = 0 if piece is None else (1 if piece.color == chess.WHITE else -1)
            if census[r, c] != expected:
                out.append(square)
    return out


def plausible_example(symbol: str, image: np.ndarray) -> bool:
    """Could this normalised square really show `symbol`: some piece for a piece, mostly board for empty?"""
    share = coverage(image)
    return share <= MAX_EMPTY_COVERAGE if symbol == EMPTY else share >= MIN_PIECE_COVERAGE


def _file_name(name: str) -> str:
    return re.sub(r"[^\w .-]", "_", name).strip() or "pieces"


@dataclass
class PieceSet:
    name: str
    examples: dict[str, list[np.ndarray]] = field(default_factory=dict)  # symbol (or ".") -> NORM×NORM×3 images

    @property
    def missing(self) -> list[str]:
        """Piece symbols with no example yet; those pieces can't be recognised."""
        return [s for s in PIECE_SYMBOLS if not self.examples.get(s)]

    @property
    def example_count(self) -> int:
        return sum(len(v) for v in self.examples.values())

    def learn(self, board_image: np.ndarray, placement: chess.BaseBoard, orientation: Orientation) -> int:
        """Add examples from a straightened board whose position is known. Returns how many were new."""
        normalized, _ = normalize_board(board_image)
        added = 0
        for r in range(8):
            for c in range(8):
                piece = placement.piece_at(screen_cell_to_square(r, c, orientation))
                symbol = piece.symbol() if piece else EMPTY
                added += self._add(symbol, normalized[r, c])
        return added

    def add_examples(self, examples: Iterable[tuple[str, np.ndarray]]) -> int:
        """Add normalised square images whose contents are known (symbol or "."). Returns how many were new."""
        return sum(self._add(symbol, image) for symbol, image in examples)

    def copy(self) -> PieceSet:
        return PieceSet(self.name, {symbol: list(images) for symbol, images in self.examples.items()})

    def _add(self, symbol: str, image: np.ndarray) -> bool:
        if not plausible_example(symbol, image):
            return False
        bucket = self.examples.setdefault(symbol, [])
        sample = image.astype(np.float32) / 255
        distances = [float(np.mean((sample - e.astype(np.float32) / 255) ** 2)) for e in bucket]
        if distances and min(distances) < DUPLICATE_DISTANCE:
            return False
        if len(bucket) < MAX_EXAMPLES:
            bucket.append(image.copy())
        else:
            # Full: replace the most similar example, so the set keeps its variety.
            bucket[int(np.argmin(distances))] = image.copy()
        return True

    def save(self, folder: Path | None = None) -> Path:
        folder = folder or pieces_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_file_name(self.name)}.npz"
        arrays = {_key(s): np.stack(v) for s, v in self.examples.items() if v}
        np.savez_compressed(path, **arrays)
        return path

    @classmethod
    def load(cls, name: str, folder: Path | None = None) -> PieceSet:
        path = (folder or pieces_dir()) / f"{_file_name(name)}.npz"
        with np.load(path) as data:
            examples = {_symbol(k): [a for a in data[k]] for k in data.files}
        for images in examples.values():
            if images and images[0].shape != (NORM, NORM, 3):
                raise ValueError(f"{path} was made by a different ChessLens version; learn the pieces again")
        # Sets learned before examples were checked can hold impossible ones; leave them out (they are
        # gone from the file at the next save).
        examples = {s: [a for a in images if plausible_example(s, a)] for s, images in examples.items()}
        return cls(name=name, examples=examples)


# Readable array names in the .npz ("white_n" rather than "N", "empty" rather than ".").
def _key(symbol: str) -> str:
    if symbol == EMPTY:
        return "empty"
    return ("white_" if symbol.isupper() else "black_") + symbol.lower()


def _symbol(key: str) -> str:
    if key == "empty":
        return EMPTY
    colour, letter = key.split("_")
    return letter.upper() if colour == "white" else letter
