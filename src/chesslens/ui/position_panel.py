"""Position editor: digital board, piece palette, game-state fields and FEN (spec Phase 1, FR-10)."""

from __future__ import annotations

import chess
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from chesslens.chess.fen_pgn import export_fen, parse_fen
from chesslens.chess.validations import en_passant_candidates, position_problems, possible_castling_rights
from chesslens.domain.models import Orientation
from chesslens.ui.board_widget import BoardEditor, PiecePalette

_CASTLING = [
    (chess.BB_H1, "White O-O"),
    (chess.BB_A1, "White O-O-O"),
    (chess.BB_H8, "Black O-O"),
    (chess.BB_A8, "Black O-O-O"),
]


class PositionPanel(QWidget):
    """Holds the position being corrected. Every change emits position_changed(fen); changes the user
    made by hand (not ones pushed in by board reading or game tracking) also emit edited_by_user."""

    position_changed = Signal(str)
    edited_by_user = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._board = chess.Board()

        self.editor = BoardEditor()
        self.editor.square_clicked.connect(self.apply_tool)
        self.editor.square_typed.connect(self.set_piece)
        self.palette = PiecePalette()

        start_btn = QPushButton("Start position")
        start_btn.clicked.connect(lambda: self._replace(chess.Board()))
        clear_btn = QPushButton("Empty board")
        clear_btn.clicked.connect(lambda: self._replace(chess.Board(None)))
        reset_row = QHBoxLayout()
        reset_row.addWidget(start_btn)
        reset_row.addWidget(clear_btn)
        reset_row.addStretch(1)

        self._white_to_move = QRadioButton("White")
        self._black_to_move = QRadioButton("Black")
        side_group = QButtonGroup(self)
        side_group.addButton(self._white_to_move)
        side_group.addButton(self._black_to_move)
        self._white_to_move.toggled.connect(self._on_side_toggled)
        self._castling_boxes: dict[int, QCheckBox] = {}
        for mask, label in _CASTLING:
            box = QCheckBox(label)
            box.toggled.connect(self._on_castling_toggled)
            self._castling_boxes[mask] = box
        self._ep_combo = QComboBox()
        self._ep_combo.currentIndexChanged.connect(self._on_ep_changed)

        state = QGridLayout()
        state.addWidget(QLabel("To move:"), 0, 0)
        state.addWidget(self._white_to_move, 0, 1)
        state.addWidget(self._black_to_move, 0, 2)
        state.addWidget(QLabel("Castling:"), 1, 0)
        for i, (mask, _) in enumerate(_CASTLING):
            state.addWidget(self._castling_boxes[mask], 1 + i // 2, 1 + i % 2)
        state.addWidget(QLabel("En passant:"), 3, 0)
        state.addWidget(self._ep_combo, 3, 1)
        hint = QLabel("These can't be read from a screenshot (spec §6.4), so check them yourself.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #808080;")
        state.addWidget(hint, 4, 0, 1, 3)
        state_box = QGroupBox("Game state")
        state_box.setLayout(state)

        self._fen_edit = QLineEdit()
        self._fen_edit.setAccessibleName("FEN")
        self._fen_edit.setToolTip("Paste a FEN and press Enter to load it")
        self._fen_edit.returnPressed.connect(self._on_fen_entered)
        copy_btn = QPushButton("Copy FEN")
        copy_btn.clicked.connect(self._copy_fen)
        fen_row = QHBoxLayout()
        fen_row.addWidget(QLabel("FEN:"))
        fen_row.addWidget(self._fen_edit, 1)
        fen_row.addWidget(copy_btn)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.palette)
        layout.addLayout(reset_row)
        layout.addWidget(state_box)
        layout.addLayout(fen_row)
        layout.addWidget(self._status)
        self._refresh()

    # -- public API ----------------------------------------------------------

    @property
    def board(self) -> chess.Board:
        return self._board.copy(stack=False)

    def fen(self) -> str:
        return export_fen(self._board)

    def load_fen(self, text: str) -> None:
        """Replace the position from a FEN; raises ValueError if it can't be parsed."""
        self._replace(parse_fen(text))

    def set_orientation(self, orientation: Orientation) -> None:
        self.editor.set_orientation(orientation)

    def apply_tool(self, square: chess.Square, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
        """What a click on a square does: right button clears; left places the palette piece,
        or clears the square if that piece is already there."""
        tool = None if button == Qt.MouseButton.RightButton else self.palette.current()
        if tool is not None and self._board.piece_at(square) == tool:
            tool = None
        self.set_piece(square, tool)

    def set_piece(self, square: chess.Square, piece: chess.Piece | None) -> None:
        if piece is None:
            self._board.remove_piece_at(square)
        else:
            self._board.set_piece_at(square, piece)
        self._after_edit()

    def set_placement(self, placement: chess.BaseBoard, turn: chess.Color | None = None) -> bool:
        """Take the pieces from a board reading in one edit, keeping side to move unless `turn` is given.

        Castling rights the new placement no longer allows are dropped and en
        passant is cleared. Returns False when nothing changed.
        """
        new_turn = self._board.turn if turn is None else turn
        if placement.board_fen() == self._board.board_fen() and new_turn == self._board.turn:
            return False
        board = self._board.copy(stack=False)
        board.set_board_fen(placement.board_fen())
        board.turn = new_turn
        board.ep_square = None
        self._replace(board, by_user=False)
        return True

    def show_board(self, board: chess.Board) -> None:
        """Show a tracked game position as it is (with its moves), highlighting the last move."""
        self.editor.set_last_move(board.peek() if board.move_stack else None)
        if board.fen() == self._board.fen() and board.move_stack == self._board.move_stack:
            return
        self._replace(board.copy(), by_user=False)

    # -- internals -----------------------------------------------------------

    def _replace(self, board: chess.Board, by_user: bool = True) -> None:
        self._board = board
        self._after_edit(by_user)

    def _after_edit(self, by_user: bool = True) -> None:
        if by_user:
            self.editor.set_last_move(None)
        # Drop game state the new placement can no longer support.
        self._board.castling_rights &= possible_castling_rights(self._board)
        if self._board.ep_square is not None and self._board.ep_square not in en_passant_candidates(self._board):
            self._board.ep_square = None
        self._refresh()
        self.position_changed.emit(self.fen())
        if by_user:
            self.edited_by_user.emit()

    def _refresh(self) -> None:
        board = self._board
        self.editor.set_position(board)
        widgets = [self._white_to_move, self._black_to_move, self._ep_combo, *self._castling_boxes.values()]
        for w in widgets:
            w.blockSignals(True)
        (self._white_to_move if board.turn == chess.WHITE else self._black_to_move).setChecked(True)
        possible = possible_castling_rights(board)
        for mask, box in self._castling_boxes.items():
            box.setEnabled(bool(possible & mask))
            box.setChecked(bool(board.castling_rights & mask))
        self._ep_combo.clear()
        self._ep_combo.addItem("none", None)
        for square in en_passant_candidates(board):
            self._ep_combo.addItem(chess.square_name(square), square)
        self._ep_combo.setCurrentIndex(max(0, self._ep_combo.findData(board.ep_square)))
        self._ep_combo.setEnabled(self._ep_combo.count() > 1)
        for w in widgets:
            w.blockSignals(False)
        self._fen_edit.setText(self.fen())
        self._show_status()

    def _show_status(self, error: str | None = None) -> None:
        if error:
            self._status.setText(f"✖ {error}")
            self._status.setStyleSheet("color: #b3261e;")
            return
        problems = position_problems(self._board)
        if problems:
            self._status.setText("\n".join(f"⚠ {p}" for p in problems))
            self._status.setStyleSheet("color: #b3261e;")
            return
        side = "White" if self._board.turn == chess.WHITE else "Black"
        if self._board.is_checkmate():
            text = f"✔ Legal position: {side} is checkmated."
        elif self._board.is_stalemate():
            text = f"✔ Legal position: stalemate, {side} has no moves."
        else:
            text = f"✔ Legal position, {side} to move{' (in check)' if self._board.is_check() else ''}."
        self._status.setText(text)
        self._status.setStyleSheet("color: #1e7b34;")

    def _on_side_toggled(self) -> None:
        self._board.turn = chess.WHITE if self._white_to_move.isChecked() else chess.BLACK
        self._after_edit()

    def _on_castling_toggled(self) -> None:
        rights = 0
        for mask, box in self._castling_boxes.items():
            if box.isChecked():
                rights |= mask
        self._board.castling_rights = rights
        self._after_edit()

    def _on_ep_changed(self) -> None:
        self._board.ep_square = self._ep_combo.currentData()
        self._after_edit()

    def _on_fen_entered(self) -> None:
        try:
            self.load_fen(self._fen_edit.text())
        except ValueError as exc:
            self._show_status(str(exc))

    def _copy_fen(self) -> None:
        QGuiApplication.clipboard().setText(self.fen())
