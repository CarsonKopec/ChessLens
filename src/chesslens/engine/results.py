"""Engine output as plain values, scored from White's point of view (spec §6.5)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import chess
import chess.engine

PV_DISPLAY_PLIES = 12


@dataclass(frozen=True)
class EngineLine:
    rank: int  # 1 = best
    uci: str
    san: str
    score_cp_white: int | None  # centipawns, + favours White; None when a mate is found
    mate_white: int | None  # moves to mate: + White mates, - Black mates; None for a numeric eval
    pv_uci: tuple[str, ...]
    pv_san: str  # numbered SAN, e.g. "12... Nc6 13. Bb5 a6"

    @property
    def score_text(self) -> str:
        """'+0.32', '-1.50', '#3' (White mates in 3) or '#-2' (Black mates in 2). Not a win probability."""
        if self.mate_white is not None:
            return f"#{self.mate_white}"
        return f"{(self.score_cp_white or 0) / 100:+.2f}"


def bar_fraction(line: EngineLine | None) -> float:
    """Share of an evaluation bar filled for White: a visual squash of the evaluation, not a win probability."""
    if line is None:
        return 0.5
    if line.mate_white is not None:
        return 1.0 if line.mate_white > 0 else 0.0
    cp = line.score_cp_white or 0
    return min(0.97, max(0.03, 1 / (1 + math.exp(-0.004 * cp))))


@dataclass(frozen=True)
class AnalysisResult:
    position_version: int
    fen: str
    depth: int
    nodes: int
    analysis_ms: int
    lines: tuple[EngineLine, ...]
    final: bool  # False while the engine is still searching
    engine_name: str = ""
    settings: dict = field(default_factory=dict, compare=False)

    @property
    def best(self) -> EngineLine | None:
        return self.lines[0] if self.lines else None

    def for_version(self, version: int) -> AnalysisResult:
        """The same analysis re-tagged for another version of an identical position (cache hit)."""
        return replace(self, position_version=version)

    def to_dict(self) -> dict:
        """The spec §6.5 JSON schema."""
        return {
            "position_version": self.position_version,
            "fen": self.fen,
            "analysis_ms": self.analysis_ms,
            "depth": self.depth,
            "lines": [
                {
                    "rank": line.rank,
                    "uci": line.uci,
                    "san": line.san,
                    "score_cp_white": line.score_cp_white,
                    "mate": line.mate_white,
                    "pv_uci": list(line.pv_uci),
                }
                for line in self.lines
            ],
        }


def build_result(
    version: int,
    board: chess.Board,
    infos: list[chess.engine.InfoDict],
    final: bool,
    elapsed_ms: int,
    engine_name: str = "",
    settings: dict | None = None,
) -> AnalysisResult:
    """Turn python-chess MultiPV info dicts into an AnalysisResult. Lines without a PV yet are skipped."""
    lines = []
    for info in infos:
        pv = info.get("pv") or []
        score = info.get("score")
        if not pv or score is None:
            continue
        white = score.white()
        lines.append(
            EngineLine(
                rank=len(lines) + 1,
                uci=pv[0].uci(),
                san=board.san(pv[0]),
                score_cp_white=None if white.is_mate() else white.score(),
                mate_white=white.mate() if white.is_mate() else None,
                pv_uci=tuple(m.uci() for m in pv),
                pv_san=board.variation_san(pv[:PV_DISPLAY_PLIES]),
            )
        )
    head = infos[0] if infos else {}
    return AnalysisResult(
        position_version=version,
        fen=board.fen(),
        depth=int(head.get("depth", 0)),
        nodes=int(head.get("nodes", 0)),
        analysis_ms=elapsed_ms,
        lines=tuple(lines),
        final=final,
        engine_name=engine_name,
        settings=dict(settings or {}),
    )
