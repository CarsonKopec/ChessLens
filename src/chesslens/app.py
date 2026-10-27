"""ChessLens entry point.

  chesslens [gui] [--window TEXT] [--video FILE]   open the capture dashboard (default)
  chesslens list                                   list windows that can be captured
  chesslens grab [--window TEXT] [-o FILE]         save one frame of a window as PNG
  chesslens analyse [FEN] [--time S]               print Stockfish's analysis as JSON (spec §6.5)
  chesslens benchmark DIR [--pieces NAME | --train DIR]   piece-recognition accuracy on labeled boards
  chesslens selftest                               check that this copy of ChessLens works
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from chesslens.config import Config, load_config


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, help="TOML settings file (default: config/default.toml)")
    common.add_argument("-v", "--verbose", action="store_true", help="log capture events")

    parser = argparse.ArgumentParser(prog="chesslens", description="Read a mirrored iPhone chess app on Windows.")
    sub = parser.add_subparsers(dest="command")
    gui = sub.add_parser("gui", parents=[common], help="open the capture dashboard (default)")
    gui.add_argument("--window", help="pre-select the window whose process or title contains this text")
    gui.add_argument("--video", type=Path, help="replay a screen recording instead of a live window")
    sub.add_parser("list", parents=[common], help="list windows that can be captured")
    grab = sub.add_parser("grab", parents=[common], help="save one frame of a window's client area as PNG")
    grab.add_argument("--window", help="process or title text to match (default: window_hint from config)")
    grab.add_argument("-o", "--output", type=Path, default=Path("chesslens-grab.png"))
    analyse = sub.add_parser("analyse", parents=[common], help="analyse a FEN with Stockfish and print JSON")
    analyse.add_argument("fen", nargs="?", default=None, help="position (default: the starting position)")
    analyse.add_argument("--time", type=float, default=None, help="search seconds (default: think_time_s from config)")
    bench = sub.add_parser("benchmark", parents=[common], help="measure piece recognition on labeled boards")
    bench.add_argument("data", type=Path, help="folder of labeled boards (PNG + JSON from 'Save labeled board')")
    source = bench.add_mutually_exclusive_group()
    source.add_argument("--pieces", default="Chess.com", help="learned piece set to test (default: Chess.com)")
    source.add_argument("--train", type=Path, help="learn a fresh piece set from this folder instead (keep it from other games)")
    sub.add_parser("selftest", parents=[common], help="check that this copy of ChessLens works")

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in sub.choices and argv[0] not in ("-h", "--help")):
        argv.insert(0, "gui")
    if argv[0] != "gui":
        _attach_console()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    if args.command == "list":
        return _list(config)
    if args.command == "grab":
        return _grab(config, args.window, args.output)
    if args.command == "analyse":
        return _analyse(config, args.fen, args.time)
    if args.command == "benchmark":
        return _benchmark(args.data, args.pieces, args.train)
    if args.command == "selftest":
        from chesslens.selftest import run_selftest

        return run_selftest(config)
    return _gui(config, args.window, args.video)


def _attach_console() -> None:
    """Make the packaged ChessLens.exe (a windowed program) print properly for the command-line subcommands.

    Write to wherever its output was redirected or piped, or else to the terminal it was started from,
    in the encoding that terminal expects: an explicit PYTHONIOENCODING wins, as in Python itself;
    otherwise the console's code page, as console programs do. A regular Python run sets all this up itself.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import ctypes
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    attached = bool(kernel32.AttachConsole(-1))  # ATTACH_PARENT_PROCESS; fails harmlessly if there is none
    code_page = kernel32.GetConsoleOutputCP() if attached else 0
    requested = os.environ.get("PYTHONIOENCODING", "").split(":")[0]
    encoding = requested or (f"cp{code_page}" if code_page else "utf-8")
    if sys.stdout is not None:  # output was redirected or piped and the streams exist: just fix the encoding
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding=encoding, errors="replace")
        return
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    if handle and handle != ctypes.c_void_p(-1).value:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        sys.stdout = sys.stderr = open(fd, "w", encoding=encoding, errors="replace", buffering=1)  # noqa: SIM115
    elif attached:
        sys.stdout = sys.stderr = open("CONOUT$", "w", encoding=encoding, errors="replace", buffering=1)  # noqa: SIM115


