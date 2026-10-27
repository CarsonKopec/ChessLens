"""Position editor behaviour, driven through its widgets offscreen."""

import chess
import pytest
from conftest import dispose
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from chesslens.domain.models import Orientation
from chesslens.ui.position_panel import PositionPanel

WHITE_KNIGHT = chess.Piece(chess.KNIGHT, chess.WHITE)


@pytest.fixture
def panel(qapp):
    p = PositionPanel()
    p.resize(460, 760)
    p.show()
    qapp.processEvents()
    yield p
    dispose(p)


def click_cell(panel: PositionPanel, row: int, col: int, button=Qt.MouseButton.LeftButton) -> None:
    centre = panel.editor._cell_rect(row, col).center().toPoint()
    QTest.mouseClick(panel.editor, button, Qt.KeyboardModifier.NoModifier, centre)


def test_starts_from_the_initial_position(panel):
    assert panel.fen() == chess.STARTING_FEN
    assert panel._status.text().startswith("✔")


def test_click_places_palette_piece_and_again_removes_it(panel):
    panel.palette.select(WHITE_KNIGHT)
    click_cell(panel, 4, 4)  # e4 with White at the bottom
    assert panel.board.piece_at(chess.E4) == WHITE_KNIGHT
    click_cell(panel, 4, 4)
    assert panel.board.piece_at(chess.E4) is None


def test_right_click_clears(panel):
    click_cell(panel, 6, 0, Qt.MouseButton.RightButton)  # a2
    assert panel.board.piece_at(chess.A2) is None


def test_eraser_tool(panel):
    panel.palette.select(None)
    click_cell(panel, 0, 0)  # a8
    assert panel.board.piece_at(chess.A8) is None


def test_clicks_follow_orientation(panel):
    panel.set_orientation(Orientation.BLACK_BOTTOM)
    panel.palette.select(None)
    click_cell(panel, 0, 0)  # top-left is h1 when Black is at the bottom
    assert panel.board.piece_at(chess.H1) is None
    assert panel.board.piece_at(chess.A8) is not None


def test_keyboard_editing(panel):
    panel.editor.setFocus()
    click_cell(panel, 6, 4)  # cursor to e2 (and the default tool toggles the pawn off)
    QTest.keyClick(panel.editor, Qt.Key.Key_Up)
    QTest.keyClick(panel.editor, Qt.Key.Key_Up)  # e4
    QTest.keyClicks(panel.editor, "n")
    assert panel.board.piece_at(chess.E4) == chess.Piece(chess.KNIGHT, chess.BLACK)
    QTest.keyClick(panel.editor, Qt.Key.Key_Delete)
    assert panel.board.piece_at(chess.E4) is None


def test_castling_rights_follow_the_pieces(panel):
    boxes = list(panel._castling_boxes.values())
    assert all(b.isEnabled() and b.isChecked() for b in boxes)
    panel.set_piece(chess.H1, None)
    white_short = panel._castling_boxes[chess.BB_H1]
    assert not white_short.isEnabled() and not white_short.isChecked()
    assert panel.fen().split()[2] == "Qkq"
    panel._castling_boxes[chess.BB_A8].setChecked(False)
    assert panel.fen().split()[2] == "Qk"


def test_side_to_move_and_en_passant(panel):
    panel.load_fen("4k3/8/8/3pP3/8/8/8/4K3 w - - 0 1")
    combo = panel._ep_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["none", "d6"]
    combo.setCurrentIndex(1)
    assert panel.fen().split()[3] == "d6"
    panel._black_to_move.setChecked(True)
    fields = panel.fen().split()
    assert fields[1] == "b" and fields[3] == "-"  # en passant no longer possible
    assert not combo.isEnabled()


def test_problems_are_shown(panel):
    panel.load_fen("8/8/8/8/8/8/8/4K3 w - - 0 1")
    assert "⚠ Black has no king." in panel._status.text()


def test_fen_field_loads_and_reports_errors(panel):
    panel._fen_edit.setText("4k3/8/8/8/8/8/8/R3K3")
    QTest.keyClick(panel._fen_edit, Qt.Key.Key_Return)
    assert panel.board.piece_at(chess.A1) == chess.Piece(chess.ROOK, chess.WHITE)
    panel._fen_edit.setText("rubbish")
    QTest.keyClick(panel._fen_edit, Qt.Key.Key_Return)
    assert panel._status.text().startswith("✖ Not a valid FEN")
    assert panel.board.piece_at(chess.A1) is not None  # position kept


def test_copy_fen(panel):
    panel.load_fen("4k3/8/8/8/8/8/8/4K3 b - - 3 40")
    panel._copy_fen()
    assert QGuiApplication.clipboard().text() == "4k3/8/8/8/8/8/8/4K3 b - - 3 40"


def test_every_change_is_announced(panel):
    seen = []
    panel.position_changed.connect(seen.append)
    panel.set_piece(chess.E4, WHITE_KNIGHT)
    assert seen and seen[-1] == panel.fen()


def test_points_outside_the_board_hit_no_square(panel):
    board = panel.editor._board_rect()
    assert panel.editor._square_at(board.topLeft() - QPointF(1, 1)) is None
    assert panel.editor._square_at(board.bottomRight() + QPointF(1, 1)) is None
    assert panel.editor._square_at(board.topLeft() + QPointF(1, 1)) == chess.A8
