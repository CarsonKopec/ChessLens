# ChessLens architecture

ChessLens follows the pipeline of the specification ([chesslens_spec_v1_1.md](../chesslens_spec_v1_1.md) §5):

```
mirroring window ──► capture ──► board finding ──► piece reading ──► game tracking ──► engine ──► dashboard
   (AirScreen)      (thread)     (calibration)    (reader thread)    (legal moves)    (thread)
```

## Threads and data flow

| Thread | Does | Hands on |
| --- | --- | --- |
| Capture (`capture/manager.py`) | Reads the window's client area at a set rate (default 5 fps), stamps frames, retries with backoff if the window goes | Each frame to the UI through a queued Qt signal |
| UI (Qt main thread) | Shows the preview, keeps the board calibration up to date, owns all state | The newest frame to the board reader's one-frame slot |
| Board reader (`vision/board_reader.py`) | Straightens the board, waits until it has been still for two frames, reads all 64 squares | One reading per still position, through a queued signal |
| Engine (`engine/stockfish_service.py`) | Runs Stockfish over UCI on the newest submitted position only | Version-tagged results through a queued signal |

Queues hold at most one item, so a slow stage never builds a backlog; it just skips to the newest frame.

## Invariants

- **Nothing unverified reaches the engine.** The coordinator (`engine/coordinator.py`) only submits legal, unfinished positions; the engine service checks again.
- **No stale result is shown.** Every position gets a version. A newer submission stops the running search; results are checked against the current version in the service and again in the UI.
- **No move is invented.** The tracker (`chess/tracker.py`) accepts a change only when exactly one explanation fits. That's legal moves from the current position (one, or two if a reply was too fast to see), or a step through the game. Every square the change touches must also be read confidently, or half-sure with a matching guess. Otherwise it waits or asks.
- **The screen is never trusted blindly.** A covered, minimised or off-screen window gives no frames, not wrong ones. A region that no longer looks like a chessboard is reported, not read.

## Modules

| Package | Responsibility |
| --- | --- |
| `capture/` | `FrameSource` protocol; Win32 window capture with visibility checks; video replay; capture thread; view crop |
| `vision/` | Board detection (equally spaced edges + light/dark check), perspective warp, orientation guess, square normalisation, learned piece sets, 13-class reading with calibrated confidence, background reader, benchmark |
| `chess/` | FEN parsing, position and placement validation, game tracking (PGN tree, variations, flips, new games) |
| `engine/` | Stockfish discovery and service, result types (scores from White's view), cache, coordinator |
| `explain/` | Rule-of-thumb notes on engine moves (optional, offline) |
| `persistence/` | Opt-in SQLite game history with retention |
| `ui/` | Dashboard, preview with grid and corner handles, board editor, analysis panel, game panel, privacy dialog |
| `domain/` | Shared small types (`Orientation`) |
| `gamebar.py` | Keeps the analysis the dashboard shows in `analysis.json` in the Xbox Game Bar widget's own app-data folder, the one place that UWP app can read; rewritten on change and every 5 s as a heartbeat |
| `gamebar-widget/` (C#, outside `src/`) | The Game Bar widget: a UWP XAML app (the only kind Game Bar hosts) that re-reads that file a few times a second. Built and installed by `scripts/build_gamebar_widget.py` |

The capture package imports neither the engine nor the UI (spec §11).

## Recognition in short

Each square of the straightened board is cut out of its background (the board colour, or the app's highlight tint), reduced to 48×48, and compared with every learned example of each class, allowing a pixel of misalignment. Distances become probabilities with a calibrated temperature, plus an "unrecognised" option, so a square that resembles nothing learned gets low confidence instead of a forced guess. A reading is rejected as a whole if the placement is impossible (wrong number of kings, too many pawns, impossible promotions, both kings in check) or the piece set is incomplete.

## Testing

`pytest` runs over 200 tests:

- unit tests for every module
- engine tests against a scripted UCI engine (and real Stockfish when installed)
- Qt tests rendered offscreen
- an end-to-end test that plays a recorded game through capture, reading and tracking
- live Win32 capture tests (marked `desktop`), which open small windows

`chesslens selftest` checks an installed copy the same way without the screen or the phone.

## Packaging

`scripts/build_windows.py` builds a PyInstaller folder app, collects the bundled libraries' licence texts into `licenses/`, and compiles `packaging/chesslens.iss` with Inno Setup into a per-user installer. Stockfish is not bundled.
