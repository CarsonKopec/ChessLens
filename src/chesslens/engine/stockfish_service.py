"""One local Stockfish (or other UCI) process that analyses only the newest submitted position (spec §6.5).

The engine runs on a worker thread. submit() replaces any waiting request and
stops a search in progress, so a result is only ever emitted for the latest
request; anything superseded is dropped, never shown (spec §5: an old result
must not overwrite a newer position).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

from chesslens.engine.analysis_cache import AnalysisCache, state_key
from chesslens.engine.results import AnalysisResult, build_result

log = logging.getLogger(__name__)

INSTALL_HINT = "winget install --id Stockfish.Stockfish --exact"


def default_threads() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def find_stockfish(*preferred: str | None) -> Path | None:
    """The first Stockfish executable found: explicit paths, then PATH, then winget's install folders.

    winget installs Stockfish as a portable zip and adds a link under
    %LOCALAPPDATA%\\Microsoft\\WinGet\\Links, which only reaches PATH for
    shells started afterwards, so that folder is searched directly too.
    """
    candidates: list[Path] = [Path(p) for p in preferred if p]
    if found := shutil.which("stockfish"):
        candidates.append(Path(found))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        candidates += sorted((winget / "Links").glob("stockfish*.exe"))
        candidates += sorted(winget.glob("Packages/Stockfish.Stockfish*/**/stockfish*.exe"))
    return next((c for c in candidates if c.is_file()), None)


class EngineUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class EngineStatus:
    state: str  # "starting", "ready", "thinking", "idle", "error", "closed"
    detail: str = ""


class EngineService:
    def __init__(
        self,
        command: str | list[str],
        *,
        threads: int = 0,
        hash_mb: int = 128,
        multipv: int = 3,
        think_time_s: float = 1.0,
        on_result: Callable[[AnalysisResult], None] | None = None,
        on_status: Callable[[EngineStatus], None] | None = None,
        cache: AnalysisCache | None = None,
        partial_interval_s: float = 0.2,
    ) -> None:
        self.command = command
        self.threads = threads or default_threads()
        self.hash_mb = hash_mb
        self.multipv = multipv
        self.think_time_s = think_time_s
        self.engine_name = ""
        self._on_result = on_result
        self._on_status = on_status
        self._cache = cache or AnalysisCache()
        self._partial_interval_s = partial_interval_s
        self._cond = threading.Condition()
        self._pending: tuple[int, int, chess.Board] | None = None  # (request id, version, board)
        self._request_id = 0  # bumps on every submit/cancel; a search for an older id is stale
        self._active: chess.engine.SimpleAnalysisResult | None = None
        self._closing = False
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None

    @property
    def settings(self) -> dict:
        """Search settings that change the answer; part of the cache key."""
        return {"multipv": self.multipv, "think_time_s": self.think_time_s, "threads": self.threads, "hash_mb": self.hash_mb}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, wait: bool = False, timeout: float = 15.0) -> None:
        """Launch the engine on the worker thread. With wait=True, block until it is ready or failed."""
        if self.running:
            return
        self._closing = False
        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()
        if wait:
            if not self._ready.wait(timeout):
                raise EngineUnavailable(f"{self._command_text()} did not start within {timeout:.0f}s")
            if self._start_error is not None:
                raise EngineUnavailable(str(self._start_error)) from self._start_error

    def submit(self, version: int, board: chess.Board) -> None:
        """Analyse `board` as position `version`, abandoning anything older.

        Refuses positions that are not legal or have no moves: only validated
        states may reach the engine (spec FR-07).
        """
        if not board.is_valid():
            raise ValueError(f"refusing to analyse an illegal position ({board.status()!r})")
        if board.is_game_over():
            raise ValueError("the game is over; there is nothing to analyse")
        if not self.running:
            self.start()
        with self._cond:
            self._request_id += 1
            self._pending = (self._request_id, version, board.copy(stack=False))
            active = self._active
            self._cond.notify_all()
        if active is not None:
            active.stop()

    def cancel(self) -> None:
        """Drop the waiting request and stop the current search; nothing more is emitted for them."""
        with self._cond:
            self._request_id += 1
            self._pending = None
            active = self._active
        if active is not None:
            active.stop()

    def close(self, timeout: float = 5.0) -> None:
        with self._cond:
            self._closing = True
            self._request_id += 1
            self._pending = None
            active = self._active
            self._cond.notify_all()
        if active is not None:
            active.stop()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    # -- worker --------------------------------------------------------------

    def _run(self) -> None:
        self._status("starting", self._command_text())
        try:
            engine = chess.engine.SimpleEngine.popen_uci(self.command, timeout=10.0)
        except Exception as exc:  # missing file, not a UCI engine, crashed during handshake
            self._start_error = exc
            self._ready.set()
            self._status("error", f"could not start {self._command_text()}: {exc}")
            return
        try:
            options = {k: v for k, v in {"Threads": self.threads, "Hash": self.hash_mb}.items() if k in engine.options}
            engine.configure(options)
            self.engine_name = engine.id.get("name", "UCI engine")
            self._ready.set()
            self._status("ready", self.engine_name)
            while True:
                with self._cond:
                    self._cond.wait_for(lambda: self._pending is not None or self._closing)
                    if self._closing:
                        break
                    request_id, version, board = self._pending
                    self._pending = None
                self._analyse(engine, request_id, version, board)
        except Exception as exc:
            if not self._ready.is_set():
                self._start_error = exc
                self._ready.set()
            log.warning("engine failed", exc_info=exc)
            self._status("error", f"{self.engine_name or 'the engine'} stopped: {exc}")
        finally:
            try:
                engine.quit()
            except Exception:
                engine.close()
        if self._closing:
            self._status("closed")

    def _analyse(self, engine: chess.engine.SimpleEngine, request_id: int, version: int, board: chess.Board) -> None:
        key = state_key(board, self.settings)
        cached = self._cache.get(key)
        if cached is not None:
            self._emit(request_id, cached.for_version(version))
            self._status("idle", self.engine_name)
            return
        self._status("thinking", f"position {version}")
        started = time.monotonic()
        last_partial = started
        limit = chess.engine.Limit(time=self.think_time_s)
        with engine.analysis(board, limit, multipv=self.multipv) as analysis:
            with self._cond:
                self._active = analysis
                stale = request_id != self._request_id
            if stale:
                analysis.stop()
            try:
                for _ in analysis:
                    now = time.monotonic()
                    if now - last_partial >= self._partial_interval_s:
                        last_partial = now
                        self._emit(request_id, self._result(version, board, analysis, False, started))
            finally:
                with self._cond:
                    self._active = None
        if not self._is_current(request_id):
            # Superseded or cancelled: incomplete, so neither shown nor cached.
            with self._cond:
                idle = self._pending is None
            if idle:
                self._status("idle", self.engine_name)
            return
        result = self._result(version, board, analysis, True, started)
        self._cache.put(key, result)
        self._emit(request_id, result)
        self._status("idle", self.engine_name)

    def _result(self, version, board, analysis, final, started) -> AnalysisResult:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return build_result(version, board, analysis.multipv, final, elapsed_ms, self.engine_name, self.settings)

    def _is_current(self, request_id: int) -> bool:
        with self._cond:
            return request_id == self._request_id

    def _emit(self, request_id: int, result: AnalysisResult) -> None:
        """Deliver a result unless its request was superseded.

        The check and the callback happen under the lock submit() takes, so
        once submit() returns no older result can come out. The callback must
        therefore be quick and must not wait on another thread that calls
        submit() (a queued Qt signal is fine).
        """
        if not result.lines or self._on_result is None:
            return
        with self._cond:
            if request_id != self._request_id:
                return
            try:
                self._on_result(result)
            except Exception:
                log.exception("on_result callback failed")

    def _status(self, state: str, detail: str = "") -> None:
        log.info("engine %s %s", state, detail)
        if self._on_status is not None:
            try:
                self._on_status(EngineStatus(state, detail))
            except Exception:
                log.exception("on_status callback failed")

    def _command_text(self) -> str:
        return self.command if isinstance(self.command, str) else " ".join(self.command)
