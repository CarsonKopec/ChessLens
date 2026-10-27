import chess
import cv2
import numpy as np
import pytest
from synthetic import WOOD, render_board

from chesslens.chess.validations import placement_problems
from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import square_to_screen_cell
from chesslens.vision.piece_classifier import CONFIDENT, read_board
from chesslens.vision.piece_set import EMPTY, MAX_EXAMPLES, PieceSet, list_piece_sets, occupancy_mismatches
from chesslens.vision.square_features import cells, empty_square, normalize_board, normalize_square, square_backgrounds

# Kings and queens on the other square colour than in the start position.
TEST_POSITION = "r1b2r1k/pp1qbppp/2n1pn2/8/2PP4/2N1PN2/PP1Q1PPP/R1B2K1R"


def board(placement=chess.STARTING_BOARD_FEN, square=100, orientation=Orientation.WHITE_BOTTOM, **kw):
    """A board as the warp would deliver it: 800×800 whatever the size on screen."""
    image = render_board(placement, square=square, orientation=orientation, letters=True, **kw)
    return image if image.shape[0] == 800 else cv2.resize(image, (800, 800), interpolation=cv2.INTER_LINEAR)


@pytest.fixture(scope="module")
def learned():
    pieces = PieceSet("test")
    pieces.learn(board(), chess.BaseBoard(), Orientation.WHITE_BOTTOM)
    return pieces


def test_normalised_piece_looks_the_same_on_any_square_colour():
    images = render_board("8/8/8/8/8/8/PP6/8", square=100, letters=True, highlight=(chess.B2,))
    sq = cells(images)
    backgrounds, tinted = square_backgrounds(images)
    a2, b2 = square_to_screen_cell(chess.A2, Orientation.WHITE_BOTTOM), square_to_screen_cell(chess.B2, Orientation.WHITE_BOTTOM)
    assert tinted[b2] and not tinted[a2]
    on_light = normalize_square(np.ascontiguousarray(sq[a2]), backgrounds[a2]).astype(float) / 255
    on_highlight = normalize_square(np.ascontiguousarray(sq[b2]), backgrounds[b2]).astype(float) / 255
    assert np.mean((on_light - on_highlight) ** 2) < 0.002


def test_a_streak_through_a_corner_is_not_a_highlight():
    # Wood grain: a dark streak down one side of a light square used to read as a highlight from its
    # corners, so the wrong background was cut away and wood showed around the piece (c8 on the browser).
    image = render_board(square=100, letters=True, colours=WOOD, highlight=(chess.E2, chess.E4))
    r, c = square_to_screen_cell(chess.C8, Orientation.WHITE_BOTTOM)
    image[r * 100 : (r + 1) * 100, c * 100 + 86 : c * 100 + 92] = (30, 50, 70)  # a dark grain line
    _, tinted = square_backgrounds(image)
    marked = {square_to_screen_cell(s, Orientation.WHITE_BOTTOM) for s in (chess.E2, chess.E4)}
    assert {(int(r_), int(c_)) for r_, c_ in np.argwhere(tinted)} == marked
    normalized, _ = normalize_board(image)
    f8 = square_to_screen_cell(chess.F8, Orientation.WHITE_BOTTOM)  # the other bishop, on a clean light square
    streak_column = normalized[r, c].astype(float)[:, :40] / 255  # the piece, left of the streak
    assert np.mean((streak_column - normalized[f8].astype(float)[:, :40] / 255) ** 2) < 0.002


def test_impossible_examples_are_not_learned():
    pieces = PieceSet("guarded")
    assert not pieces._add("b", empty_square())  # a blank square taught as a bishop
    covered = np.full_like(empty_square(), 60)
    assert not pieces._add(EMPTY, covered)  # a piece (or a cut gone wrong) taught as empty
    assert pieces.example_count == 0


