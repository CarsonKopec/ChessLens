"""Engine service and coordinator, against a scripted UCI engine (and real Stockfish when installed)."""

import sys
import threading
import time
from pathlib import Path

import chess
import chess.engine
import pytest
from conftest import wait_for

from chesslens.engine.analysis_cache import AnalysisCache, state_key
from chesslens.engine.coordinator import AnalysisCoordinator, AnalysisState
from chesslens.engine.results import AnalysisResult, build_result
from chesslens.engine.stockfish_service import EngineService, EngineUnavailable, find_stockfish

FAKE = [sys.executable, str(Path(__file__).with_name("fake_uci.py"))]
BACK_RANK_MATE = "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"  # Ra8#


class Recorder:
    def __init__(self):
        self.results: list[AnalysisResult] = []
        self.statuses = []
        self.lock = threading.Lock()

    def result(self, r):
        with self.lock:
            self.results.append(r)

    def status(self, s):
        self.statuses.append(s)

    def final(self, version):
        return next((r for r in self.results if r.final and r.position_version == version), None)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_UCI_LOG", str(tmp_path / "uci.log"))
    rec = Recorder()
    service = EngineService(FAKE, think_time_s=0.3, on_result=rec.result, on_status=rec.status, partial_interval_s=0.05)
    service.start(wait=True)
    yield service, rec, tmp_path / "uci.log"
    service.close()


def go_count(log: Path) -> int:
    return sum(1 for line in log.read_text().splitlines() if line.startswith("go"))


# -- results -------------------------------------------------------------------


def test_multipv_lines_with_san_pv_and_white_scores(engine):
    service, rec, _ = engine
    service.submit(1, chess.Board())
    result = wait_for(lambda: rec.final(1))
    assert service.engine_name == "FakeFish 1.0"
    assert [line.rank for line in result.lines] == [1, 2, 3]
    board = chess.Board()
    for line in result.lines:
        assert board.san(chess.Move.from_uci(line.uci)) == line.san
        assert line.pv_uci[0] == line.uci and line.pv_san.startswith(f"1. {line.san}")
    assert result.lines[0].score_cp_white == 40 + result.depth
    assert result.lines[1].score_cp_white == 25 + result.depth
    assert result.best.score_text == f"+{(40 + result.depth) / 100:.2f}"
    assert result.depth >= 3 and result.nodes > 0 and result.analysis_ms > 0


def test_scores_are_from_whites_point_of_view_when_black_moves(engine):
    service, rec, _ = engine
    board = chess.Board()
    board.push_san("e4")
    service.submit(1, board)
    result = wait_for(lambda: rec.final(1))
    assert result.best.score_cp_white == -(40 + result.depth)
    assert result.best.pv_san.startswith("1...")


def test_mate_is_reported_as_mate_not_centipawns(engine):
    service, rec, _ = engine
    service.submit(1, chess.Board(BACK_RANK_MATE))
    best = wait_for(lambda: rec.final(1)).best
    assert best.san == "Ra8#" and best.mate_white == 1 and best.score_cp_white is None
    assert best.score_text == "#1"


def test_partial_results_stream_before_the_final_one(engine):
    service, rec, _ = engine
    service.submit(1, chess.Board())
    wait_for(lambda: rec.final(1))
    flags = [r.final for r in rec.results]
    assert flags[0] is False and flags[-1] is True


def test_newer_position_supersedes_older_and_nothing_stale_follows(engine):
    service, rec, _ = engine
    service.think_time_s = 5.0
    service.submit(1, chess.Board())
    wait_for(lambda: rec.results)  # the first search is under way
    board = chess.Board()
    board.push_san("d4")
    service.submit(2, board)
    after = len(rec.results)
    started = time.monotonic()
    wait_for(lambda: any(r.position_version == 2 for r in rec.results[after:]), timeout=3)
    assert time.monotonic() - started < 2.0  # the old 5 s search was stopped, not waited out
    assert all(r.position_version == 2 for r in rec.results[after:])
    assert rec.final(1) is None


