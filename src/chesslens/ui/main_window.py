"""Dashboard: pick the mirroring window, find the board, correct the position, analyse it (spec §4.1, §6.2, §6.5, §6.7)."""

from __future__ import annotations

import itertools
import json
import logging
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import chess
import chess.pgn
import cv2
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from chesslens.capture.base import Frame, FrameSource, SourceState, SourceStatus
from chesslens.capture.manager import MAX_FPS, MIN_FPS, CaptureManager
from chesslens.capture.recorded_video import VideoFileSource
from chesslens.capture.roi import Roi, auto_trim
from chesslens.capture.window_capture import WindowCaptureSource, WindowInfo, list_windows
from chesslens import __version__
from chesslens.chess.tracker import GameTracker, TransitionKind
from chesslens.config import Config
from chesslens.diagnostics import TrackingLog
from chesslens.domain.models import Orientation
from chesslens.engine.coordinator import AnalysisCoordinator, AnalysisStatus
from chesslens.engine.results import AnalysisResult
from chesslens.engine.stockfish_service import EngineService, EngineStatus, find_stockfish
from chesslens.gamebar import GameBarFeed
from chesslens.persistence.sqlite_store import SessionStore, StoredMove, sessions_path
from chesslens.resources import resource_path
from chesslens.ui.analysis_panel import AnalysisPanel
from chesslens.ui.game_panel import GamePanel, move_list_html
from chesslens.ui.position_panel import PositionPanel
from chesslens.ui.preview_widget import PreviewWidget
from chesslens.ui.settings_dialog import PrivacyDialog
from chesslens.user_settings import load_user_settings, privacy_setting, save_user_settings
from chesslens.vision.board_detector import detect_board
from chesslens.vision.board_reader import BoardReader, ReaderSetup
from chesslens.vision.board_warp import screen_cell_to_square, square_to_screen_cell
from chesslens.vision.calibration import BoardCalibration, guess_orientation
from chesslens.vision.piece_classifier import CLASSES, NO_BOARD, BoardObservation, read_board
from chesslens.vision.piece_set import EMPTY, PieceSet, list_piece_sets, occupancy_mismatches, pieces_dir

log = logging.getLogger(__name__)

AUTO_DETECT_INTERVAL_S = 1.0
DEFAULT_PIECE_SET = "Chess.com"
# A square the reading wasn't sure about is learned from only if it gave the confirmed answer at least
# this much probability: a close call between plausible pieces, not something else entirely. Unreadable
# squares need more, since overlays land there; real overlays put their probability on "unrecognised".
MIN_LEARN_PROBABILITY = 0.2
MIN_LEARN_PROBABILITY_UNREADABLE = 0.25


ABOUT_TEXT = (
    "<h3>ChessLens {version}</h3>"
    "<p>Reads a chess app mirrored from your phone, follows the game, and analyses it with Stockfish, "
    "all on this computer. Nothing is uploaded.</p>"
    "<p><b>Fair play:</b> use engine help only where it's allowed: study, puzzles, games against the "
    "computer, or reviewing finished games.</p>"
    "<p>Stockfish (GPL-3.0) is a separate program you install yourself. ChessLens uses PySide6/Qt "
    "(LGPL-3.0), python-chess (GPL-3.0), OpenCV (Apache-2.0), NumPy (BSD-3-Clause), mss (MIT) and "
    "pywin32 (PSF); their licence texts are in the <i>licenses</i> folder of an installed copy.</p>"
)

FAIR_PLAY_REMINDER = (
    "Only use engine suggestions where engine help is allowed: studying, puzzles, games against "
    "the computer, or reviewing games that are over.\n\n"
    "Using them during a live or rated game against another person breaks Chess.com's fair-play "
    "rules, and those of other chess sites."
)


