# ChessLens — iPhone Screen-to-Chess Analysis

**Technical Specification — v1.1 (external mirroring)**  
**Date:** October 24, 2026  
**Status:** Proposed / implementation-ready blueprint  
**Primary platform:** Windows 11 laptop; iPhone as visual source  
**Preferred transport:** Existing external iPhone mirroring application (wireless preferred)  
**Wired fallback:** Handled by the external mirroring/capture application, not ChessLens  
**Excluded:** Direct iPhone-to-laptop USB-C-to-USB-C screen mirroring

## 1. Executive Summary

ChessLens is a local-first desktop application that receives a mirrored view of a chess app running on an iPhone, detects the chessboard and pieces, maintains a validated game state, sends positions to Stockfish, and shows suggestions and explanations in a Windows dashboard. The same chess-analysis pipeline works regardless of which screen-capture transport is selected. MVP targets chess study, puzzles, self-play, engine-permitted games, and post-game review. It must not be marketed or designed to circumvent competitive chess platforms' fair-play rules.

**Key design decision:** ChessLens does **not** mirror the iPhone or manage transport. A separate external Windows application displays the iPhone screen. ChessLens selects that application window and reads its visible client area using desktop capture. An optional capture-device adapter can be added later without changing the downstream recognition pipeline.

## 2. Goals, Non-Goals, and Success Criteria

### Goals
1. Read the visible window of an already-running external iPhone mirroring application on Windows; do not implement phone connectivity.
2. Automatically locate an 8×8 chessboard and identify square occupancy and piece classes/colors.
3. Track moves across time and maintain legal chess state, not just a static image.
4. Use Stockfish through UCI to generate top candidate moves, centipawn/mate evaluations, and principal variations.
5. Display the current position, confidence, suggestions, and plain-language rationales in an accessible dashboard.
6. Recover from interrupted streams, occlusions, overlays, board flips, and uncertain detections.
7. Keep images and session data local by default.

### Non-Goals for v1
- Automatically operating the phone or making moves.
- Bypassing a chess app's restrictions or fair-play controls.
- Supporting every possible theme, variant, or chess-like game on day one.
- Fully autonomous interpretation of any arbitrary iPhone app.
- Guaranteed sub-second end-to-end analysis on all laptops.
- Treating a single screenshot as sufficient to infer castling/en-passant rights reliably.

### Acceptance Targets (targets, not promises)
| Area | MVP target | Measurement |
|---|---|---|
| External window input | Stable selectable mirrored window | 20-minute trial with recoverable interruption |
| Wired connection | External application responsibility | ChessLens works unchanged when source window continues showing video |
| Board detection | ≥ 98% on supported theme/resolution test set | Labeled board images |
| Piece classification | ≥ 99% per-square on supported theme test set | Labeled square crops |
| Move tracking | ≥ 99% correct legal transition recognition in controlled recordings | 100+ annotated moves, including special moves |
| Analysis latency | First result ≤ 3 seconds after a stable new position on reference hardware | P50/P95 reported separately |
| Stability | Recover after source loss without corrupting chess history | Fault-injection tests |
| Safety | No unverified move submitted to Stockfish | Invariant tests |

Performance metrics should record actual hardware, capture resolution, and Stockfish configuration.

## 3. Usage Scenarios

- **Study / puzzles:** User opens a chessboard or puzzle on the iPhone; ChessLens mirrors it, recognizes the current position, and explains engine-recommended candidate moves.
- **Replay / game review:** User steps through completed games; ChessLens tracks each position and highlights inaccuracies and alternatives.
- **Self-play / engine-allowed practice:** Suggestions can remain enabled during play where the rules explicitly permit assistance.
- **Troubleshooting:** If vision confidence is low, user corrects board orientation, highlights board bounds, edits a FEN, or confirms a detected move.

**Fair-play mode:** Live suggestions should be used only in settings that allow engine assistance. An analysis-only/review mode may disable live hints while still recording a session for later study. The app should show a visible reminder before enabling live recommendations.