def test_cancel_stops_and_emits_nothing_more(engine):
    service, rec, _ = engine
    service.think_time_s = 5.0
    service.submit(1, chess.Board())
    wait_for(lambda: rec.results)
    service.cancel()
    after = len(rec.results)
    wait_for(lambda: rec.statuses[-1].state == "idle", timeout=2)
    time.sleep(0.2)
    assert len(rec.results) == after


def test_only_legal_unfinished_positions_reach_the_engine(engine):
    service, _, log = engine
    with pytest.raises(ValueError):
        service.submit(1, chess.Board("8/8/8/8/8/8/8/4K3 w - - 0 1"))  # no black king
    with pytest.raises(ValueError):
        service.submit(2, chess.Board("R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1"))  # checkmated
    assert go_count(log) == 0


def test_repeated_position_comes_from_the_cache(engine):
    service, rec, log = engine
    service.submit(1, chess.Board())
    first = wait_for(lambda: rec.final(1))
    other = chess.Board()
    other.push_san("c4")
    service.submit(2, other)
    wait_for(lambda: rec.final(2))
    service.submit(3, chess.Board())
    again = wait_for(lambda: rec.final(3))
    assert again.lines == first.lines
    assert go_count(log) == 2


def test_missing_engine_is_reported(tmp_path):
    rec = Recorder()
    service = EngineService(str(tmp_path / "nope.exe"), on_status=rec.status)
    with pytest.raises(EngineUnavailable):
        service.start(wait=True)
    assert rec.statuses[-1].state == "error"


