import chess
import cv2
import numpy as np
import pytest
from synthetic import DARK, LIGHT, render_board

from chesslens.domain.models import Orientation
from chesslens.vision.board_warp import (
    from_board_coords,
    grid_lines,
    screen_cell_to_square,
    square_images,
    square_to_screen_cell,
    to_board_coords,
    warp_board,
)

SKEWED = [(112.0, 64.0), (498.0, 80.0), (520.0, 470.0), (90.0, 452.0)]


def test_white_bottom_puts_a8_top_left_and_h1_bottom_right():
    assert screen_cell_to_square(0, 0, Orientation.WHITE_BOTTOM) == chess.A8
    assert screen_cell_to_square(7, 7, Orientation.WHITE_BOTTOM) == chess.H1
    assert screen_cell_to_square(6, 4, Orientation.WHITE_BOTTOM) == chess.E2


def test_black_bottom_puts_h1_top_left_and_a8_bottom_right():
    assert screen_cell_to_square(0, 0, Orientation.BLACK_BOTTOM) == chess.H1
    assert screen_cell_to_square(7, 7, Orientation.BLACK_BOTTOM) == chess.A8
    assert screen_cell_to_square(1, 3, Orientation.BLACK_BOTTOM) == chess.E2


@pytest.mark.parametrize("orientation", list(Orientation))
def test_square_mapping_round_trips(orientation):
    for square in chess.SQUARES:
        assert screen_cell_to_square(*square_to_screen_cell(square, orientation), orientation) == square


def test_warp_straightens_a_skewed_board():
    board = render_board(square=100)
    to_frame = cv2.getPerspectiveTransform(np.float32([[0, 0], [800, 0], [800, 800], [0, 800]]), np.float32(SKEWED))
    frame = cv2.warpPerspective(board, to_frame, (640, 560))
    straight = warp_board(frame, SKEWED)
    assert straight.shape == (800, 800, 3)
    # Compare square colours away from the edges, where resampling blurs.
    for r in range(8):
        for c in range(8):
            got = straight[r * 100 + 5 : r * 100 + 20, c * 100 + 5 : c * 100 + 20].reshape(-1, 3).mean(axis=0)
            want = LIGHT if (r + c) % 2 == 0 else DARK
            assert np.abs(got - want).max() < 12, (r, c, got)


def test_board_coordinates_round_trip_and_grid_meets_corners():
    pts = [(0, 0), (8, 0), (8, 8), (0, 8), (3.5, 2.25)]
    frame_pts = from_board_coords(SKEWED, pts)
    assert np.allclose(frame_pts[:4], SKEWED, atol=1e-3)
    assert np.allclose(to_board_coords(SKEWED, frame_pts), pts, atol=1e-3)
    lines = grid_lines(SKEWED)
    assert len(lines) == 18
    assert np.allclose(lines[0], [SKEWED[0], SKEWED[3]], atol=1e-3)  # left edge
    assert np.allclose(lines[17], [SKEWED[3], SKEWED[2]], atol=1e-3)  # bottom edge


@pytest.mark.parametrize("orientation", list(Orientation))
def test_square_images_follow_orientation(orientation):
    board = render_board("4k3/8/8/8/8/8/8/4K3", square=100, orientation=orientation)
    crops = square_images(board, orientation)
    assert len(crops) == 64 and crops[chess.A1].shape == (100, 100, 3)
    assert crops[chess.E8][50, 50].max() < 80  # black king's fill
    assert crops[chess.E1][50, 50].min() > 240  # white king's fill