## 4. External Mirroring and Window Capture (Revised)

**Connection to iPhone is out of scope.** The user starts a third-party app that displays the iPhone screen on the Windows desktop. That app can use wireless mirroring or any supported wired source. ChessLens only reads the displayed pixels.

### 4.1 Setup
1. Launch the external screen-mirroring app and connect the iPhone using its own setup process.
2. Keep the app's screen-preview window visible and unobstructed on the Windows desktop.
3. In ChessLens, click **Select Window**, and choose the receiver by window title and process name.
4. Preview the captured window content, crop out title bars/borders, and select or auto-detect the board area.
5. Start analysis on confirmed board states only.

### 4.2 Capture behavior
- Enumerate top-level windows via Win32 (`EnumWindows`, `GetWindowText`, `GetWindowThreadProcessId`).
- Capture the selected **client area** by translating `GetClientRect` using `ClientToScreen`; avoid grabbing window chrome.
- Use `mss` for simple region capture and OpenCV for frame processing.
- In this mode, window capture reads **visible desktop pixels**: occlusion, minimizing, scaling, moving to a different monitor, and DPI settings can disrupt frames.
- A future optional Windows Graphics Capture backend may support occlusion-resistant capture when tested; do not promise it for the MVP.
- Automatically pause analysis when the window disappears, becomes minimized, is obscured, or yields stale/invalid frames.
- Provide adjustable capture FPS (default 5 FPS) and optional ROI crop. Trigger Stockfish when the validated chess state changes, not at capture frame rate.

### 4.3 Frame source abstraction

```python
from typing import Protocol
import numpy as np

class FrameSource(Protocol):
    def start(self) -> None: ...
    def read(self) -> np.ndarray | None: ...  # BGR image
    def stop(self) -> None: ...

class WindowCaptureSource:  # required MVP implementation
    pass

class VideoFileSource:  # test fixture / replay
    pass
```

The previous HDMI/UVC handling can be a future optional input plugin, **not** a required capture path or implementation milestone.

## 5. High-Level Architecture

```
  [iPhone Chess App]
         │
    ┌────┴──────────────────────────────┐
    │                                   │
 [External Mirroring App]        [Recorded Test Video]
    │                                   │
 [Window Capture]                 [Camera Capture]
    └────────────────┬──────────────────┘
                     ▼
               [FrameSource API]
                     ▼
          [Preprocessing + Board ROI]
                     ▼
          [Grid Rectification / 8×8]
                     ▼
           [Piece Classification]
                     ▼
       [Temporal Tracking / Hypotheses]
                     ▼
        [python-chess Legal State]
               │             │
               ▼             ▼
       [Stockfish UCI]   [Session Database]
               │             │
               └──────┬──────┘
                      ▼
             [Windows Dashboard]
          board + eval + moves + logs
```

**Concurrency:** one thread/task for capture, a bounded/latest-frame queue for vision, an event-driven chess-state coordinator, a cancellable or version-keyed engine worker, and a UI event bus. Never allow an old Stockfish result to overwrite a newer position.

## 6. Components and Detailed Behavior

### 6.1 Capture Manager
- List top-level Windows application windows with human-readable titles and process names.
- Provide live preview, pause, crop controls, and resolution/FPS readouts.
- Detect source disconnects and retry with bounded backoff.
- Apply frame timestamps and source identifiers.
- Drop excess frames rather than building an ever-growing queue.
- Offer explicit selection of mirrored receiver window to avoid capturing unrelated desktop content.

### 6.2 Board Detection and Calibration
- Detect roughly square board region via OpenCV edges/lines, contours, and an optional small object-detection model.
- Perspective-correct board using four corners to canonical 8×8 grid (e.g. 800×800 output).
- User can drag corners manually when automatic detection fails.
- Track region-of-interest across frames to avoid re-detecting on every frame.
- Support white-bottom and black-bottom board orientations; show and allow manual override.
- Ignore video frames showing animations or orientation transitions until the image stabilizes.
- Keep per-frame detection confidence and calibration version.