def test_crashed_engine_restarts_on_next_position(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_UCI_CRASH_ONCE", str(tmp_path / "crashed"))
    rec = Recorder()
    service = EngineService(FAKE, think_time_s=0.2, on_result=rec.result, on_status=rec.status)
    try:
        service.submit(1, chess.Board())
        wait_for(lambda: any(s.state == "error" for s in rec.statuses))
        wait_for(lambda: not service.running)
        service.submit(2, chess.Board())
        assert wait_for(lambda: rec.final(2)).best is not None
    finally:
        service.close()


# -- result values ---------------------------------------------------------------


def test_result_schema_matches_spec():
    board = chess.Board()
    info = {
        "depth": 17,
        "nodes": 123,
        "multipv": 1,
        "score": chess.engine.PovScore(chess.engine.Cp(25), chess.WHITE),
        "pv": [chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5")],
    }
    result = build_result(42, board, [info, {"multipv": 2}], True, 700)
    assert result.to_dict() == {
        "position_version": 42,
        "fen": chess.STARTING_FEN,
        "analysis_ms": 700,
        "depth": 17,
        "lines": [{"rank": 1, "uci": "e2e4", "san": "e4", "score_cp_white": 25, "mate": None, "pv_uci": ["e2e4", "e7e5"]}],
    }


@pytest.mark.parametrize(
    "score, text",
    [(chess.engine.Cp(-150), "-1.50"), (chess.engine.Cp(0), "+0.00"), (chess.engine.Mate(-2), "#-2"), (chess.engine.Mate(3), "#3")],
)
def test_score_text(score, text):
    info = {"score": chess.engine.PovScore(score, chess.WHITE), "pv": [chess.Move.from_uci("e2e4")]}
    assert build_result(1, chess.Board(), [info], True, 0).best.score_text == text


def test_cache_key_covers_state_and_settings():
    board = chess.Board()
    cache = AnalysisCache(capacity=2)
    result = build_result(1, board, [], True, 0)
    cache.put(state_key(board, {"multipv": 3}), result)
    assert cache.get(state_key(board, {"multipv": 3})) is result
    assert cache.get(state_key(board, {"multipv": 1})) is None
    no_castling = board.copy()
    no_castling.castling_rights = 0
    assert cache.get(state_key(no_castling, {"multipv": 3})) is None


# -- coordinator ------------------------------------------------------------------


class FakeService:
    def __init__(self):
        self.submitted, self.cancels = [], 0

    def submit(self, version, board):
        self.submitted.append((version, board.fen()))

    def cancel(self):
        self.cancels += 1

    def close(self):
        pass


def make_coordinator():
    statuses = []
    coord = AnalysisCoordinator(on_status=statuses.append)
    engine = FakeService()
    coord.set_engine(engine)
    return coord, engine, statuses


def test_coordinator_versions_and_sends_legal_positions_only_when_enabled():
    coord, engine, statuses = make_coordinator()
    coord.set_position(chess.Board())
    assert engine.submitted == [] and statuses[-1].state is AnalysisState.OFF
    coord.set_enabled(True)
    assert engine.submitted == [(1, chess.STARTING_FEN)]
    assert coord.set_position(chess.Board("8/8/8/8/8/8/8/4K3 w - - 0 1")) == 2
    assert statuses[-1].state is AnalysisState.ILLEGAL and "Black has no king." in statuses[-1].detail
    assert len(engine.submitted) == 1 and engine.cancels >= 1


def test_coordinator_reports_game_over():
    coord, engine, statuses = make_coordinator()
    coord.set_enabled(True)
    coord.set_position(chess.Board("R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1"))
    assert statuses[-1].state is AnalysisState.GAME_OVER and statuses[-1].detail == "Checkmate: White wins."
    coord.set_position(chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"))
    assert statuses[-1].detail == "Stalemate: the game is drawn."
    assert engine.submitted == []


def test_coordinator_accepts_only_current_version_while_enabled():
    coord, _, _ = make_coordinator()
    coord.set_enabled(True)
    coord.set_position(chess.Board())
    old = build_result(coord.position_version, chess.Board(), [], True, 0)
    coord.set_position(chess.Board())
    assert not coord.accept(old)
    current = old.for_version(coord.position_version)
    assert coord.accept(current)
    coord.set_enabled(False)
    assert not coord.accept(current)


def test_coordinator_without_engine_says_so():
    statuses = []
    coord = AnalysisCoordinator(on_status=statuses.append)
    coord.set_enabled(True)
    assert statuses[-1].state is AnalysisState.NO_ENGINE


# -- discovery ----------------------------------------------------------------------


def test_find_stockfish_order(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert find_stockfish("") is None
    packaged = tmp_path / "Microsoft" / "WinGet" / "Packages" / "Stockfish.Stockfish_abc" / "stockfish" / "stockfish-windows-x86-64.exe"
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(b"")
    assert find_stockfish("") == packaged
    link = tmp_path / "Microsoft" / "WinGet" / "Links" / "stockfish.exe"
    link.parent.mkdir(parents=True)
    link.write_bytes(b"")
    assert find_stockfish("") == link
    chosen = tmp_path / "my-stockfish.exe"
    chosen.write_bytes(b"")
    assert find_stockfish(str(tmp_path / "missing.exe"), str(chosen)) == chosen


# -- real Stockfish -----------------------------------------------------------------

REAL = find_stockfish()


@pytest.mark.skipif(REAL is None, reason="Stockfish is not installed")
def test_real_stockfish_finds_mate_and_three_lines():
    rec = Recorder()
    service = EngineService(str(REAL), think_time_s=0.5, on_result=rec.result)
    try:
        service.start(wait=True)
        assert "Stockfish" in service.engine_name
        service.submit(1, chess.Board(BACK_RANK_MATE))
        best = wait_for(lambda: rec.final(1), timeout=10).best
        assert best.san == "Ra8#" and best.mate_white == 1
        service.submit(2, chess.Board())
        result = wait_for(lambda: rec.final(2), timeout=10)
        assert len(result.lines) == 3 and abs(result.best.score_cp_white) < 100
    finally:
        service.close()