def test_impossible_examples_already_in_a_set_are_left_out(learned, tmp_path):
    # A set learned before the check: one blank "bishop" made every empty square a 50/50 call.
    spoiled = learned.copy()
    spoiled.examples["b"].append(empty_square())
    spoiled.save(tmp_path)
    obs = read_board(board(TEST_POSITION), spoiled, Orientation.WHITE_BOTTOM)
    assert obs.confidence.min() < 0.6
    loaded = PieceSet.load("test", tmp_path)
    assert len(loaded.examples["b"]) == len(learned.examples["b"])
    obs = read_board(board(TEST_POSITION), loaded, Orientation.WHITE_BOTTOM)
    assert obs.placement() == chess.BaseBoard(TEST_POSITION) and obs.confidence.min() > 0.95


def test_learning_guard_on_a_wood_board():
    image = render_board(square=100, letters=True, colours=WOOD)
    assert occupancy_mismatches(image, chess.BaseBoard(), Orientation.WHITE_BOTTOM) == []


def test_learning_from_the_start_position_covers_every_piece(learned):
    assert learned.missing == []
    # Pawns on light and dark squares normalise to the same image, so one example covers both.
    assert len(learned.examples["P"]) == 1
    again = PieceSet("again", {k: list(v) for k, v in learned.examples.items()})
    assert again.learn(board(), chess.BaseBoard(), Orientation.WHITE_BOTTOM) == 0  # nothing new


def test_reads_an_unseen_position_with_highlights_and_move_dots(learned):
    image = board(TEST_POSITION, highlight=(chess.D1, chess.D2), dots=(chess.E2, chess.D1, chess.E5))
    obs = read_board(image, learned, Orientation.WHITE_BOTTOM)
    assert obs.placement() == chess.BaseBoard(TEST_POSITION)
    assert obs.confidence.min() > 0.95
    assert obs.problems() == []


def test_reads_at_phone_resolution(learned):
    small = PieceSet("small")
    small.learn(board(square=39), chess.BaseBoard(), Orientation.WHITE_BOTTOM)
    obs = read_board(board(TEST_POSITION, square=39), small, Orientation.WHITE_BOTTOM)
    assert obs.placement() == chess.BaseBoard(TEST_POSITION)
    assert obs.problems() == []


def test_reading_follows_orientation(learned):
    image = board(TEST_POSITION, orientation=Orientation.BLACK_BOTTOM)
    obs = read_board(image, learned, Orientation.BLACK_BOTTOM)
    assert obs.placement() == chess.BaseBoard(TEST_POSITION)


def test_side_to_move_from_last_move_highlight(learned):
    def hint(**kw):
        return read_board(board(TEST_POSITION, **kw), learned, Orientation.WHITE_BOTTOM).side_to_move_hint()

    assert hint(highlight=(chess.D1, chess.D2)) == chess.BLACK  # White's queen just moved
    assert hint(highlight=(chess.E8, chess.H8)) == chess.WHITE  # Black's king just moved
    assert hint(highlight=(chess.D1, chess.D2, chess.E4)) is None  # a third marked square: ambiguous
    assert hint() is None


def test_something_unknown_on_a_square_is_flagged(learned):
    image = board(TEST_POSITION)
    r, c = square_to_screen_cell(chess.E5, Orientation.WHITE_BOTTOM)
    # Something with structure the set has never seen, like a menu or marker drawn over the square.
    cv2.circle(image, (c * 100 + 50, r * 100 + 50), 32, (40, 40, 220), -1)
    cv2.rectangle(image, (c * 100 + 30, r * 100 + 40), (c * 100 + 70, r * 100 + 60), (240, 240, 60), -1)
    obs = read_board(image, learned, Orientation.WHITE_BOTTOM)
    assert obs.uncertain_cells() == [(r, c)]
    assert "Not sure about e5." in obs.problems()


def test_readings_from_an_incomplete_piece_set_are_not_trusted():
    # A piece the set never saw can resemble one it did (here the black knight), so a reading
    # is only trusted once every piece has been learned.
    no_knights = chess.BaseBoard()
    for sq in (chess.B1, chess.G1, chess.B8, chess.G8):
        no_knights.remove_piece_at(sq)
    pieces = PieceSet("no knights")
    pieces.learn(board(no_knights.board_fen()), no_knights, Orientation.WHITE_BOTTOM)
    assert sorted(pieces.missing) == ["N", "n"]
    obs = read_board(board(), pieces, Orientation.WHITE_BOTTOM)
    assert obs.problems()[0] == "The piece set hasn't learned N n yet."
    white_knight = square_to_screen_cell(chess.B1, Orientation.WHITE_BOTTOM)
    assert obs.confidence[white_knight] < CONFIDENT  # far from everything learned


