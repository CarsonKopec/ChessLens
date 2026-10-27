"""`chesslens selftest`: check that this copy of ChessLens works, from source or as the installed app.

Runs each part on generated data, without the screen or the phone: settings,
the Qt window toolkit, board finding, piece recognition, game tracking, and
Stockfish if it is installed. Prints one line per check.
"""

from __future__ import annotations

import os
import platform
import sys
import threading
import time
import traceback
from collections.abc import Callable

import numpy as np

from chesslens import __version__
from chesslens.config import Config, DEFAULT_CONFIG_PATH


def run_selftest(config: Config) -> int:
    print(f"ChessLens {__version__} on Python {platform.python_version()}, {platform.platform()}")
    print(f"Frozen build: {getattr(sys, 'frozen', False)}; default settings: {DEFAULT_CONFIG_PATH}")
    print(f"Output encoding: {getattr(sys.stdout, 'encoding', None)}")
    checks: list[tuple[str, Callable[[], str]]] = [
        ("settings", lambda: f"capture {config.capture.fps} fps, engine MultiPV {config.engine.multipv}"),
        ("window toolkit (Qt)", _check_qt),
        ("board finding and reading", _check_vision),
        ("game tracking", _check_tracking),
        ("Stockfish", lambda: _check_engine(config)),
    ]
    failures = 0
    for name, check in checks:
        try:
            print(f"  ok    {name}: {check()}")
        except _Skipped as skipped:
            print(f"  skip  {name}: {skipped}")
        except Exception:
            failures += 1
            print(f"  FAIL  {name}:\n" + "".join("        " + line for line in traceback.format_exc().splitlines(True)))
    print("All checks passed." if not failures else f"{failures} check(s) failed.")
    return 1 if failures else 0


class _Skipped(Exception):
    pass


def _check_qt() -> str:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import __version__ as pyside_version
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return f"PySide6 {pyside_version}, platform {app.platformName()}"


def _synthetic_board(placement: str) -> np.ndarray:
    """A plain two-colour board with simple distinct pieces, 50 px squares."""
    import chess
    import cv2

    board = chess.BaseBoard(placement)
    image = np.zeros((400, 400, 3), np.uint8)
    shapes = {chess.PAWN: 0.18, chess.KNIGHT: 0.24, chess.BISHOP: 0.28, chess.ROOK: 0.32, chess.QUEEN: 0.36, chess.KING: 0.4}
    for sq in chess.SQUARES:
        r, c = 7 - chess.square_rank(sq), chess.square_file(sq)
        image[r * 50 : r * 50 + 50, c * 50 : c * 50 + 50] = (210, 238, 238) if (r + c) % 2 == 0 else (86, 150, 119)
        piece = board.piece_at(sq)
        if piece:
            colour = (250, 250, 250) if piece.color else (50, 50, 50)
            cv2.circle(image, (c * 50 + 25, r * 50 + 27), int(50 * shapes[piece.piece_type]), colour, -1)
            cv2.putText(image, piece.symbol().upper(), (c * 50 + 17, r * 50 + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (128, 0, 128), 2)
    return image


def _check_vision() -> str:
    import chess
    import cv2

    from chesslens.domain.models import Orientation
    from chesslens.vision.board_detector import detect_board
    from chesslens.vision.piece_classifier import read_board
    from chesslens.vision.piece_set import PieceSet

    frame = np.full((600, 700, 3), (43, 46, 49), np.uint8)
    frame[100:500, 150:550] = _synthetic_board(chess.STARTING_BOARD_FEN)
    found = detect_board(frame)
    if found is None:
        raise AssertionError("the test board was not found")
    pieces = PieceSet("selftest")
    pieces.learn(found.warp(frame), chess.BaseBoard(), Orientation.WHITE_BOTTOM)
    after_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"
    frame[100:500, 150:550] = _synthetic_board(after_e4)
    start = time.perf_counter()
    reading = read_board(cv2.resize(found.warp(frame), (800, 800)), pieces, Orientation.WHITE_BOTTOM)
    ms = (time.perf_counter() - start) * 1000
    if reading.placement().board_fen() != after_e4 or reading.problems():
        raise AssertionError(f"read {reading.placement().board_fen()} with problems {reading.problems()}")
    return f"board found ({found.confidence:.0%} of squares match), 64/64 squares read in {ms:.0f} ms"


def _check_tracking() -> str:
    import chess

    from chesslens.chess.tracker import GameTracker, TransitionKind

    tracker = GameTracker()
    board = chess.Board()
    for san in ("e4", "c5", "Nf3", "d6"):
        board.push_san(san)
        if tracker.observe(chess.BaseBoard(board.board_fen())).kind is not TransitionKind.MOVES:
            raise AssertionError(f"{san} was not followed")
    return "1. e4 c5 2. Nf3 d6 followed"


def _check_engine(config: Config) -> str:
    import chess

    from chesslens.engine.stockfish_service import INSTALL_HINT, EngineService, find_stockfish
    from chesslens.user_settings import load_user_settings

    path = find_stockfish(config.engine.path, load_user_settings().get("engine_path"))
    if path is None:
        raise _Skipped(f"not installed (optional; install with: {INSTALL_HINT})")
    done = threading.Event()
    results = []
    service = EngineService(str(path), think_time_s=0.3, on_result=lambda r: (results.append(r), r.final and done.set()))
    try:
        service.start(wait=True)
        service.submit(1, chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"))
        if not done.wait(15):
            raise AssertionError("no answer within 15 s")
    finally:
        service.close()
    best = results[-1].best
    if best is None or best.san != "Ra8#":
        raise AssertionError(f"expected Ra8#, got {best and best.san}")
    return f"{service.engine_name} found Ra8# ({path})"
