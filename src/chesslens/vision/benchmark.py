"""Recognition benchmark over labeled boards saved from the dashboard (spec §6.3, §13).

A labeled board is the straightened PNG written by "Save labeled board…" plus
its JSON (FEN and orientation). Keep training and test boards from different
games or sessions; near-duplicate frames would overstate accuracy (spec §6.3).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import chess
import cv2
import numpy as np

from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import screen_cell_to_square
from chesslens.vision.piece_classifier import CONFIDENT, read_board
from chesslens.vision.piece_set import PieceSet

CONFIDENCE_BUCKETS = ((0.0, 0.5), (0.5, 0.9), (0.9, 0.99), (0.99, 1.01))


@dataclass(frozen=True)
class LabeledBoard:
    path: Path
    image: np.ndarray
    placement: chess.BaseBoard
    orientation: Orientation


@dataclass
class BenchmarkReport:
    boards: int = 0
    squares: int = 0
    correct_squares: int = 0
    exact_boards: int = 0
    accepted: int = 0  # readings with no problems, which would go into the editor
    accepted_wrong: list[str] = field(default_factory=list)  # the costly case: trusted but wrong
    buckets: dict[tuple[float, float], list[int]] = field(default_factory=lambda: {b: [0, 0] for b in CONFIDENCE_BUCKETS})
    read_ms: list[float] = field(default_factory=list)

    def lines(self) -> list[str]:
        if not self.boards:
            return ["No labeled boards found."]
        out = [
            f"Boards: {self.boards}   squares: {self.squares}",
            f"Squares right: {self.correct_squares}/{self.squares} ({self.correct_squares / self.squares:.2%})",
            f"Boards read exactly: {self.exact_boards}/{self.boards}",
            f"Readings accepted (valid and sure): {self.accepted}/{self.boards}; accepted but wrong: {len(self.accepted_wrong)}",
        ]
        out += [f"  wrong: {name}" for name in self.accepted_wrong]
        out.append("By confidence:   squares   right")
        for (lo, hi), (count, right) in self.buckets.items():
            label = f"{lo:.2f}-{min(hi, 1):.2f}"
            out.append(f"  {label:>11}  {count:>9}   {right / count:.2%}" if count else f"  {label:>11}  {count:>9}   -")
        out.append(f"Read time: median {np.median(self.read_ms):.0f} ms, max {max(self.read_ms):.0f} ms")
        return out


def load_labeled_boards(folder: Path) -> list[LabeledBoard]:
    boards = []
    for meta_path in sorted(folder.glob("*.json")):
        image_path = meta_path.with_suffix(".png")
        if not image_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
        boards.append(
            LabeledBoard(image_path, image, chess.BaseBoard(meta["fen"].split()[0]), Orientation(meta["orientation"]))
        )
    return boards


def train(name: str, boards: list[LabeledBoard]) -> PieceSet:
    pieces = PieceSet(name)
    for b in boards:
        pieces.learn(b.image, b.placement, b.orientation)
    return pieces


def evaluate(pieces: PieceSet, boards: list[LabeledBoard]) -> BenchmarkReport:
    report = BenchmarkReport()
    for b in boards:
        start = time.perf_counter()
        obs = read_board(b.image, pieces, b.orientation)
        report.read_ms.append((time.perf_counter() - start) * 1000)
        right = np.zeros((8, 8), bool)
        for r in range(8):
            for c in range(8):
                truth = b.placement.piece_at(screen_cell_to_square(r, c, b.orientation))
                got = obs.symbol_at(r, c)
                right[r, c] = got == (truth.symbol() if truth else ".")
        report.boards += 1
        report.squares += 64
        report.correct_squares += int(right.sum())
        report.exact_boards += int(right.all())
        if not obs.problems(CONFIDENT):
            report.accepted += 1
            if not right.all():
                report.accepted_wrong.append(b.path.name)
        for (lo, hi), tally in report.buckets.items():
            in_bucket = (obs.confidence >= lo) & (obs.confidence < hi)
            tally[0] += int(in_bucket.sum())
            tally[1] += int((in_bucket & right).sum())
    return report