def test_impossible_reading_is_rejected(learned):
    obs = read_board(board("4k3/8/8/8/8/8/8/K3K3"), learned, Orientation.WHITE_BOTTOM)
    assert obs.confidence.min() > 0.95  # it is sure of every square...
    assert "White has 2 kings." in obs.problems()  # ...but no real game looks like this


@pytest.mark.parametrize(
    "placement, problem",
    [
        ("8/8/8/8/8/8/8/4K3", "Black has no king."),
        ("4k3/8/8/8/8/8/8/K3K3", "White has 2 kings."),
        ("4k3/pppppppp/p7/8/8/8/8/4K3", "Black has 9 pawns."),
        ("4k2P/8/8/8/8/8/8/4K3", "There are pawns on the first or last rank."),
        ("4k3/8/8/8/8/8/PPPPPPPP/QQQ1K3", "White has more extra pieces than its missing pawns could have promoted to."),
        ("8/8/8/3k4/3K4/8/8/8", "Both kings are in check."),
    ],
)
def test_placement_problems(placement, problem):
    assert problem in placement_problems(chess.BaseBoard(placement))


def test_real_positions_have_no_placement_problems():
    assert placement_problems(chess.BaseBoard()) == []
    assert placement_problems(chess.BaseBoard(TEST_POSITION)) == []
    assert placement_problems(chess.BaseBoard("4k3/8/8/8/8/8/1PPPPPPP/QQ2K3")) == []  # one promotion, one pawn gone


def test_piece_set_save_and_load_round_trip(learned, tmp_path):
    learned.save(tmp_path)
    loaded = PieceSet.load("test", tmp_path)
    assert loaded.missing == [] and loaded.example_count == learned.example_count
    for symbol, images in learned.examples.items():
        assert all(np.array_equal(a, b) for a, b in zip(images, loaded.examples[symbol]))


def test_piece_sets_live_in_appdata(learned, isolated_appdata):
    assert list_piece_sets() == []
    path = learned.save()
    assert path.parent == isolated_appdata / "ChessLens" / "pieces"
    assert list_piece_sets() == ["test"]


def test_examples_per_class_are_capped():
    pieces = PieceSet("cap")
    rng = np.random.default_rng(0)
    for _ in range(MAX_EXAMPLES + 5):
        pieces._add("Q", rng.integers(0, 255, (48, 48, 3), dtype=np.uint8))
    assert len(pieces.examples["Q"]) == MAX_EXAMPLES


def test_a_full_class_replaces_its_most_similar_example():
    pieces = PieceSet("variety")
    distinct = np.zeros((48, 48, 3), np.uint8)  # e.g. the example learned from the start position
    pieces._add("K", distinct)
    for i in range(MAX_EXAMPLES + 4):  # many similar newer pictures, each different enough to keep
        image = np.full((48, 48, 3), 200, np.uint8)
        image[: 4 * (i + 1) % 48] = 120
        pieces._add("K", image)
    assert len(pieces.examples["K"]) == MAX_EXAMPLES
    assert any(np.array_equal(e, distinct) for e in pieces.examples["K"])  # the odd one out survives


def test_copy_is_independent(learned):
    copy = learned.copy()
    copy._add("Q", np.zeros((48, 48, 3), np.uint8))
    assert len(copy.examples["Q"]) == len(learned.examples["Q"]) + 1


def test_learning_guard_spots_an_editor_that_doesnt_match():
    image = board()
    assert occupancy_mismatches(image, chess.BaseBoard(), Orientation.WHITE_BOTTOM) == []
    moved = chess.BaseBoard()
    moved.remove_piece_at(chess.E2)
    moved.set_piece_at(chess.E4, chess.Piece(chess.PAWN, chess.WHITE))
    assert sorted(occupancy_mismatches(image, moved, Orientation.WHITE_BOTTOM)) == [chess.E2, chess.E4]