### 6.3 Piece Recognition
- Classify each square as empty or one of 12 pieces (6 types × 2 colors).
- v1: start with known-board-theme template matching or manually labeled small CNN; evaluate a YOLO-style detector as a future alternative.
- Maintain dataset splits by **theme/game/session**, not random near-duplicate frames, to avoid overestimating accuracy.
- Output a 64-square probability distribution, not merely one hard class per square.
- Reject inconsistent results: missing kings, too many pieces, impossible promotion history, etc.
- Provide manual correction on a board editor and save labeled examples **only after consent**.

### 6.4 State Estimation and Move Tracking
- Represent game state with `python-chess.Board`, including side to move, castling rights, en-passant target, halfmove clock, and move number.
- **Board occupancy alone does not fully determine FEN.** If beginning midgame, ask for side to move and unknown castling/en-passant rights, or mark them uncertain. For precise engine analysis prefer tracking from the standard initial position or importing PGN/FEN.
- On stable visual changes, compare predicted occupancy to positions reachable by legal move(s) from the last confirmed state.
- Prefer a single legal transition; permit temporary intermediate frames for piece animations.
- Handle promotion via UI/dialog recognition or manual confirmation; validate castling and en passant through the chess rules library.
- Reject impossible transitions and request correction rather than fabricating moves.
- Maintain `confirmed_state`, `candidate_state`, confidence and a bounded rollback log.
- Account for flips: a board orientation change is not a move.
- On reconnect, compare the new observation with the old state; if multiple moves happened during outage, request PGN/FEN confirmation or reconstruct only if unambiguous.
- Preserve a full position history for repetition and review. Never invent who moved first from a single screenshot.

### 6.5 Engine Service (Stockfish)
- Use local Stockfish executable through python-chess UCI API.
- Configure engine path, number of threads, memory/hash size, MultiPV (default 3), and thinking budget.
- Analyze only a stable, legally validated position; cache results by complete state key.
- Return move in UCI and SAN, score from White's perspective, mate indication, search depth, nodes, principal variation(s), and time consumed.
- Cancel/ignore stale analysis when a newer board version arrives.
- Do not confuse centipawn evaluation with win probability. Distinguish checkmate scores from numeric eval.
- In UI, distinguish *best engine move* from *suggestion explanation* (an LLM summary can be imperfect).

Example API schema:

```json
{
  "position_version": 42,
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "analysis_ms": 700,
  "depth": 17,
  "lines": [
    { "rank": 1, "uci": "e2e4", "san": "e4", "score_cp_white": 25, "mate": null, "pv_uci": ["e2e4", "e7e5"] }
  ]
}
```

*This JSON is illustrative only; it is not live analysis data.*

### 6.6 Explanation Service (optional)
- MVP uses deterministic wording: piece development, captures, checks, threats, king safety, center control, material change, and PV rendering.
- Optional on-device/hosted language model can rewrite bounded, grounded facts into plain English, but must not invent lines or claim a tactical proof it did not calculate.
- All explanations should cite actual moves/PVs as inputs and flag uncertainty.
- Keep the LLM optional so core chess analysis works offline.

### 6.7 Dashboard

**Layout:**
- Left: captured phone preview with board bounding box, source selector, connection status.
- Center: clean digital chessboard, orientation toggle, last move highlight, confidence warnings.
- Right: evaluation bar, top 3 candidate moves, engine PV, thinking status, short explanation.
- Bottom: PGN move list, source/recognition logs, settings, export controls.

**Controls:** connect/disconnect; Wireless / Wired / File; select source; calibrate; pause capture; force rescan; confirm/correct position; analysis on/off; review-only; save PGN/FEN; export annotated session.

**Privacy UX:** clearly indicate when screen capture is active. Avoid silently recording the full iPhone screen. Default to board-only in-memory processing, no cloud transfer.

## 7. Recommended Technology Stack

