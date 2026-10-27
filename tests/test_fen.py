import chess
import pytest

from chesslens.chess.fen_pgn import export_fen, parse_fen
from chesslens.chess.validations import en_passant_candidates, position_problems, possible_castling_rights


def test_parse_full_and_placement_only_fen():
    assert export_fen(parse_fen(chess.STARTING_FEN)) == chess.STARTING_FEN
    board = parse_fen("  4k3/8/8/8/8/8/8/4K3  ")
    assert export_fen(board) == "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


@pytest.mark.parametrize("text", ["", "   ", "not a fen", "8/8/8 w", "4k3/8/8/8/8/8/8/4K3 x - - 0 1"])
def test_bad_fen_raises_readable_error(text):
    with pytest.raises(ValueError):
        parse_fen(text)


def test_start_position_has_no_problems():
    assert position_problems(chess.Board()) == []


@pytest.mark.parametrize(
    "fen, problem",
    [
        ("8/8/8/8/8/8/8/8 w - - 0 1", "The board is empty."),
        ("8/8/8/8/8/8/8/4K3 w - - 0 1", "Black has no king."),
        ("4k3/8/8/8/8/8/8/4K2P w - - 0 1", "There are pawns on the first or last rank."),
        ("4k3/8/8/8/8/8/8/3K3R w K - 0 1", "A castling right doesn't match where the king and rooks are."),
        ("4k3/4Q3/8/8/8/8/8/4K3 w - - 0 1", "The side that just moved is in check."),
        ("4k3/8/8/8/8/8/PPPPPPPP/P3K3 w - - 0 1", "White has more than 8 pawns."),
    ],
)
def test_problems_are_explained(fen, problem):
    assert problem in position_problems(chess.Board(fen))


def test_possible_castling_follows_king_and_rooks():
    assert possible_castling_rights(chess.Board()) == chess.BB_CORNERS
    board = chess.Board()
    board.remove_piece_at(chess.H1)
    board.remove_piece_at(chess.E8)
    assert possible_castling_rights(board) == chess.BB_A1


def test_en_passant_candidates_need_a_capturing_pawn():
    assert en_passant_candidates(chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - - 0 1")) == [chess.D6]
    assert en_passant_candidates(chess.Board("4k3/8/8/3pPp2/8/8/8/4K3 w - - 0 1")) == [chess.D6, chess.F6]
    assert en_passant_candidates(chess.Board("4k3/8/8/3p4/8/8/8/4K3 w - - 0 1")) == []
    assert en_passant_candidates(chess.Board("4k3/8/8/8/3Pp3/8/8/4K3 b - - 0 1")) == [chess.D3]