class _Bridge(QObject):
    """Carries capture- and engine-thread callbacks onto the GUI thread (cross-thread signals are queued)."""

    frame = Signal(int, object)
    status = Signal(int, object)
    engine_result = Signal(object)
    engine_status = Signal(object)
    observation = Signal(object)


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: Config,
        video: Path | None = None,
        window_match: str | None = None,
        engine_command: str | list[str] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("ChessLens")
        self.resize(1560, 860)
        self._config = config
        self._engine_command = engine_command  # overrides discovery (tests, or a non-Stockfish engine)
        self._fair_play_acknowledged = False
        self._hint = window_match or config.capture.window_hint
        self._videos: list[Path] = [video] if video else []
        self._manager: CaptureManager | None = None
        self._generation = 0  # bumps on every start/stop so late signals from an old source are ignored
        self._roi: Roi | None = None
        self._last_frame: Frame | None = None
        self._calibration: BoardCalibration | None = None
        self._calibration_versions = itertools.count(1)
        self._orientation = Orientation.WHITE_BOTTOM
        self._auto_trim_pending = False
        self._auto_detect = True  # off after "Clear board" until the user asks again
        self._last_detect_at = float("-inf")
        self._pieces: PieceSet | None = None
        self._tracker: GameTracker | None = None
        self._unexplained: BoardObservation | None = None  # the last reading the tracker couldn't explain
        self._screen_paused = False  # following, but the source stopped giving usable frames
        self._tracking_log = TrackingLog()  # recent readings while following; saved only on request
        self._store: SessionStore | None = None  # opt-in game history, open only while turned on
        self._stored_game: chess.pgn.Game | None = None
        self._stored_game_id: int | None = None

        self._bridge = _Bridge()
        self._bridge.frame.connect(self._on_frame)
        self._bridge.status.connect(self._on_status)
        self._bridge.engine_result.connect(self._on_engine_result)
        self._bridge.engine_status.connect(self._on_engine_status)
        self._bridge.observation.connect(self._on_observation)
        self._reader = BoardReader(on_observation=self._bridge.observation.emit)
        self._reader.start()

        # Source row
        self._source_combo = QComboBox()
        # Long window titles must not widen the whole window.
        self._source_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._source_combo.setMinimumContentsLength(36)
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        refresh_btn = QPushButton("&Refresh")
        refresh_btn.setToolTip("Re-scan open windows")
        refresh_btn.clicked.connect(self._populate_sources)
        video_btn = QPushButton("Open &video…")
        video_btn.setToolTip("Replay a screen recording instead of a live window")
        video_btn.clicked.connect(self._open_video)
        self._start_btn = QPushButton("&Start capture")
        self._start_btn.clicked.connect(self._toggle_capture)
        self._pause_box = QCheckBox("&Pause")
        self._pause_box.toggled.connect(self._on_pause)
        self._fps_spin = QDoubleSpinBox()
        self._fps_spin.setRange(MIN_FPS, MAX_FPS)
        self._fps_spin.setDecimals(1)
        self._fps_spin.setSuffix(" fps")
        self._fps_spin.setValue(config.capture.fps)
        self._fps_spin.valueChanged.connect(self._on_fps)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Mirroring window:"))
        source_row.addWidget(self._source_combo, 1)
        source_row.addWidget(refresh_btn)
        source_row.addWidget(video_btn)
        source_row.addSpacing(12)
        source_row.addWidget(self._fps_spin)
        source_row.addWidget(self._pause_box)
        source_row.addWidget(self._start_btn)

        # Board row
        detect_btn = QPushButton("&Detect board")
        detect_btn.setToolTip("Find the chessboard in the current frame")
        detect_btn.clicked.connect(lambda: self._detect_now())
        clear_board_btn = QPushButton("Clear board")
        clear_board_btn.setToolTip("Forget the board position and stop finding it automatically")
        clear_board_btn.clicked.connect(self._clear_board)
        self._orientation_combo = QComboBox()
        for o in Orientation:
            self._orientation_combo.addItem(o.label, o)
        self._orientation_combo.setToolTip("Which side's pieces are at the bottom of the phone screen")
        self._orientation_combo.currentIndexChanged.connect(
            lambda: self._set_orientation(self._orientation_combo.currentData())
        )
        flip_btn = QPushButton("&Flip")
        flip_btn.clicked.connect(lambda: self._set_orientation(self._orientation.flipped()))
        trim_btn = QPushButton("&Auto-trim view")
        trim_btn.setToolTip("Zoom the preview to the phone screen by cutting solid bars around it")
        trim_btn.clicked.connect(self._auto_trim)
        clear_crop_btn = QPushButton("&Whole window")
        clear_crop_btn.setToolTip("Show the whole mirroring window")
        clear_crop_btn.clicked.connect(lambda: self._set_roi(None))
        save_btn = QPushButton("Save labeled board…")
        save_btn.setToolTip("Save the straightened board image and its FEN, for building a recognition test set")
        save_btn.clicked.connect(self._save_labeled_board)

        board_row = QHBoxLayout()
        board_row.addWidget(QLabel("Board:"))
        board_row.addWidget(detect_btn)
        board_row.addWidget(clear_board_btn)
        board_row.addSpacing(12)
        board_row.addWidget(self._orientation_combo)
        board_row.addWidget(flip_btn)
        board_row.addSpacing(12)
        board_row.addWidget(trim_btn)
        board_row.addWidget(clear_crop_btn)
        board_row.addStretch(1)
        board_row.addWidget(save_btn)

        # Recognition row (spec §6.3)
        self._pieces_combo = QComboBox()
        self._pieces_combo.setEditable(True)
        self._pieces_combo.setToolTip("Which learned piece set to recognise; type a new name to learn another")
        self._pieces_combo.setMinimumContentsLength(14)
        self._pieces_combo.addItems(list_piece_sets() or [DEFAULT_PIECE_SET])
        self._pieces_combo.textActivated.connect(self._load_piece_set)
        learn_btn = QPushButton("&Learn pieces")
        learn_btn.setToolTip("Learn what the pieces look like from the board on screen, using the editor's position as the answer")
        learn_btn.clicked.connect(self._learn_pieces)
        read_btn = QPushButton("&Read board")
        read_btn.setToolTip("Read the pieces on screen into the editor")
        read_btn.clicked.connect(self._read_board_now)
        self._follow_box = QCheckBox("F&ollow the game")
        self._follow_box.setToolTip(
            "Follow the game on screen move by move, starting from the editor's position: "
            "each new position must be explained by legal moves"
        )
        self._follow_box.toggled.connect(self._on_follow_toggled)
        self._recognition_label = QLabel()
        recognition_row = QHBoxLayout()
        recognition_row.addWidget(QLabel("Pieces:"))
        recognition_row.addWidget(self._pieces_combo)
        recognition_row.addWidget(learn_btn)
        recognition_row.addSpacing(12)
        recognition_row.addWidget(read_btn)
        recognition_row.addWidget(self._follow_box)
        recognition_row.addSpacing(12)
        recognition_row.addWidget(self._recognition_label, 1)

        help_label = QLabel(
            "Drag on the preview to mark the board, drag its corner dots to adjust it, "
            "and click a square there or on the board at right to correct it."
        )
        help_label.setStyleSheet("color: #808080;")

        self._preview = PreviewWidget()
        self._preview.board_drawn.connect(self._on_board_drawn)
        self._preview.corner_moved.connect(self._on_corner_moved)
        self._preview.square_clicked.connect(self._on_preview_square)
        self._panel = PositionPanel()
        self._panel.setMinimumWidth(400)
        self._panel.position_changed.connect(self._on_position_changed)
        self._panel.edited_by_user.connect(self._on_user_edit)
        self._analysis = AnalysisPanel(config.engine.think_time_s, config.engine.multipv)
        self._analysis.enabled_toggled.connect(self._on_analysis_toggled)
        self._analysis.think_time_changed.connect(self._on_think_time)
        self._analysis.locate_requested.connect(self._locate_engine)
        self._gamebar = GameBarFeed()  # the Xbox Game Bar widget's copy of the analysis, if it is installed
        self._coordinator = AnalysisCoordinator(on_status=self._on_analysis_status)
        self._game_panel = GamePanel()
        self._game_panel.undo_requested.connect(self._undo_move)
        self._game_panel.new_game_requested.connect(self._new_game)
        self._game_panel.copy_pgn_requested.connect(self._copy_pgn)
        self._game_panel.save_pgn_requested.connect(self._save_pgn)
        self._game_panel.use_screen_requested.connect(self._use_screen_position)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self._analysis)
        right.addWidget(self._game_panel)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        right.setMinimumWidth(320)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._preview)
        splitter.addWidget(self._panel)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)

        layout = QVBoxLayout()
        layout.addLayout(source_row)
        layout.addLayout(board_row)
        layout.addLayout(recognition_row)
        layout.addWidget(help_label)
        layout.addWidget(splitter, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self._capture_lamp = QLabel()
        self._state_label = QLabel()
        self._board_label = QLabel()
        self._size_label = QLabel()
        self._fps_label = QLabel()
        bar = self.statusBar()
        bar.addWidget(self._capture_lamp)
        bar.addWidget(self._state_label, 1)
        bar.addPermanentWidget(self._board_label)
        bar.addPermanentWidget(self._size_label)
        bar.addPermanentWidget(self._fps_label)

        self._readout_timer = QTimer(self)
        self._readout_timer.timeout.connect(self._refresh_readouts)
        self._readout_timer.start(500)
        self._gamebar_timer = QTimer(self)
        self._gamebar_timer.timeout.connect(self._gamebar.tick)
        self._gamebar_timer.start(250)
        self._build_menus()
        self._prune_history()

        self._populate_sources()
        if video:
            self._source_combo.setCurrentIndex(self._source_combo.findData(video))
        self._coordinator.set_position(self._panel.board)
        self._load_piece_set(self._pieces_combo.currentText())
        self._refresh_readouts()

    # -- sources -------------------------------------------------------------

    def _populate_sources(self) -> None:
        combo = self._source_combo
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for window in list_windows(self._hint):
            combo.addItem(window.label, window)
        for path in self._videos:
            combo.addItem(f"Video file: {path.name}", path)
        index = self._find_source(previous) if previous is not None else -1
        combo.setCurrentIndex(max(index, 0))
        combo.blockSignals(False)
        if index < 0 and self._manager is not None:
            # Never silently start capturing a different app because the chosen one closed.
            self._stop_capture()
            self._preview.set_message("The selected window is gone. Pick a window and press Start capture.")

    def _find_source(self, data: object) -> int:
        """Index of the same source after a refresh: same window handle, else same app's window, else -1."""
        combo = self._source_combo
        items = [combo.itemData(i) for i in range(combo.count())]
        if isinstance(data, Path):
            return items.index(data) if data in items else -1
        if not isinstance(data, WindowInfo):
            return -1
        for i, item in enumerate(items):
            if isinstance(item, WindowInfo) and item.hwnd == data.hwnd:
                return i
        for i, item in enumerate(items):
            if isinstance(item, WindowInfo) and (item.process_name, item.class_name) == (data.process_name, data.class_name):
                return i
        return -1

    def _open_video(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Open screen recording", "", "Video (*.mp4 *.mov *.avi *.mkv);;All files (*)")
        if not name:
            return
        path = Path(name)
        if path not in self._videos:
            self._videos.append(path)
            self._source_combo.addItem(f"Video file: {path.name}", path)
        self._source_combo.setCurrentIndex(self._source_combo.findData(path))

    def _make_source(self) -> FrameSource | None:
        data = self._source_combo.currentData()
        if isinstance(data, WindowInfo):
            return WindowCaptureSource(data, check_occlusion=self._config.capture.check_occlusion)
        if isinstance(data, Path):
            return VideoFileSource(data, loop=True, realtime=True)
        return None

    def _on_source_changed(self) -> None:
        # A different source has its own geometry: forget the crop and board, and restart capture so
        # no frame, board position or reading from the old source lingers (FR-03).
        self._set_roi(None)
        self._auto_detect = True
        self._set_calibration(None)
        self._last_frame = None
        if self._manager is not None:
            self._stop_capture()
            self._start_capture()

    # -- capture lifecycle ---------------------------------------------------

    def _toggle_capture(self) -> None:
        if self._manager is None:
            self._start_capture()
        else:
            self._stop_capture()

    def _start_capture(self) -> None:
        source = self._make_source()
        if source is None:
            self._preview.clear("No window selected. Open the mirroring app, then press Refresh.")
            return
        self._generation += 1
        gen = self._generation
        cfg = self._config.capture
        self._manager = CaptureManager(
            source,
            fps=self._fps_spin.value(),
            on_frame=lambda frame: self._bridge.frame.emit(gen, frame),
            on_status=lambda status: self._bridge.status.emit(gen, status),
            initial_backoff=cfg.initial_backoff_s,
            max_backoff=cfg.max_backoff_s,
        )
        self._manager.roi = self._roi
        self._manager.paused = self._pause_box.isChecked()
        self._manager.start()
        self._last_frame = None
        self._auto_trim_pending = self._roi is None
        self._auto_detect = True
        self._last_detect_at = float("-inf")
        self._preview.clear("Waiting for the first frame…")
        self._start_btn.setText("&Stop capture")
        self._refresh_readouts()

    def _stop_capture(self) -> None:
        if self._manager is None:
            return
        self._generation += 1
        self._manager.stop()
        self._manager = None
        self._preview.set_message("Capture stopped.")
        self._state_label.setText("Stopped")
        self._start_btn.setText("&Start capture")
        self._refresh_readouts()

    def _on_frame(self, gen: int, frame: Frame) -> None:
        if gen != self._generation:
            return
        self._last_frame = frame
        if self._auto_trim_pending:
            self._auto_trim_pending = False
            if self._roi is None and (roi := auto_trim(frame.image)) is not None:
                self._set_roi(roi)
        self._track_board(frame)
        self._preview.show_frame(frame.image)
        self._reader.offer(frame)

    def _track_board(self, frame: Frame) -> None:
        """Find the board when there is none yet or the window changed size; otherwise keep it (spec §6.2)."""
        cal = self._calibration
        if cal is not None and cal.frame_size == frame.size:
            return
        if not self._auto_detect and cal is None:
            return
        now = time.monotonic()
        if now - self._last_detect_at < AUTO_DETECT_INTERVAL_S:
            return
        self._last_detect_at = now
        if self._detect(frame) or cal is None:
            return
        self._set_calibration(cal.rescaled(frame.size))
        self.statusBar().showMessage("The window changed size; check that the board grid still fits.", 8000)

    def _on_status(self, gen: int, status: SourceStatus) -> None:
        if gen != self._generation:
            return
        self._state_label.setText(str(status))
        if status.ok:
            self._preview.set_message("Paused." if self._pause_box.isChecked() else "")
        elif status.state is not SourceState.IDLE:
            self._preview.set_message(f"No usable frames: {status.detail or status.state.value}.")
        self._follow_through_outage(status)

    def _follow_through_outage(self, status: SourceStatus) -> None:
        """While following, a lost screen pauses tracking and keeps the game; when it comes back the board
        is read again and checked against the game, which reconstructs up to two missed moves (spec §6.4, §12)."""
        if not self._following() or status.state is SourceState.IDLE:
            return
        if not status.ok and not self._screen_paused:
            self._screen_paused = True
            self._game_panel.show_notice(
                f"The screen can't be read ({status.detail or status.state.value}). "
                "Following is paused; the game so far is kept.",
                "waiting",
            )
        elif status.ok and self._screen_paused:
            self._screen_paused = False
            self._game_panel.show_notice("The screen is back. Checking the board against the game…")
            self._configure_reader()

    def _on_pause(self, paused: bool) -> None:
        if self._manager is not None:
            self._manager.paused = paused
            self._preview.set_message("Paused." if paused else "")
        self._refresh_readouts()

    def _on_fps(self, value: float) -> None:
        if self._manager is not None:
            self._manager.fps = value

    # -- view crop -----------------------------------------------------------

    def _set_roi(self, roi: Roi | None) -> None:
        self._roi = roi
        if self._manager is not None:
            self._manager.roi = roi
        self._preview.set_view(roi)
        self._refresh_readouts()

    def _auto_trim(self) -> None:
        if self._last_frame is None:
            self.statusBar().showMessage("Start capture first.", 3000)
            return
        roi = auto_trim(self._last_frame.image)
        if roi is None:
            self.statusBar().showMessage("No solid borders found to trim.", 3000)
        else:
            self._set_roi(roi)

    # -- board calibration ---------------------------------------------------

    def _detect(self, frame: Frame) -> bool:
        region = self._roi.to_pixels(*frame.size) if self._roi else None
        found = detect_board(frame.image, region=region)
        if found is None:
            return False
        self._set_calibration(found)
        orientation, confidence = guess_orientation(found.warp(frame.image))
        note = ""
        if orientation is not None and confidence >= 0.3:
            self._set_orientation(orientation)
            note = f" Looks like {orientation.label.lower()}; use Flip if not."
        self.statusBar().showMessage(f"Board found ({found.confidence:.0%} of squares match).{note}", 8000)
        return True

    def _detect_now(self) -> None:
        if self._last_frame is None:
            self.statusBar().showMessage("Start capture first.", 3000)
            return
        self._auto_detect = True
        if not self._detect(self._last_frame):
            self.statusBar().showMessage("No board found. Drag a rectangle around it on the preview.", 8000)

    def _clear_board(self) -> None:
        self._auto_detect = False
        self._set_calibration(None)

    def _set_calibration(self, calibration: BoardCalibration | None) -> None:
        if calibration is not None:
            calibration = replace(calibration, version=next(self._calibration_versions))
        self._calibration = calibration
        self._preview.set_board(calibration.corner_array if calibration else None, self._orientation)
        self._configure_reader()
        self._refresh_readouts()

    def _on_board_drawn(self, rect: tuple[float, float, float, float]) -> None:
        if self._last_frame is None:
            return
        self._set_calibration(BoardCalibration.from_rect(*rect, frame_size=self._last_frame.size))

    def _on_corner_moved(self, index: int, point: tuple[float, float]) -> None:
        if self._calibration is None:
            return
        try:
            self._set_calibration(self._calibration.with_corner(index, point))
        except ValueError:
            pass  # would fold the board over; keep the last good shape

    def _set_orientation(self, orientation: Orientation) -> None:
        self._orientation = orientation
        self._orientation_combo.blockSignals(True)
        self._orientation_combo.setCurrentIndex(self._orientation_combo.findData(orientation))
        self._orientation_combo.blockSignals(False)
        self._panel.set_orientation(orientation)
        self._analysis.set_orientation(orientation)
        self._preview.set_board(self._calibration.corner_array if self._calibration else None, orientation)
        self._configure_reader()

    def _on_preview_square(self, row: int, col: int, button: Qt.MouseButton) -> None:
        self._panel.apply_tool(screen_cell_to_square(row, col, self._orientation), button)

    def _save_labeled_board(self) -> None:
        frame, cal = self._last_frame, self._calibration
        if frame is None or cal is None:
            self.statusBar().showMessage("Capture a frame and mark the board first.", 4000)
            return
        stamp = datetime.fromtimestamp(frame.wall_time).strftime("%Y%m%d-%H%M%S")
        name, _ = QFileDialog.getSaveFileName(self, "Save labeled board", f"board-{stamp}.png", "PNG image (*.png)")
        if not name:
            return
        png = Path(name).with_suffix(".png")
        ok, buf = cv2.imencode(".png", cal.warp(frame.image))
        if not ok:
            self.statusBar().showMessage("Could not encode the board image.", 4000)
            return
        buf.tofile(png)  # cv2.imwrite cannot handle non-ASCII Windows paths
        label = {
            "fen": self._panel.fen(),
            "orientation": self._orientation.value,
            "captured_at": datetime.fromtimestamp(frame.wall_time).isoformat(timespec="seconds"),
            "source": frame.source_id,
            "frame_size": list(frame.size),
            "corners": [list(c) for c in cal.corners],
            "calibration": {"method": cal.method, "confidence": cal.confidence, "version": cal.version},
        }
        png.with_suffix(".json").write_text(json.dumps(label, indent=2), encoding="utf-8")
        self.statusBar().showMessage(f"Saved {png.name} and {png.with_suffix('.json').name}", 6000)

    # -- readouts ------------------------------------------------------------

    def _refresh_readouts(self) -> None:
        # Always-visible capture indicator (spec §6.7 privacy UX, FR-15).
        if self._manager is None:
            self._capture_lamp.setText("○ Not capturing")
            self._capture_lamp.setStyleSheet("padding: 2px 8px;")
        elif self._pause_box.isChecked():
            self._capture_lamp.setText("❚❚ Capture paused")
            self._capture_lamp.setStyleSheet("padding: 2px 8px; background: #8a6d00; color: white;")
        elif isinstance(self._manager.source, VideoFileSource):
            self._capture_lamp.setText("▶ Playing video file")
            self._capture_lamp.setStyleSheet("padding: 2px 8px; background: #1f5f99; color: white;")
        else:
            self._capture_lamp.setText("● CAPTURING SCREEN")
            self._capture_lamp.setStyleSheet("padding: 2px 8px; background: #b3261e; color: white; font-weight: bold;")

        cal = self._calibration
        if cal is None:
            self._board_label.setText("Board: not set")
        elif cal.method == "auto":
            self._board_label.setText(f"Board: found automatically, {cal.confidence:.0%} match (v{cal.version})")
        else:
            self._board_label.setText(f"Board: set by hand (v{cal.version})")

        if self._last_frame is None:
            self._size_label.setText("")
        else:
            w, h = self._last_frame.size
            self._size_label.setText(f"{w}×{h}")
        fps = self._manager.measured_fps if self._manager is not None else 0.0
        self._fps_label.setText(f"{fps:.1f} fps" if self._manager is not None else "")

    # -- piece recognition ---------------------------------------------------

    def _load_piece_set(self, name: str) -> None:
        name = name.strip() or DEFAULT_PIECE_SET
        try:
            self._pieces = PieceSet.load(name)
        except FileNotFoundError:
            self._pieces = None
        except (OSError, ValueError) as exc:
            self._pieces = None
            self.statusBar().showMessage(f"Couldn't load piece set '{name}': {exc}", 8000)
        self._configure_reader()
        self._show_recognition_state()

    def _configure_reader(self, reread: bool = True) -> None:
        """Hand the reader the current board geometry, pieces and orientation. With reread, the board is
        read again as it is now; without (an improved piece set), it carries on with the next change."""
        if reread:
            self._preview.set_uncertain([])
        if self._calibration is None or self._pieces is None:
            self._reader.configure(None)
        else:
            self._reader.configure(ReaderSetup(self._calibration, self._pieces, self._orientation), reset=reread)

    def _show_recognition_state(self, text: str | None = None) -> None:
        if text is None:
            pieces = self._pieces
            if pieces is None:
                text = (
                    f"'{self._pieces_combo.currentText()}' isn't learned yet: show the start position on the "
                    "phone and in the editor, then press Learn pieces."
                )
            elif pieces.missing:
                text = f"'{pieces.name}': {pieces.example_count} examples; can't recognise {' '.join(pieces.missing)} yet."
            else:
                text = f"'{pieces.name}': {pieces.example_count} examples learned."
        self._recognition_label.setText(text)

    def _learn_pieces(self) -> None:
        frame, cal = self._last_frame, self._calibration
        if frame is None or cal is None:
            self.statusBar().showMessage("Capture the board and mark it first.", 5000)
            return
        name = self._pieces_combo.currentText().strip() or DEFAULT_PIECE_SET
        board_image = cal.warp(frame.image)
        placement = self._panel.board
        mismatches = occupancy_mismatches(board_image, placement, self._orientation)
        if mismatches and not self._confirm_mismatch(mismatches):
            return
        if not self._confirm_learning(pieces_dir()):
            return
        pieces = self._pieces if self._pieces is not None and self._pieces.name == name else PieceSet(name)
        added = pieces.learn(board_image, placement, self._orientation)
        pieces.save()
        self._pieces = pieces
        if self._pieces_combo.findText(name) < 0:
            self._pieces_combo.addItem(name)
        self._pieces_combo.setCurrentText(name)
        self._configure_reader()
        self._show_recognition_state()
        if added:
            self.statusBar().showMessage(f"Learned {added} new example{'' if added == 1 else 's'} for '{name}'.", 6000)
        else:
            self.statusBar().showMessage(f"Nothing new: '{name}' already knew every piece on this board.", 6000)

    def _confirm_mismatch(self, squares: list[chess.Square]) -> bool:
        names = ", ".join(chess.square_name(s) for s in squares)
        answer = QMessageBox.question(
            self,
            "Editor doesn't match the screen",
            f"The editor's position doesn't seem to match the board on screen at {names}.\n\n"
            "Learning from a wrong position teaches ChessLens the wrong pieces. Learn anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_learning(self, folder: Path) -> bool:
        """Consent before keeping any images (spec §6.3)."""
        answer = QMessageBox.question(
            self,
            "Learn pieces",
            "ChessLens will learn what your pieces look like from the board on screen, taking the "
            "editor's position as the answer.\n\nWhile it follows a game, it also keeps learning: when it "
            "wasn't sure about a square and the game then confirms what's on it, that square is added as "
            "another example (e.g. a piece first seen on a dark square).\n\nSmall images of the squares are "
            f"saved on this computer only, in:\n{folder}\n\nNothing is uploaded.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _read_board_now(self) -> None:
        frame, cal = self._last_frame, self._calibration
        if frame is None or cal is None:
            self.statusBar().showMessage("Capture the board and mark it first.", 5000)
            return
        if self._pieces is None:
            self.statusBar().showMessage("Learn the pieces first.", 5000)
            return
        observation = read_board(cal.warp(frame.image), self._pieces, self._orientation, cal.version, frame.seq)
        self._apply_observation(observation, into_editor=True, asked=True)

    def _on_follow_toggled(self, on: bool) -> None:
        if on:
            board = self._panel.board
            if board.fen() != chess.STARTING_FEN and not self._confirm_tracking_start(board):
                self._follow_box.blockSignals(True)
                self._follow_box.setChecked(False)
                self._follow_box.blockSignals(False)
                return
            if self._tracker is None:
                self._tracker = GameTracker(board)
            else:
                self._tracker.start(board)  # the previous game stays in the session's PGN
            self._game_panel.show_notice(None)
            self._show_game()
            self._configure_reader()  # read the board as it is now, not just after the next change
        self._game_panel.set_following(on)
        if not on and self._tracker is not None:
            self._show_game()

    def _confirm_tracking_start(self, board: chess.Board) -> bool:
        """A screenshot doesn't show side to move, castling or en passant, so confirm them (spec §6.4)."""
        rights = board.castling_xfen() if board.castling_rights else "none"
        ep = chess.square_name(board.ep_square) if board.ep_square is not None and board.has_legal_en_passant() else "none"
        answer = QMessageBox.question(
            self,
            "Follow from this position?",
            "ChessLens will follow the game from the position in the editor:\n\n"
            f"{'White' if board.turn == chess.WHITE else 'Black'} to move\nCastling: {rights}\nEn passant: {ep}\n\n"
            "A screenshot can't show these, so check them before you start.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _following(self) -> bool:
        return self._follow_box.isChecked() and self._tracker is not None

    def _track(self, observation: BoardObservation) -> None:
        """Explain a still reading by the game so far (spec §6.4); only clear explanations change it."""
        tracker = self._tracker
        if observation.unlearned:
            self._show_recognition_state(" ".join(observation.placement_problems()))
            return
        side_hint = observation.side_to_move_hint()
        t = tracker.observe(
            observation.placement(),
            observation.unreadable_squares(),
            observation.half_sure_squares(),
            last_move=observation.last_move_squares(),
            last_mover=None if side_hint is None else not side_hint,
        )
        kind = t.kind
        self._tracking_log.add(observation, t, tracker.board.fen())
        confirmed = (TransitionKind.MOVES, TransitionKind.NAVIGATED, TransitionKind.NO_CHANGE, TransitionKind.NEW_GAME)
        if kind in confirmed and not t.flipped:  # a reading seen the wrong way round must never be learned
            self._learn_from_confirmed(observation, tracker.board)
        if kind is TransitionKind.MOVES:
            self._unexplained = None
            self._record_history_moves(observation, len(t.sans))
            first_ply = tracker.node.ply() - len(t.sans) + 1
            played = " ".join(move_list_html([(first_ply + i, san, False) for i, san in enumerate(t.sans)]).split())
            caught_up = f"{len(t.sans)} moves came faster than the screen settled: {played}. {t.detail}".strip()
            self._game_panel.show_notice((t.detail or None) if len(t.sans) == 1 else caught_up)
            self._show_recognition_state(f"Played {played}.")
        elif kind is TransitionKind.NAVIGATED:
            self._unexplained = None
            self._game_panel.show_notice(f"Showing the game {t.detail} (the screen went back or forward).")
        elif kind is TransitionKind.NEW_GAME:
            self._unexplained = None
            if t.flipped:
                self._set_orientation(self._orientation.flipped())
            turned = " with the board turned round" if t.flipped else ""
            self._game_panel.show_notice(f"The starting position appeared{turned}: a new game began. The last one is kept in the PGN.")
        elif kind is TransitionKind.FLIPPED:
            self._game_panel.show_notice("The board was turned round on screen; orientation switched, not a move.")
            self._set_orientation(self._orientation.flipped())
            return
        elif kind is TransitionKind.WAITING:
            self._game_panel.show_notice(t.detail, "waiting")
            return
        elif kind is TransitionKind.UNEXPLAINED:
            self._unexplained = observation
            self._game_panel.show_notice(
                f"{t.detail} Not following this. Correct the editor, or use the screen position.", "problem", offer_screen=True
            )
            return
        else:  # NO_CHANGE
            if t.detail:
                self._game_panel.show_notice(t.detail, "waiting")
            elif self._unexplained is None:
                self._game_panel.show_notice(None)
            return
        self._panel.show_board(tracker.board)
        self._show_game()

    def _learn_from_confirmed(self, observation: BoardObservation, board: chess.Board) -> None:
        """Keep learning (consented to at Learn pieces): a square the reading wasn't sure about, once
        the game confirms what is on it, becomes another example of that piece. Typically a piece seen
        on the other square colour for the first time (e.g. the black king on d8, read 0.38 → 0.9986).

        It must have been a close call: the confirmed contents got a fair share of the probability. An
        unreadable square is learned only as a piece, never as empty, so an overlay can't teach "empty".
        """
        pieces = self._pieces
        if pieces is None or observation.squares is None or not privacy_setting("keep_learning"):
            return
        unreadable = observation.unreadable_squares()
        examples = []
        for square in observation.uncertain_squares():
            r, c = square_to_screen_cell(square, observation.orientation)
            piece = board.piece_at(square)
            symbol = piece.symbol() if piece else EMPTY
            probability = observation.probabilities[r, c, CLASSES.index(symbol)]
            if square in unreadable:
                if symbol != EMPTY and probability >= MIN_LEARN_PROBABILITY_UNREADABLE:
                    examples.append((symbol, observation.squares[r, c]))
            elif probability >= MIN_LEARN_PROBABILITY:
                examples.append((symbol, observation.squares[r, c]))
        if not examples:
            return
        improved = pieces.copy()
        added = improved.add_examples(examples)
        if not added:
            return
        improved.save()
        self._pieces = improved
        self._configure_reader(reread=False)
        self.statusBar().showMessage(
            f"Learned {added} more example{'' if added == 1 else 's'} from the game "
            f"({', '.join(chess.square_name(s) for s in sorted(observation.uncertain_squares()))}).",
            6000,
        )

    # -- opt-in game history (spec §9 step 10) --------------------------------

    def _history(self) -> SessionStore | None:
        """The game-history store, only while the user has it turned on in Privacy & data."""
        if not privacy_setting("save_games"):
            self._close_history()
            return None
        if self._store is None:
            self._store = SessionStore()
        game = self._tracker.game if self._tracker is not None else None
        if game is not None and game is not self._stored_game:
            self._stored_game, self._stored_game_id = game, self._store.new_game()
        return self._store

    def _record_history_moves(self, observation: BoardObservation, count: int) -> None:
        store = self._history()
        if store is None:
            return
        sureness = float(observation.confidence.min())
        moves = [
            StoredMove(node.ply(), node.san(), node.move.uci(), node.board().fen(), sureness)
            for node in self._tracker.last_moves(count)
        ]
        store.record_moves(self._stored_game_id, moves)

    def _save_history_pgn(self) -> None:
        if self._tracker is None or not self._tracker.game.variations:
            return
        store = self._history()
        if store is not None:
            store.save_pgn(self._stored_game_id, self._tracker.current_pgn())

    def _prune_history(self) -> None:
        """Apply the retention period at start-up, even if saving has since been turned off."""
        if sessions_path().exists():
            store = SessionStore()
            removed = store.prune(float(privacy_setting("retention_days")))
            store.close()
            if removed:
                log.info("deleted %d saved game(s) past the retention period", removed)

    def _close_history(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store, self._stored_game, self._stored_game_id = None, None, None

    def _show_game(self) -> None:
        tracker = self._tracker
        if tracker is None:
            return
        board = tracker.board
        moves = tracker.move_list()
        if not self._follow_box.isChecked():
            status = "Not following. The game so far is below."
        elif board.is_game_over():
            status = f"Game over: {board.outcome().result()}."
        else:
            status = f"Following: {len(moves)} move{'' if len(moves) == 1 else 's'}, {'White' if board.turn == chess.WHITE else 'Black'} to move."
        self._game_panel.show_moves(moves, status)
        self._save_history_pgn()

    def _on_user_edit(self) -> None:
        """A hand correction while following: carry on from the corrected position (spec §6.4)."""
        if not self._following():
            return
        self._tracker.start(self._panel.board)
        self._unexplained = None
        self._game_panel.show_notice("Following from your corrected position. The moves before it are kept in the PGN.")
        self._show_game()

    def _use_screen_position(self) -> None:
        observation = self._unexplained
        if observation is None or not self._following():
            return
        self._panel.set_placement(observation.placement(), observation.side_to_move_hint())
        self._tracker.start(self._panel.board)
        self._unexplained = None
        self._game_panel.show_notice(
            "Following from the position on screen. Check side to move and castling in the editor.", "waiting"
        )
        self._show_game()

    def _undo_move(self) -> None:
        if not self._following():
            return
        san = self._tracker.undo()
        if san is None:
            self.statusBar().showMessage("Only the last move of the game can be taken back.", 5000)
            return
        self._panel.show_board(self._tracker.board)
        self._game_panel.show_notice(f"Took back {san}.")
        self._show_game()

    def _new_game(self) -> None:
        """Start over: forget the moves of this session and everything shown about them."""
        if not self._following():
            return
        if self._tracker.has_moves and not self._confirm_new_game():
            return
        self._tracker.clear()
        self._tracking_log.clear()
        self._unexplained = None
        self._preview.set_uncertain([])
        self._panel.editor.set_arrow(None)
        self._analysis.clear_lines()
        self._panel.show_board(self._tracker.board)  # start position, no last-move highlight; analysis restarts
        self._game_panel.show_notice("New game from the starting position.")
        self._show_game()

    def _confirm_new_game(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Start a new game?",
            "This clears the moves followed so far, including earlier games of this session.\n\n"
            "Use Save PGN… first if you want to keep them.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _copy_pgn(self) -> None:
        if self._tracker is None:
            self.statusBar().showMessage("No game followed yet.", 4000)
            return
        QGuiApplication.clipboard().setText(self._tracker.pgn())
        self.statusBar().showMessage("PGN copied.", 3000)

    def _save_pgn(self) -> None:
        if self._tracker is None:
            self.statusBar().showMessage("No game followed yet.", 4000)
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        name, _ = QFileDialog.getSaveFileName(self, "Save games as PGN", f"chesslens-{stamp}.pgn", "PGN (*.pgn)")
        if name:
            Path(name).write_text(self._tracker.pgn(), encoding="utf-8")
            self.statusBar().showMessage(f"Saved {Path(name).name}", 5000)

    def _on_observation(self, observation: BoardObservation) -> None:
        cal = self._calibration
        # Only readings made with the current geometry and orientation count.
        if cal is None or observation.calibration_version != cal.version or observation.orientation is not self._orientation:
            return
        self._apply_observation(observation, into_editor=self._follow_box.isChecked())

    def _apply_observation(self, observation: BoardObservation, into_editor: bool, asked: bool = False) -> None:
        """Show a reading and, if allowed, put it in the editor.

        An impossible placement never reaches the editor (spec §6.3). Unsure
        squares keep a reading out of Follow the board; when the user asked
        with Read board it is loaded, and the unsure squares are named and
        marked on the preview for checking.
        """
        if not observation.board_visible:
            self._preview.set_uncertain([])
            self._show_recognition_state(NO_BOARD)
            if self._following():
                self._game_panel.show_notice(f"{NO_BOARD} Following is paused; the game so far is kept.", "waiting")
            return
        self._preview.set_uncertain(
            [(r, c, s if (s := observation.symbol_at(r, c)) != EMPTY else "") for r, c in observation.uncertain_cells()]
        )
        if self._following():
            self._track(observation)
            return
        hard = observation.placement_problems()
        unsure = ", ".join(chess.square_name(s) for s in observation.uncertain_squares())
        if hard or (unsure and not asked):
            reasons = hard + ([f"Not sure about {unsure}."] if unsure else [])
            self._show_recognition_state("Reading not used: " + " ".join(reasons))
            return
        placement = observation.placement()
        pieces = len(placement.piece_map())
        if not into_editor:
            self._show_recognition_state(f"Board read: {pieces} pieces, all recognised.")
            return
        turn = observation.side_to_move_hint()
        changed = self._panel.set_placement(placement, turn)
        whose = "" if turn is None else f" {'White' if turn else 'Black'} to move (from the last-move highlight)."
        verb = "Updated the editor" if changed else "The editor already matches"
        check = f" Check {unsure} (marked): not sure about them." if unsure else ""
        self._show_recognition_state(f"{verb}: {pieces} pieces.{whose}{check} Check castling rights.")

    # -- engine analysis -----------------------------------------------------

    def _on_position_changed(self, _fen: str) -> None:
        # Every edit is a new position version; the old arrow and lines no longer apply.
        self._panel.editor.set_arrow(None)
        self._coordinator.set_position(self._panel.board)

    def _on_analysis_toggled(self, on: bool) -> None:
        if on and not self._fair_play_acknowledged:
            if not self._confirm_fair_play():
                self._analysis.toggle.blockSignals(True)
                self._analysis.toggle.setChecked(False)
                self._analysis.toggle.blockSignals(False)
                return
            self._fair_play_acknowledged = True
        if on and self._coordinator.engine is None:
            self._coordinator.set_engine(self._make_engine())
        self._coordinator.set_enabled(on)
        if not on:
            self._panel.editor.set_arrow(None)

    def _confirm_fair_play(self) -> bool:
        """The visible reminder the spec requires before live recommendations are enabled (§3)."""
        answer = QMessageBox.question(
            self,
            "Turn on engine analysis?",
            FAIR_PLAY_REMINDER,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _make_engine(self) -> EngineService | None:
        cfg = self._config.engine
        command = self._engine_command
        if command is None:
            found = find_stockfish(cfg.path, load_user_settings().get("engine_path"))
            if found is None:
                return None
            command = str(found)
        engine = EngineService(
            command,
            threads=cfg.threads,
            hash_mb=cfg.hash_mb,
            multipv=cfg.multipv,
            think_time_s=self._analysis.think_time_s,
            on_result=self._bridge.engine_result.emit,
            on_status=self._bridge.engine_status.emit,
        )
        engine.start()
        return engine

    def _locate_engine(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Locate Stockfish", "", "Programs (*.exe);;All files (*)")
        if not name:
            return
        save_user_settings(engine_path=name)
        self._engine_command = None
        self._coordinator.set_engine(self._make_engine())

    def _on_think_time(self, seconds: float) -> None:
        if self._coordinator.engine is not None:
            self._coordinator.engine.think_time_s = seconds
            self._coordinator.refresh()

    def _on_engine_result(self, result: AnalysisResult) -> None:
        # Second guard after the engine's own: only the current position's analysis may show (spec §9 step 9).
        if not self._coordinator.accept(result):
            return
        self._analysis.show_result(result)
        self._gamebar.show_analysis(result, self._game_panel.status.text())
        best = result.best
        self._panel.editor.set_arrow(chess.Move.from_uci(best.uci) if best else None)
        # Annotated session (spec §6.7): the finished evaluation goes into the followed game's PGN.
        if result.final and best is not None and self._tracker is not None:
            if self._tracker.annotate(result.fen, best.score_cp_white, best.mate_white, result.depth):
                self._save_history_pgn()

    def _on_engine_status(self, status: EngineStatus) -> None:
        self._analysis.show_engine_status(status)

    def _on_analysis_status(self, status: AnalysisStatus) -> None:
        self._analysis.show_status(status)
        self._gamebar.show_status(status, self._game_panel.status.text())

    # -- menus and dialogs ---------------------------------------------------

    def _build_menus(self) -> None:
        def action(menu, text, slot, shortcut=None):
            act = QAction(text, self)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            menu.addAction(act)
            return act

        file_menu = self.menuBar().addMenu("&File")
        action(file_menu, "Open screen &recording…", self._open_video)
        action(file_menu, "Save &labeled board…", self._save_labeled_board)
        action(file_menu, "Save games as &PGN…", self._save_pgn, "Ctrl+S")
        action(file_menu, "Save &tracking log…", self._save_tracking_log)
        file_menu.addSeparator()
        action(file_menu, "E&xit", self.close, "Ctrl+Q")
        settings_menu = self.menuBar().addMenu("&Settings")
        action(settings_menu, "&Privacy && data…", self._open_privacy)
        action(settings_menu, "&Locate Stockfish…", self._locate_engine)
        help_menu = self.menuBar().addMenu("&Help")
        action(help_menu, "&User guide", self._open_user_guide, "F1")
        action(help_menu, "&About ChessLens", self._show_about)

    def _save_tracking_log(self) -> None:
        """What ChessLens saw over its last readings while following, for working out why it lost track."""
        if not len(self._tracking_log):
            self.statusBar().showMessage("Nothing to save: the log fills while following a game.", 5000)
            return
        folder = QFileDialog.getExistingDirectory(self, "Save tracking log to folder")
        if folder:
            out = self._tracking_log.save(Path(folder))
            self.statusBar().showMessage(f"Saved {len(self._tracking_log)} readings to {out}", 8000)

    def _open_privacy(self) -> None:
        dialog = PrivacyDialog(self)
        dialog.pieces_deleted.connect(lambda: self._load_piece_set(self._pieces_combo.currentText()))
        dialog.settings_changed.connect(lambda: None if privacy_setting("save_games") else self._close_history())
        dialog.exec()

    def _open_user_guide(self) -> None:
        guide = resource_path("docs/user-guide.md")
        if not guide.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(guide))):
            self.statusBar().showMessage(f"The user guide is at {guide}", 8000)

    def _show_about(self) -> None:
        QMessageBox.about(self, "About ChessLens", ABOUT_TEXT.format(version=__version__))

    def closeEvent(self, event: QCloseEvent) -> None:
        self._stop_capture()
        self._reader.stop()
        self._coordinator.close()
        self._close_history()
        self._gamebar_timer.stop()
        self._gamebar.close()
        super().closeEvent(event)