| Layer | Primary choice | Alternate |
|---|---|---|
| Language | Python 3.12+ | Python 3.11 |
| Desktop UI | PySide6 / Qt | Local FastAPI + React web UI |
| Computer vision | OpenCV + NumPy | ONNX Runtime model inference |
| ML model | PyTorch during training; ONNX for deployment | Lightweight template matching for known themes |
| Screen window capture | Windows Graphics Capture integration or `mss` | OBS virtual-camera bridge |
| Window selection | `pywin32` / Win32 | OS window enumerator |
| Chess rules + PGN/FEN | `python-chess` | — |
| Engine | Stockfish executable (UCI) | Other UCI-compatible engines later |
| Settings/session DB | SQLite + JSON config | — |
| Packaging | PyInstaller or Nuitka | — |
| Logs | Python `logging` / structured JSON logs | — |

**Dependencies:** Any iPhone mirroring/receiver application is external software, not supplied or controlled by ChessLens. Verify licensing and compatibility before bundling. Stockfish is GPL-licensed; review obligations for redistribution. Do not bundle copyrighted game UI assets without permission.

## 8. Suggested Repository Structure

```text
chesslens/
├── README.md
├── pyproject.toml
├── config/
│   └── default.toml
├── src/chesslens/
│   ├── app.py
│   ├── domain/
│   │   ├── events.py
│   │   ├── models.py
│   │   └── session.py
│   ├── capture/
│   │   ├── base.py
│   │   ├── window_capture.py
│   │   ├── uvc_capture.py
│   │   └── recorded_video.py
│   ├── vision/
│   │   ├── board_detector.py
│   │   ├── board_warp.py
│   │   ├── piece_classifier.py
│   │   ├── temporal_filter.py
│   │   └── calibration.py
│   ├── chess/
│   │   ├── tracker.py
│   │   ├── fen_pgn.py
│   │   └── validations.py
│   ├── engine/
│   │   ├── stockfish_service.py
│   │   └── analysis_cache.py
│   ├── explain/
│   │   └── rule_explanations.py
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── board_widget.py
│   │   └── settings_dialog.py
│   ├── persistence/
│   │   └── sqlite_store.py
│   └── util/
│       └── logging.py
├── assets/
├── models/                   # gitignored large weights as appropriate
├── datasets/                 # gitignored/private by default
├── tests/
│   ├── fixtures/
│   ├── test_move_tracker.py
│   ├── test_board_detection.py
│   ├── test_engine.py
│   └── test_capture_reconnect.py
├── scripts/
│   ├── collect_labeled_boards.py
│   └── benchmark_pipeline.py
└── docs/
    ├── architecture.md
    └── privacy-fair-play.md
```

## 9. Runtime Event Flow

1. User selects capture source.
2. Capture manager emits timestamped frames to bounded queue.
3. Board detector rectifies a candidate board and sets confidence.
4. Piece recognizer emits probabilistic board observation.
5. Temporal filter waits for a stable observation across several frames.
6. Chess tracker tests observation against legal successors and either accepts transition, asks for user input, or preserves previous state.
7. On new confirmed position, coordinator increments `position_version`.
8. Engine analyzes that board state asynchronously and returns version-tagged results.
9. UI displays results only when version matches current confirmed position.
10. Session stores timestamp, confirmed FEN, move, confidence, and engine analysis (if saving enabled).

```python
# Pseudocode only
on_stable_observation(observation):
    transition = tracker.match_legal_transition(observation)
    if transition.is_unambiguous:
        confirmed = tracker.commit(transition)
        version = session.increment_version()
        engine.submit(version, confirmed.board.copy())
    else:
        ui.show_review_required(transition.reason)

on_engine_result(result):
    if result.position_version == session.position_version:
        ui.show_analysis(result)
```

## 10. Functional Requirements

