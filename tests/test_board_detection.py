import time

import chess
import cv2
import numpy as np
import pytest
from synthetic import mirror_window, phone_screen, render_board

from chesslens.domain.models import Orientation
from chesslens.vision.board_detector import _grid_origins, detect_board


def assert_corners(found, expected, tol=1.5):
    assert found is not None, "no board found"
    assert np.abs(np.float32(found.corners) - np.float32(expected)).max() <= tol, found.corners


def rect(x, y, side):
    return [(x, y), (x + side, y), (x + side, y + side), (x, y + side)]


def test_finds_board_inside_a_mirrored_phone_screen():
    screen, (bx, by) = phone_screen(render_board(square=50))
    frame, scale, (px, py) = mirror_window(screen)
    found = detect_board(frame)
    assert_corners(found, rect(px + bx * scale, py + by * scale, 400 * scale))
    assert found.confidence >= 0.95
    assert found.method == "auto"
    assert found.frame_size == (1084, 681)


def test_region_restricts_search_and_keeps_frame_coordinates():
    screen, (bx, by) = phone_screen(render_board(square=50))
    frame, scale, (px, py) = mirror_window(screen)
    region = (px, py, round(screen.shape[1] * scale), 681)
    assert_corners(detect_board(frame, region=region), rect(px + bx * scale, py + by * scale, 400 * scale))


def test_finds_board_that_fills_the_whole_image():
    # The outer edges are the image border, so only the 7 inner lines are visible.
    assert_corners(detect_board(render_board(square=40)), rect(0, 0, 320))


@pytest.mark.parametrize("square", [14, 25, 39.5, 61.3])
def test_sub_pixel_square_sizes(square):
    big = render_board(square=100)
    side = 800 * square / 100
    board = cv2.resize(big, (round(side), round(side)), interpolation=cv2.INTER_AREA)
    canvas = np.full((board.shape[0] + 80, board.shape[1] + 60, 3), (43, 46, 49), np.uint8)
    canvas[50 : 50 + board.shape[0], 30 : 30 + board.shape[1]] = board
    assert_corners(detect_board(canvas), rect(30, 50, board.shape[1]), tol=1.0)


def test_highlights_dots_and_black_at_bottom_do_not_matter():
    board = render_board(
        "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R",
        square=40,
        orientation=Orientation.BLACK_BOTTOM,
        highlight=(chess.F3, chess.G1),
        dots=(chess.E2, chess.F1, chess.G1),
    )
    screen, (bx, by) = phone_screen(board)
    found = detect_board(screen)
    assert_corners(found, rect(bx, by, 320))
    assert found.confidence >= 0.95


def test_grid_survives_the_wobble_of_real_line_positions():
    # Inner lines measured on a live Chess.com bot-game frame (39.25 px squares). The first gap is 0.35 px
    # too wide; stepping on from it alone drifted 2 px by the last line and the board wasn't found.
    measured = np.array([39.4, 79.0, 118.0, 157.3, 196.5, 236.0, 275.0])
    origins = _grid_origins(measured, 12, 314 / 8)
    assert any(abs(s - 39.25) < 0.2 and abs(start) < 1.0 for s, start in origins)


def test_board_with_uneven_square_widths():
    # Resampled video makes squares alternate between 39 and 40 px wide.
    widths = [40, 39, 39, 40, 39, 40, 39, 39]
    edges = np.concatenate([[0], np.cumsum(widths)])
    board = render_board(square=50, letters=True)
    uneven = np.zeros((edges[-1], edges[-1], 3), np.uint8)
    for r in range(8):
        for c in range(8):
            cell = board[r * 50 : (r + 1) * 50, c * 50 : (c + 1) * 50]
            uneven[edges[r] : edges[r + 1], edges[c] : edges[c + 1]] = cv2.resize(
                cell, (widths[c], widths[r]), interpolation=cv2.INTER_AREA
            )
    canvas = np.full((uneven.shape[0] + 100, uneven.shape[1] + 40, 3), (43, 46, 49), np.uint8)
    canvas[60 : 60 + uneven.shape[0], 20 : 20 + uneven.shape[1]] = uneven
    assert_corners(detect_board(canvas), rect(20, 60, edges[-1]), tol=1.5)


def test_no_board_means_none():
    screen, _ = phone_screen(np.full((320, 320, 3), (43, 46, 49), np.uint8))
    assert detect_board(screen) is None
    assert detect_board(np.zeros((50, 50, 3), np.uint8)) is None


def test_plain_stripes_are_not_a_board():
    stripes = np.zeros((400, 400, 3), np.uint8)
    for i in range(0, 400, 40):
        stripes[:, i : i + 20] = 255
    assert detect_board(stripes) is None


def test_detection_is_fast_enough_for_a_full_window():
    frame, _, _ = mirror_window(phone_screen(render_board(square=50))[0])
    start = time.perf_counter()
    detect_board(frame)
    assert time.perf_counter() - start < 0.5
