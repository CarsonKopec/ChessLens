import chess
import cv2
import numpy as np
import pytest
from synthetic import WOOD, render_board

from chesslens.domain.models import Orientation
from chesslens.vision.calibration import BoardCalibration, guess_orientation, is_convex, piece_colours


def test_from_rect_orders_corners_clockwise_from_top_left():
    cal = BoardCalibration.from_rect(10, 20, 300, 310, frame_size=(640, 480))
    assert cal.corners == ((10, 20), (310, 20), (310, 330), (10, 330))
    assert cal.method == "manual"


def test_moving_a_corner_makes_it_manual():
    cal = BoardCalibration.from_rect(0, 0, 100, 100, frame_size=(200, 200))
    auto = BoardCalibration(cal.corners, cal.frame_size, method="auto", confidence=0.9)
    moved = auto.with_corner(2, (110, 104))
    assert moved.corners[2] == (110, 104)
    assert moved.method == "manual" and moved.confidence == 1.0


def test_corner_cannot_fold_the_board_over():
    cal = BoardCalibration.from_rect(0, 0, 100, 100, frame_size=(200, 200))
    with pytest.raises(ValueError):
        cal.with_corner(0, (150, 150))  # top-left dragged past bottom-right


def test_convexity():
    assert is_convex([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert not is_convex([(0, 0), (10, 10), (10, 0), (0, 10)])  # bow tie
    assert not is_convex([(0, 0), (0, 10), (10, 10), (10, 0)])  # anticlockwise on screen
    assert not is_convex([(0, 0), (5, 0), (10, 0), (0, 10)])  # three in a line


def test_rescaled_follows_a_uniformly_resized_window():
    cal = BoardCalibration.from_rect(100, 50, 200, 200, frame_size=(1000, 500))
    assert cal.rescaled((2000, 1000)).corners == ((200, 100), (600, 100), (600, 500), (200, 500))


def test_warp_uses_the_corners():
    board = render_board("4k3/8/8/8/8/8/8/4K3", square=50)
    frame = np.zeros((500, 500, 3), np.uint8)
    frame[30:430, 60:460] = board
    straight = BoardCalibration.from_rect(60, 30, 400, 400, frame_size=(500, 500)).warp(frame)
    assert np.abs(straight.astype(int) - cv2.resize(board, (800, 800)).astype(int)).mean() < 6


def test_census_finds_each_side_and_ignores_move_dots():
    board = render_board(square=100, dots=(chess.E3, chess.E4, chess.D3, chess.D4))
    census = piece_colours(board)
    assert (census[:2] == -1).all()
    assert (census[2:6] == 0).all()
    assert (census[6:] == 1).all()


def test_census_handles_highlighted_squares():
    board = render_board("4k3/8/8/8/4P3/8/8/4K3", square=100, highlight=(chess.E2, chess.E4))
    census = piece_colours(board)
    assert census[4, 4] == 1 and census[6, 4] == 0  # pawn on the highlighted e4, nothing on e2
    assert int((census != 0).sum()) == 3


@pytest.mark.parametrize("letters", [False, True])
def test_census_on_dark_squares_darker_than_the_old_dark_piece_bar(letters):
    # Chess.com's wood board: dark squares average ~93, so "much of the cell is below 100" called
    # every white piece on a dark square black, and Learn pieces warned on a correct position.
    board = render_board(square=100, letters=letters, colours=WOOD)
    census = piece_colours(board)
    assert (census[:2] == -1).all() and (census[2:6] == 0).all() and (census[6:] == 1).all()


@pytest.mark.parametrize("orientation", list(Orientation))
def test_orientation_guess_from_start_position(orientation):
    guess, confidence = guess_orientation(render_board(square=100, orientation=orientation))
    assert guess is orientation
    assert confidence == 1.0


def test_orientation_guess_from_a_middlegame():
    placement = "r1bq1rk1/pp3ppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R"
    guess, confidence = guess_orientation(render_board(placement, square=100, orientation=Orientation.BLACK_BOTTOM))
    assert guess is Orientation.BLACK_BOTTOM and confidence > 0.5


def test_orientation_unknown_without_evidence():
    assert guess_orientation(render_board("8/8/8/8/8/8/8/8", square=100)) == (None, 0.0)
    assert guess_orientation(render_board("8/8/8/8/8/8/3k4/3K4", square=100))[0] is None  # both on one half