**FR-01** Select any visible external mirroring application window without requiring proprietary automation hooks.  
**FR-02** Preview selected window client area and crop to the chessboard.  
**FR-03** Source switching does not crash UI or leave stale results.  
**FR-04** Calibrate board ROI automatically or via four-corner manual editor.  
**FR-05** Detect orientation and allow override.  
**FR-06** Output per-square piece identity and confidence.  
**FR-07** Require legal state consistency before analysis.  
**FR-08** Track standard moves, castling, en passant, promotions, and board flips.  
**FR-09** Show best N engine lines with SAN/PV and normalized evaluation.  
**FR-10** Let user edit position and import/export FEN/PGN.  
**FR-11** Support pausing analysis and enabling review-only mode.  
**FR-12** Allow per-session capture/model/engine telemetry and opt-in storage.  
**FR-13** Work without internet after capture is established and dependencies installed; wireless LAN connectivity may still be required.  
**FR-14** Never control or click the iPhone automatically.  
**FR-15** Provide a visible active-capture status and explicit stop action.

## 11. Non-Functional Requirements

- **Modularity:** capture adapters never import chess engine or UI components.
- **Resource control:** bounded queues; engine worker budget; adjustable FPS and resolution.
- **Robustness:** retries, timeouts, error messages, optional manual corrections.
- **Security:** local-only bindings, safe file paths, no arbitrary remote command execution, no silent external uploads.
- **Privacy:** crop to chessboard before saving, opt-in image logging, configurable retention and deletion.
- **Accessibility:** readable text, keyboard controls, color-independent piece/confidence indicators.
- **Observability:** structured logs; latency counters for capture/detect/classify/track/analyze/render.
- **Maintainability:** typed interfaces, clear contracts, fixtures and regression tests.

## 12. Core Edge Cases

| Situation | Expected behavior |
|---|---|
| Board animating after a move | Delay state commit until stable |
| Notification covers board | Mark low confidence; don't infer a move |
| Screen orientation rotates | Recalibrate geometry |
| Board flipped (Black at bottom) | Change coordinate mapping, not history |
| Theme/piece set changes | Warn; ask for classifier selection or calibration |
| Opening a game mid-position | Ask for turn/castling/EP details or manual FEN |
| Two moves occurred during lag | Attempt bounded legal search; confirm if ambiguous |
| Promotion picker overlays board | Wait/ask which piece was promoted |
| Castling/en passant | Use legal transition logic, not simple two-square diff |
| External mirroring disconnects | Pause analysis; keep last confirmed state; resume when video returns |
| Stockfish executable missing | Show setup instructions; continue capture/review |
| Window minimized or covered | Warn capture unavailable; wait until visible and unobstructed |
| Old engine result arrives late | Discard if position version mismatched |
| Draw, checkmate, stalemate | Detect via `python-chess`; stop recommending illegal moves |
| Protected/blank video | Tell user input is unavailable; don't attempt circumvention |

## 13. Testing Plan

### Unit tests
- Chess tracking: move generation, SAN/UCI/FEN, castling rights, en passant, promotions, checkmate, turn tracking.
- Known square labeling and orientation conversions.
- Bounded queue and versioned engine result handling.

### Integration tests
- External app window capture using a recorded test stream and live manual trials.
- Window move/resize, minimize, occlusion, DPI scaling, and reconnection tests.
- Vision observations → legal tracker → Stockfish → dashboard.
- Manual FEN edit and PGN round-trip.

### Benchmark data
- Store consented, labeled board images across light/dark themes, scaling, different resolutions, orientations, piece sets and UI overlays.
- Include recordings with start-position sequences and tricky moves.
- Report false-positive moves and required manual interventions, not just per-piece accuracy.

### Acceptance run
- Establish both transport methods on target hardware (where compatible).
- Analyze at least one complete permitted test game, including tactical positions.
- Simulate 10 disconnections, 10 occlusions, a flip, castling, en passant, and promotion.
- Confirm no unvalidated position reaches engine and no stale result overwrites current state.

## 14. Development Milestones

