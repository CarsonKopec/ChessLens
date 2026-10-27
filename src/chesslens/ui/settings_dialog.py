"""Privacy & data: what ChessLens keeps on this computer, and controls to change or delete it (spec §6.7, §11)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from chesslens.persistence.sqlite_store import SessionStore, sessions_path
from chesslens.user_settings import privacy_setting, save_user_settings, settings_path
from chesslens.vision.piece_set import PieceSet, list_piece_sets, pieces_dir


class PrivacyDialog(QDialog):
    pieces_deleted = Signal()  # a learned piece set was removed; reload
    settings_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Privacy & data")
        self.setMinimumWidth(520)

        intro = QLabel(
            "ChessLens works on this computer only and uploads nothing. The mirrored screen is processed "
            "in memory while capturing and never recorded. Only what is listed here is kept."
        )
        intro.setWordWrap(True)

        # Learned pieces
        self.piece_list = QListWidget()
        self.piece_list.setMaximumHeight(90)
        delete_pieces = QPushButton("Delete selected")
        delete_pieces.clicked.connect(self._delete_piece_set)
        open_pieces = QPushButton("Open folder")
        open_pieces.clicked.connect(lambda: _open_folder(pieces_dir()))
        self.keep_learning = QCheckBox("Keep learning while following a game")
        self.keep_learning.setToolTip("Add a square the reading was unsure about once the game confirms what's on it")
        self.keep_learning.setChecked(bool(privacy_setting("keep_learning")))
        self.keep_learning.toggled.connect(lambda on: self._save(keep_learning=on))
        pieces_row = QHBoxLayout()
        pieces_row.addWidget(delete_pieces)
        pieces_row.addWidget(open_pieces)
        pieces_row.addStretch(1)
        pieces_box = QGroupBox("Learned pieces: small images of board squares")
        pieces_layout = QVBoxLayout(pieces_box)
        pieces_layout.addWidget(self.piece_list)
        pieces_layout.addLayout(pieces_row)
        pieces_layout.addWidget(self.keep_learning)

        # Game history
        self.save_games = QCheckBox("Keep a history of followed games on this computer")
        self.save_games.setToolTip("Moves with their time, position and reading confidence, and each game's PGN. No images.")
        self.save_games.setChecked(bool(privacy_setting("save_games")))
        self.save_games.toggled.connect(lambda on: self._save(save_games=on))
        self.retention = QSpinBox()
        self.retention.setRange(1, 3650)
        self.retention.setSuffix(" days")
        self.retention.setValue(int(privacy_setting("retention_days")))
        self.retention.valueChanged.connect(lambda days: self._save(retention_days=days))
        self.history_label = QLabel()
        delete_history = QPushButton("Delete all saved games")
        delete_history.clicked.connect(self._delete_history)
        retention_row = QHBoxLayout()
        retention_row.addWidget(QLabel("Delete games older than"))
        retention_row.addWidget(self.retention)
        retention_row.addStretch(1)
        history_row = QHBoxLayout()
        history_row.addWidget(self.history_label, 1)
        history_row.addWidget(delete_history)
        history_box = QGroupBox("Game history (off unless you turn it on)")
        history_layout = QVBoxLayout(history_box)
        history_layout.addWidget(self.save_games)
        history_layout.addLayout(retention_row)
        history_layout.addLayout(history_row)

        other = QLabel(
            "Labeled boards and PGN files you save go only where you choose. "
            f"Settings (such as the Stockfish location) are in {settings_path()}."
        )
        other.setWordWrap(True)
        other.setStyleSheet("color: #808080;")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(pieces_box)
        layout.addWidget(history_box)
        layout.addWidget(other)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        self.piece_list.clear()
        for name in list_piece_sets():
            try:
                count = PieceSet.load(name).example_count
                self.piece_list.addItem(f"{name}  ({count} examples)")
            except (OSError, ValueError):
                self.piece_list.addItem(f"{name}  (unreadable)")
            self.piece_list.item(self.piece_list.count() - 1).setData(Qt.ItemDataRole.UserRole, name)
        if not self.piece_list.count():
            self.piece_list.addItem("None learned yet.")
        if sessions_path().exists():
            store = SessionStore()
            games, moves = store.counts()
            store.close()
            self.history_label.setText(f"{games} game{'' if games == 1 else 's'}, {moves} moves saved.")
        else:
            self.history_label.setText("Nothing saved.")

    def _save(self, **values) -> None:
        save_user_settings(**values)
        self.settings_changed.emit()

    def _delete_piece_set(self) -> None:
        item = self.piece_list.currentItem()
        name = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not name:
            return
        if QMessageBox.question(self, "Delete learned pieces", f"Delete the learned piece set '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        for path in pieces_dir().glob("*.npz"):
            if path.stem == name:
                path.unlink()
        self.refresh()
        self.pieces_deleted.emit()

    def _delete_history(self) -> None:
        if not sessions_path().exists():
            return
        if QMessageBox.question(self, "Delete game history", "Delete every saved game and move?") != QMessageBox.StandardButton.Yes:
            return
        store = SessionStore()
        store.delete_all()
        store.close()
        self.refresh()


def _open_folder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
