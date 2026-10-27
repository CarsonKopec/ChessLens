import chess
import pytest

from chesslens.engine.results import EngineLine
from chesslens.explain.rule_explanations import describe_eval, explain


def notes(moves: str, uci: str, pv=(), cp=30, mate=None) -> list[str]:
    board = chess.Board()
    for san in moves.split():
        board.push_san(san)
    move = chess.Move.from_uci(uci)
    line = EngineLine(1, uci, board.san(move), None if mate is not None else cp, mate, tuple(pv) or (uci,), "")
    return explain(board, line)


def test_development_and_castling():
    assert notes("e4 e5 Nf3 Nc6", "f1b5") == ["Develops the bishop."]
    assert notes("e4 e5 Nf3 Nc6 Bc4 Nf6", "e1g1") == ["Castles kingside: the king gets safer and the rook comes into play."]


def test_checkmate_and_capture():
    assert notes("e4 e5 Qh5 Nc6 Bc4 Nf6", "h5f7", mate=1) == ["Checkmate.", "Captures the pawn on f7."]


def test_recapture_and_retreat():
    assert notes("e4 e5 Nf3 d6 d4 exd4", "f3d4") == ["Recaptures the pawn on d4."]
    assert "Moves the attacked knight to safety." in notes("e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 e5", "d4b3")


def test_threat_and_pin():
    assert "Attacks the knight on f6." in notes("e4 Nf6", "e4e5")
    assert "Pins the knight on c6 to the king." in notes("e4 e5 Nf3 Nc6 Bc4 d6", "c4b5")


def test_piece_left_to_be_taken_is_flagged_not_explained():
    assert "Leaves the knight where it can be taken; the engine's line shows why that's fine." in notes("e4 e5 Nf3 h6", "f3g5")


def test_material_won_later_in_the_line():
    # Nf3 itself captures nothing, but the line it starts wins a knight for a pawn... net +3 for White.
    quiet = notes("d4 e5", "g1f3", pv=["g1f3", "b8c6", "d4e5", "c6e5", "f3e5"])
    assert "Wins material: 3 pawns' worth by the end of the line." in quiet


def test_mate_for_the_other_side():
    assert notes("f3 e5", "g2g4", mate=-1)[0] == "Every move loses to a forced mate; this one lasts longest (1 move)."


def test_promotion_note_without_double_counting():
    board = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    line = EngineLine(1, "a7a8q", "a8=Q", 900, None, ("a7a8q",), "")
    assert explain(board, line) == ["Promotes the pawn to a queen."]


def test_illegal_line_gives_no_notes():
    line = EngineLine(1, "e2e5", "e5?", 0, None, ("e2e5",), "")
    assert explain(chess.Board(), line) == []


@pytest.mark.parametrize(
    "cp, mate, text",
    [
        (10, None, "About equal."),
        (-60, None, "Black is slightly better."),
        (180, None, "White is clearly better."),
        (-400, None, "Black is winning."),
        (None, 3, "White has a forced mate in 3."),
        (None, -2, "Black has a forced mate in 2."),
    ],
)
def test_evaluation_in_words(cp, mate, text):
    assert describe_eval(EngineLine(1, "e2e4", "e4", cp, mate, ("e2e4",), "")) == text