def _benchmark(data: Path, pieces_name: str, train_dir: Path | None) -> int:
    from chesslens.vision.benchmark import evaluate, load_labeled_boards, train
    from chesslens.vision.piece_set import PieceSet

    boards = load_labeled_boards(data)
    if not boards:
        print(f"No labeled boards (PNG + JSON) in {data}", file=sys.stderr)
        return 1
    if train_dir is not None:
        training = load_labeled_boards(train_dir)
        if not training:
            print(f"No labeled boards in {train_dir}", file=sys.stderr)
            return 1
        pieces = train("benchmark", training)
        print(f"Learned from {len(training)} boards in {train_dir}; missing: {' '.join(pieces.missing) or 'none'}")
    else:
        try:
            pieces = PieceSet.load(pieces_name)
        except FileNotFoundError:
            print(f"No learned piece set named {pieces_name!r}. Learn it in the app, or pass --train.", file=sys.stderr)
            return 1
    print("\n".join(evaluate(pieces, boards).lines()))
    return 0


def _analyse(config: Config, fen: str | None, seconds: float | None) -> int:
    import json
    import threading

    import chess

    from chesslens.chess.fen_pgn import parse_fen
    from chesslens.chess.validations import position_problems
    from chesslens.engine.stockfish_service import INSTALL_HINT, EngineService, EngineUnavailable, find_stockfish
    from chesslens.user_settings import load_user_settings

    try:
        board = parse_fen(fen) if fen else chess.Board()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if problems := position_problems(board):
        print("Not a legal position: " + " ".join(problems), file=sys.stderr)
        return 1
    if board.is_game_over():
        print("The game is over; there is nothing to analyse.", file=sys.stderr)
        return 1
    path = find_stockfish(config.engine.path, load_user_settings().get("engine_path"))
    if path is None:
        print(f"Stockfish not found. Install it with:\n  {INSTALL_HINT}", file=sys.stderr)
        return 1
    done = threading.Event()
    final: list = []

    def on_result(result) -> None:
        if result.final:
            final.append(result)
            done.set()

    cfg = config.engine
    service = EngineService(
        str(path),
        threads=cfg.threads,
        hash_mb=cfg.hash_mb,
        multipv=cfg.multipv,
        think_time_s=seconds or cfg.think_time_s,
        on_result=on_result,
    )
    try:
        service.start(wait=True)
        service.submit(1, board)
        if not done.wait(service.think_time_s + 15):
            print("The engine did not answer in time.", file=sys.stderr)
            return 1
    except EngineUnavailable as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        service.close()
    print(f"{service.engine_name} ({path})", file=sys.stderr)
    print(json.dumps(final[0].to_dict(), indent=2))
    return 0


def _list(config: Config) -> int:
    from chesslens.capture.window_capture import client_rect_on_screen, list_windows

    hint = config.capture.window_hint
    for w in list_windows(hint):
        _, _, cw, ch = client_rect_on_screen(w.hwnd)
        mark = "*" if w.match_rank(hint) < 2 else " "
        print(f"{mark} {w.hwnd:>10}  {w.process_name or '?':<24} {cw:>5}x{ch:<5} {w.title}")
    print(f"\n* matches window_hint {hint!r}")
    return 0


def _grab(config: Config, match: str | None, output: Path) -> int:
    import cv2

    from chesslens.capture.window_capture import WindowCaptureSource, find_window

    match = match or config.capture.window_hint
    window = find_window(match)
    if window is None:
        print(f"No window matches {match!r}. Run `chesslens list` to see what is open.", file=sys.stderr)
        return 1
    source = WindowCaptureSource(window, check_occlusion=config.capture.check_occlusion)
    source.start()
    try:
        image = source.read()
    finally:
        source.stop()
    print(f"{window.title} ({window.process_name or 'pid ' + str(window.pid)}): {source.status}")
    if image is None:
        return 2
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        print("PNG encoding failed", file=sys.stderr)
        return 1
    buf.tofile(output)
    print(f"saved {image.shape[1]}x{image.shape[0]} client area to {output}")
    return 0


def _gui(config: Config, window: str | None, video: Path | None) -> int:
    import shiboken6
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from chesslens.resources import resource_path
    from chesslens.ui.main_window import MainWindow

    if sys.platform == "win32":
        import ctypes

        # Group ChessLens under its own taskbar icon rather than Python's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChessLens.ChessLens")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ChessLens")
    app.setWindowIcon(QIcon(str(resource_path("assets/chesslens.ico"))))
    win = MainWindow(config, video=video, window_match=window)
    win.show()
    code = app.exec()
    # Destroy the window while the QApplication still exists: signal connections
    # keep it in a reference cycle, and Python's shutdown order is not guaranteed.
    shiboken6.delete(win)
    return code
