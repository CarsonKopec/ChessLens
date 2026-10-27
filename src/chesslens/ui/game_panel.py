"""The followed game: move list, notices about unclear readings, and game controls (spec §6.4, §6.7 bottom)."""

from __future__ import annotations

import html

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

_NOTICE_STYLES = {
    "info": "color: palette(text);",
    "waiting": "color: #8a6d00;",
    "problem": "color: #b3261e; font-weight: bold;",
}


def move_list_html(moves: list[tuple[int, str, bool]]) -> str:
    """Numbered SAN ("1. e4 e5 2. Nf3", "23... Bc5 24. d4"), the current move highlighted.

    Each entry is (ply after the move, SAN, is current).
    """
    parts = []
    for i, (ply, san, current) in enumerate(moves):
        number, white_moved = (ply - 1) // 2 + 1, (ply - 1) % 2 == 0
        if white_moved:
            parts.append(f"{number}.")
        elif i == 0:
            parts.append(f"{number}...")
        text = html.escape(san)
        parts.append(f"<span style='background:#f2d04b; color:#000; font-weight:bold;'>&nbsp;{text}&nbsp;</span>" if current else text)
    return " ".join(parts) if parts else "<i>No moves yet.</i>"


class GamePanel(QWidget):
    undo_requested = Signal()
    new_game_requested = Signal()
    copy_pgn_requested = Signal()
    save_pgn_requested = Signal()
    use_screen_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.status = QLabel("Not following a game. Tick Follow the board to start.")
        self.status.setWordWrap(True)
        self.moves = QTextBrowser()
        self.moves.setMinimumHeight(90)
        self.moves.setAccessibleName("Moves")
        self.moves.setHtml(move_list_html([]))

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.use_screen = QPushButton("Use screen position")
        self.use_screen.setToolTip("Start following again from what the screen shows (check side to move and castling)")
        self.use_screen.clicked.connect(self.use_screen_requested)
        self.use_screen.hide()

        self.undo = QPushButton("&Undo move")
        self.undo.setToolTip("Take back the last move, e.g. if it was misread")
        self.undo.clicked.connect(self.undo_requested)
        self.new_game = QPushButton("&New game")
        self.new_game.setToolTip("Start over from the starting position, clearing this session's moves and analysis")
        self.new_game.clicked.connect(self.new_game_requested)
        copy = QPushButton("Copy PGN")
        copy.clicked.connect(self.copy_pgn_requested)
        save = QPushButton("Save PGN…")
        save.setToolTip("Save every game followed in this session")
        save.clicked.connect(self.save_pgn_requested)
        buttons = QHBoxLayout()
        for b in (self.undo, self.new_game, copy, save):
            buttons.addWidget(b)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        title = QLabel("<b>Game</b>")
        layout.addWidget(title)
        layout.addWidget(self.status)
        layout.addWidget(self.moves, 1)
        layout.addWidget(self.notice)
        layout.addWidget(self.use_screen)
        layout.addLayout(buttons)
        self.set_following(False)

    def set_following(self, following: bool) -> None:
        self.undo.setEnabled(following)
        self.new_game.setEnabled(following)
        if not following:
            self.show_notice(None)

    def show_moves(self, moves: list[tuple[int, str, bool]], status: str) -> None:
        self.moves.setHtml(move_list_html(moves))
        self.status.setText(status)
        bar = self.moves.verticalScrollBar()
        bar.setValue(bar.maximum())

    def show_notice(self, text: str | None, kind: str = "info", offer_screen: bool = False) -> None:
        self.notice.setVisible(bool(text))
        self.notice.setText(text or "")
        self.notice.setStyleSheet(_NOTICE_STYLES.get(kind, ""))
        self.use_screen.setVisible(offer_screen)