### Phase 0 — Environment and capture proof of concept
Deliverable: Windows app lists/selects the external mirroring application window and displays its client-area frames. Add offline recorded-video input for deterministic testing. No iPhone transport code.

### Phase 1 — Board finding and manual position entry
Deliverable: Board rectangle/grid UI, manual calibration, orientation switch, click-to-correct 64-square board, FEN export. This is already a functioning analysis tool without automatic piece recognition.

### Phase 2 — Stockfish integration and UI
Deliverable: Full FEN validation, legal move representation, MultiPV engine output, evaluation bar, principal-variation notation. Connect it first to manually verified positions.

### Phase 3 — Automated piece vision
Deliverable: piece classification, confidence overlay, supported theme selection, calibrated benchmarks. Reject invalid board states.

### Phase 4 — Temporal move tracker
Deliverable: legal transition matching, animation filtering, special moves, manual discrepancy resolution, session history.

### Phase 5 — Polish and packaging
Deliverable: source switcher, reconnect UX, privacy controls, PGN export, optional explanations, Windows installer, documentation and automated tests.

## 15. Project Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| External receiver does not display iPhone screen | No frames to capture | Troubleshoot the external application; ChessLens reports missing/stale input |
| Laptop window capture misses protected content | No frames | Display honest error; no circumvention |
| Wrong recognition produces misleading advice | High | Confidence threshold + manual review + legality checks |
| Castling/EP rights ambiguous midgame | Incorrect search lines | Start-position tracking or explicit manual state |
| Captured screen has moving overlays | Bad transitions | Stable-frame filter and board-only ROI |
| High CPU/GPU use | Lag and heat | Lower input FPS, use ROI, throttle engine threads |
| Licensing/distribution complexities | Release friction | Document and comply with Stockfish GPL and other libraries |
| Competitive misuse | Fair-play breach | Review-only UX; explicit engine-permitted context |

## 16. First Implementation Backlog

- [ ] Scaffold Python project and PySide6 main window.
- [ ] Create `FrameSource` protocol and `RecordedVideoSource`.
- [ ] Implement Windows receiver-window capture selector.
- [ ] Implement selected-window client-area preview and visibility checks.
- [ ] Add manual board crop/corner calibration.
- [ ] Add 8×8 digital board editor and FEN validation.
- [ ] Launch local Stockfish and show MultiPV.
- [ ] Add state/version cancellation so older results never replace newer ones.
- [ ] Collect labeled test screenshots for one theme.
- [ ] Implement confidence-scored piece recognition.
- [ ] Implement legal temporal move tracker.
- [ ] Add PGN export, logs, reconnect/recovery and privacy preferences.

## 17. Open Decisions

1. Which external Windows mirroring application will be used? Do not hard-code its title; allow picking any window.
2. Which iPhone model and chess app/theme will serve as the initial supported dataset?
3. Is the initial UI pure PySide6 or local web frontend? **Default: PySide6** for desktop capture and packaging.
4. What initial engine settings work well on laptop hardware? **Default: MultiPV 3, modest threads/hash, ~0.5–1.0 second search** subject to benchmarking.
5. Should sessions be saved? **Default: metadata/FEN/PGN only, optional and local.**
6. Which hardware adapter/capture card is compatible with the user's actual iPhone? Choose only after device identification; not needed for wireless MVP.

## 18. References

- Apple Support — Connect iPhone to a display with a cable: https://support.apple.com/guide/iphone/connect-to-a-display-with-a-cable-iph93af723b9/ios
- Apple Support — iPhone USB-C connector and supported external displays: https://support.apple.com/en-us/105099
- Stockfish project: https://stockfishchess.org/
- python-chess documentation: https://python-chess.readthedocs.io/
- OpenCV documentation: https://docs.opencv.org/
- Qt for Python / PySide6: https://doc.qt.io/qtforpython-6/

---

**Recommended build order:** capture first, then manual FEN + Stockfish, then automated recognition and move tracking. This makes each phase demonstrably useful and avoids relying on a brittle vision model before the chess engine and dashboard work.
