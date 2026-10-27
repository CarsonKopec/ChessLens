"""Engine output: evaluation bar, candidate lines and search status (spec §6.7 right panel)."""

from __future__ import annotations

import html

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import chess

from chesslens.domain.models import Orientation
from chesslens.engine.coordinator import AnalysisState, AnalysisStatus
from chesslens.engine.results import AnalysisResult, EngineLine, bar_fraction
from chesslens.engine.stockfish_service import INSTALL_HINT, EngineStatus
from chesslens.explain.rule_explanations import describe_eval, explain

FAIR_PLAY_NOTE = "Fair play: use engine help only where it's allowed: study, puzzles, games against the computer, or reviewing finished games."


class EvalBar(QWidget):
    """Vertical bar, White's share drawn from White's side of the board, with the score written on it."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(30)
        self.setMinimumHeight(200)
        self._line: EngineLine | None = None
        self._orientation = Orientation.WHITE_BOTTOM
        self._stale = True

    def sizeHint(self) -> QSize:
        return QSize(30, 400)

    def set_line(self, line: EngineLine | None, stale: bool = False) -> None:
        self._line = line
        self._stale = stale or line is None
        self.setAccessibleDescription(f"Evaluation {line.score_text}" if line else "No evaluation")
        self.update()

    def set_stale(self) -> None:
        self._stale = True
        self.update()

    def set_orientation(self, orientation: Orientation) -> None:
        self._orientation = orientation
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        white_share = bar_fraction(self._line)
        white_height = r.height() * white_share
        white_at_bottom = self._orientation is Orientation.WHITE_BOTTOM
        p.fillRect(r, QColor(64, 61, 57))
        white_rect = QRectF(r.x(), r.bottom() - white_height if white_at_bottom else r.y(), r.width(), white_height)
        p.fillRect(white_rect, QColor(240, 240, 236))
        if self._stale:
            p.fillRect(r, QColor(128, 128, 128, 110))
        p.setPen(QColor(120, 120, 120))
        p.drawRect(r)
        if self._line is not None:
            text = self._line.score_text.lstrip("+")
            white_ahead = white_share >= 0.5
            font = QFont(self.font())
            font.setPixelSize(10)
            font.setBold(True)
            p.setFont(font)
            # Write the score at the leading side's end, in that side's contrasting colour.
            at_bottom = white_ahead == white_at_bottom
            box = QRectF(r.x(), r.bottom() - 16 if at_bottom else r.y() + 2, r.width(), 14)
            p.setPen(QColor(30, 30, 30) if white_ahead else QColor(235, 235, 235))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


class AnalysisPanel(QWidget):
    enabled_toggled = Signal(bool)
    think_time_changed = Signal(float)
    locate_requested = Signal()

    def __init__(self, think_time_s: float, multipv: int) -> None:
        super().__init__()
        self._multipv = multipv

        self.toggle = QCheckBox("Engine &analysis")
        self.toggle.setToolTip("Analyse the position in the editor with Stockfish")
        self.toggle.toggled.connect(self.enabled_toggled)
        self._think = QDoubleSpinBox()
        self._think.setRange(0.1, 30.0)
        self._think.setSingleStep(0.5)
        self._think.setDecimals(1)
        self._think.setSuffix(" s")
        self._think.setValue(think_time_s)
        self._think.setToolTip("Search time per position")
        self._think.valueChanged.connect(self.think_time_changed)
        top = QHBoxLayout()
        top.addWidget(self.toggle)
        top.addStretch(1)
        top.addWidget(QLabel("Think:"))
        top.addWidget(self._think)

        note = QLabel(FAIR_PLAY_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #808080;")

        self.eval_bar = EvalBar()
        self._line_labels: list[QLabel] = []
        lines_box = QVBoxLayout()
        for _ in range(multipv):
            label = QLabel()
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setFrameShape(QFrame.Shape.StyledPanel)
            label.setMinimumHeight(44)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self._line_labels.append(label)
            lines_box.addWidget(label)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lines_box.addWidget(self.status)
        self.explain_box = QCheckBox("E&xplain the best move")
        self.explain_box.setToolTip("Plain-language notes from rules of thumb; they describe the move, not the engine's reasoning")
        self.explain_box.setChecked(True)
        self.explain_box.toggled.connect(lambda: self._show_explanation())
        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        self.explanation.setTextFormat(Qt.TextFormat.RichText)
        lines_box.addWidget(self.explain_box)
        lines_box.addWidget(self.explanation)
        self._last_result: AnalysisResult | None = None

        self._install = QLabel(
            "<b>Stockfish isn't installed.</b><br>Install it by running this in a terminal, then turn analysis "
            f"off and on again:<br><code>{INSTALL_HINT}</code><br>Or download it from "
            '<a href="https://stockfishchess.org/download/">stockfishchess.org/download</a> and use Locate Stockfish.'
        )
        self._install.setWordWrap(True)
        self._install.setOpenExternalLinks(True)
        self._install.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._install.hide()
        lines_box.addWidget(self._install)
        locate = QPushButton("Locate Stockfish…")
        locate.setToolTip("Choose the Stockfish .exe yourself")
        locate.clicked.connect(self.locate_requested)
        self.engine_label = QLabel()
        self.engine_label.setStyleSheet("color: #808080;")
        foot = QHBoxLayout()
        foot.addWidget(self.engine_label, 1)
        foot.addWidget(locate)
        lines_box.addStretch(1)
        lines_box.addLayout(foot)

        body = QHBoxLayout()
        body.addWidget(self.eval_bar)
        body.addLayout(lines_box, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(note)
        layout.addLayout(body, 1)
        self.show_status(AnalysisStatus(AnalysisState.OFF))

    @property
    def think_time_s(self) -> float:
        return self._think.value()

    def set_orientation(self, orientation: Orientation) -> None:
        self.eval_bar.set_orientation(orientation)

    def show_result(self, result: AnalysisResult) -> None:
        for i, label in enumerate(self._line_labels):
            line = result.lines[i] if i < len(result.lines) else None
            label.setText(self._line_html(line) if line else "")
            label.setStyleSheet("")
        self.eval_bar.set_line(result.best)
        state = "Done" if result.final else "Thinking…"
        nodes = f"{result.nodes / 1e6:.1f}M" if result.nodes >= 1e6 else f"{result.nodes / 1e3:.0f}k"
        self.status.setText(f"{state} Depth {result.depth}, {nodes} nodes, {result.analysis_ms / 1000:.1f} s.")
        self.status.setStyleSheet("")
        self._last_result = result
        self._show_explanation()

    def _show_explanation(self) -> None:
        """Rule-of-thumb notes on the best move, kept apart from the engine's own output (spec §6.5, §6.6)."""
        result = self._last_result
        if not self.explain_box.isChecked() or result is None or result.best is None:
            self.explanation.setText("")
            return
        best = result.best
        notes = explain(chess.Board(result.fen), best)
        why = " ".join(html.escape(n) for n in notes) or "No simple pattern; see the engine's line."
        self.explanation.setText(
            f"<b>{html.escape(best.san)}:</b> {why} {html.escape(describe_eval(best))}"
            "<br><span style='color:#808080;'>Rules of thumb, not the engine's reasoning.</span>"
        )

    def show_status(self, status: AnalysisStatus) -> None:
        """Explain why nothing (new) is shown; old lines stay visible but greyed out."""
        self._install.setVisible(status.state is AnalysisState.NO_ENGINE)
        if status.state is AnalysisState.THINKING:
            self._dim_lines()
            self.status.setText("Thinking…")
            self.status.setStyleSheet("")
            return
        self._dim_lines()
        self.eval_bar.set_stale()
        texts = {
            AnalysisState.OFF: "Analysis is off.",
            AnalysisState.NO_ENGINE: "No engine.",
            AnalysisState.ILLEGAL: f"Not analysed: {status.detail} Fix the position first.",
            AnalysisState.GAME_OVER: f"{status.detail} Nothing to analyse.",
        }
        self.status.setText(texts[status.state])
        self.status.setStyleSheet("color: #b3261e;" if status.state is AnalysisState.ILLEGAL else "")

    def show_engine_status(self, status: EngineStatus) -> None:
        if status.state == "error":
            self.status.setText(f"✖ Engine problem: {status.detail}")
            self.status.setStyleSheet("color: #b3261e;")
        elif status.state in ("ready", "idle") and status.detail:
            self.engine_label.setText(status.detail)
        elif status.state == "starting":
            self.engine_label.setText("Starting engine…")

    def clear_lines(self) -> None:
        """Remove every trace of the last analysis: lines, evaluation bar and explanation."""
        for label in self._line_labels:
            label.setText("")
            label.setStyleSheet("")
        self.eval_bar.set_line(None)
        self._last_result = None
        self.explanation.setText("")

    def _dim_lines(self) -> None:
        for label in self._line_labels:
            label.setStyleSheet("color: #9a9a9a;")
        # Greyed lines belong to an older position; their explanation would mislead, so drop it.
        self._last_result = None
        self.explanation.setText("")

    @staticmethod
    def _line_html(line: EngineLine) -> str:
        return (
            f"<b>{html.escape(line.score_text)}</b>&nbsp;&nbsp;<b>{html.escape(line.san)}</b><br>"
            f"<span style='font-family: Consolas, monospace;'>{html.escape(line.pv_san)}</span>"
        )
