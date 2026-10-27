"""A tiny UCI engine for tests: legal moves from python-chess and predictable scores.

Checkmating moves rank first and score "mate 1"; other moves are ranked by
UCI string with centipawn scores (from the side to move, as UCI reports)
of 40 - 15 * rank + depth. A search deepens one ply every
FAKE_UCI_DEPTH_DELAY seconds (default 0.05) until its movetime, depth
FAKE_UCI_MAX_DEPTH (default 8), or "stop".

FAKE_UCI_LOG: append every received command to this file.
FAKE_UCI_CRASH_ONCE: path of a marker file; the first engine process to
receive "go" while it is missing creates it and exits abruptly.
"""

import os
import queue
import sys
import threading
import time

import chess

DELAY = float(os.environ.get("FAKE_UCI_DEPTH_DELAY", "0.05"))
MAX_DEPTH = int(os.environ.get("FAKE_UCI_MAX_DEPTH", "8"))
CRASH_ONCE = os.environ.get("FAKE_UCI_CRASH_ONCE")
LOG = os.environ.get("FAKE_UCI_LOG")


def out(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def ranked_moves(board: chess.Board) -> list[tuple[chess.Move, bool]]:
    moves = sorted(board.legal_moves, key=lambda m: m.uci())
    mates = []
    for move in moves:
        board.push(move)
        if board.is_checkmate():
            mates.append(move)
        board.pop()
    return [(m, True) for m in mates] + [(m, False) for m in moves if m not in mates]


def pv_from(board: chess.Board, first: chess.Move, length: int = 4) -> list[chess.Move]:
    probe = board.copy(stack=False)
    pv = [first]
    probe.push(first)
    while len(pv) < length and not probe.is_game_over():
        reply = min(probe.legal_moves, key=lambda m: m.uci())
        pv.append(reply)
        probe.push(reply)
    return pv


def search(board: chess.Board, multipv: int, movetime_s: float | None, commands: "queue.Queue[str]") -> None:
    ranked = ranked_moves(board)
    started = time.monotonic()
    depth = 0
    while depth < MAX_DEPTH:
        depth += 1
        for i, (move, mates) in enumerate(ranked[:multipv]):
            score = "mate 1" if mates else f"cp {40 - 15 * i + depth}"
            pv = " ".join(m.uci() for m in pv_from(board, move))
            elapsed = int((time.monotonic() - started) * 1000)
            out(f"info depth {depth} seldepth {depth} multipv {i + 1} score {score} nodes {depth * 1000} nps 100000 time {elapsed} pv {pv}")
        deadline = time.monotonic() + DELAY
        stopped = False
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                cmd = commands.get(timeout=remaining)
            except queue.Empty:
                break
            if cmd == "isready":
                out("readyok")
            elif cmd == "stop":
                stopped = True
                break
            elif cmd == "quit":
                sys.exit(0)
        if stopped or (movetime_s is not None and time.monotonic() - started >= movetime_s):
            break
    out(f"bestmove {ranked[0][0].uci()}")


def main() -> None:
    commands: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        for line in sys.stdin:
            if LOG:
                with open(LOG, "a", encoding="utf-8") as fh:
                    fh.write(line)
            commands.put(line.strip())
        commands.put("quit")

    threading.Thread(target=reader, daemon=True).start()
    board = chess.Board()
    multipv = 1
    while True:
        cmd = commands.get()
        if cmd == "uci":
            out("id name FakeFish 1.0")
            out("id author ChessLens tests")
            out("option name Threads type spin default 1 min 1 max 64")
            out("option name Hash type spin default 16 min 1 max 4096")
            out("option name MultiPV type spin default 1 min 1 max 10")
            out("uciok")
        elif cmd == "isready":
            out("readyok")
        elif cmd.startswith("setoption name MultiPV value"):
            multipv = int(cmd.rsplit(" ", 1)[1])
        elif cmd.startswith("position"):
            words = cmd.split()
            if words[1] == "startpos":
                board, rest = chess.Board(), words[2:]
            else:
                board, rest = chess.Board(" ".join(words[2:8])), words[8:]
            if rest and rest[0] == "moves":
                for uci in rest[1:]:
                    board.push_uci(uci)
        elif cmd.startswith("go"):
            if CRASH_ONCE and not os.path.exists(CRASH_ONCE):
                open(CRASH_ONCE, "w").close()
                os._exit(3)
            words = cmd.split()
            movetime = int(words[words.index("movetime") + 1]) / 1000 if "movetime" in words else None
            search(board, multipv, movetime, commands)
        elif cmd == "quit":
            return


if __name__ == "__main__":
    main()
