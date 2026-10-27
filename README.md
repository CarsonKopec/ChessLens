# ChessLens

Reads a chess app mirrored from an iPhone onto a Windows desktop, recognises
the pieces, follows the game move by move, and analyses it with Stockfish,
all on this computer. See [chesslens_spec_v1_1.md](chesslens_spec_v1_1.md) for
the full specification.

ChessLens does not connect to the phone. An external mirroring app (here,
**AirScreen**) shows the iPhone screen in a window, and ChessLens reads that
window's client area from the desktop.

- **Install:** run `ChessLens-Setup-<version>.exe` (built as below). It installs for your user without admin rights and can install Stockfish with winget.
- **Use:** see the [user guide](docs/user-guide.md). Privacy and fair play: [docs/privacy-fair-play.md](docs/privacy-fair-play.md). Internals: [docs/architecture.md](docs/architecture.md).

## Status

**Phase 0: capture.** Done:

- List top-level windows by title and process, with AirScreen pre-selected (`window_hint` in [config/default.toml](config/default.toml)).
- Capture the selected window's client area with Win32 + `mss` (title bar and borders excluded) at an adjustable FPS (default 5).
- Pause instead of capturing wrong pixels when the window is minimized, covered by another window, off-screen, on another virtual desktop, or showing a blank picture.
- Reconnect with bounded backoff when the window closes, picking up the same app's new window.
- Replay a screen recording through the same pipeline (`VideoFileSource`) for offline testing.

**Phase 1: board finding and manual position entry.** Done:

- Zooms the preview to the phone screen and finds the board automatically, on the first frames and again whenever the window changes size. It looks for 7–9 equally spaced long edges in each direction and keeps the grid whose squares alternate light/dark. On a live Chess.com capture it lands within a pixel, and all 64 squares match.
- Draws the 8×8 grid and a–h / 1–8 labels on the preview. Drag a corner dot to adjust the board (four-corner calibration), or drag a rectangle to mark it by hand.
- Guesses which side is at the bottom from where light and dark pieces sit, ignoring the app's legal-move dots. **Flip** overrides it.
- Position editor: a digital board you correct by clicking squares, either on the captured preview or on the editor. Use the piece palette or type K Q R B N P / k q r b n p; right click or Delete clears a square.
- Side to move, castling rights (only those the pieces allow), and en passant (only squares where a capture is legal), plus plain-language validation such as "Black has no king".
- FEN import (paste and press Enter) and export (**Copy FEN**).
- **Save labeled board…** writes the straightened 800×800 board image plus a JSON file with its FEN and orientation. This builds the test set that piece recognition (Phase 3) will need.

**Phase 2: Stockfish analysis.** Done:

- Stockfish runs locally over UCI on a background thread: MultiPV 3, about 1 s per position (adjustable), modest threads and hash ([config/default.toml](config/default.toml)).
- Only legal, unfinished positions reach the engine. An illegal position shows what's wrong instead, and checkmate, stalemate and dead draws are reported without asking the engine.
- Every edit gives the position a new version. A newer position stops the search in progress, and results are checked against the current version twice: once in the engine service, once in the UI. Analysis of an older position never shows.
- Evaluation bar plus three candidate lines, each with the score from White's point of view (`+0.32`, or `#3` / `#-2` for mates; never a win percentage), the move in SAN, and the principal variation with move numbers. The best move is drawn as an arrow on the editor board.
- Finished analyses are cached by the complete position (pieces, side to move, castling, en passant, clocks) and the search settings.
- Turning analysis on shows a fair-play reminder once per session (spec §3). If Stockfish isn't installed, the panel shows how to install it, and capture and editing carry on.

**Phase 3: automatic piece recognition.** Done:

- **Learn pieces** learns your piece set from the board on screen, using the editor's position as the answer: show the start position on the phone and press it once. It refuses (or asks first) if the editor doesn't match what's on screen. Square images are kept only on this computer, in `%APPDATA%\ChessLens\pieces`, and never shipped with ChessLens (spec §7: app artwork is copyrighted). Several sets can be kept; type a new name in the **Pieces** box.
- Each square is cut out from its own background (board colour or the app's highlight tint), compared with every learned example, and given a probability for each of the 13 classes. A square that matches nothing well is "unrecognised", which keeps unknown overlays from being forced into a piece.
- Readings are checked as a whole: one king each, at most 8 pawns and 16 pieces, no pawns on the back ranks, no more extra pieces than promotions allow, not both kings in check. A piece set that hasn't learned all 12 pieces is never trusted.
- **Read board** loads the screen into the editor. An impossible reading is refused with the reason; unsure squares are loaded but named and marked amber with "?" on the preview.
- Reading runs on a background thread, only after the picture has stayed still for two frames, so pieces mid-animation aren't read.
- Side to move for a single reading is taken from Chess.com's last-move highlight when there is exactly one (two marked squares, one with the moved piece).
- On real Chess.com captures: learned from one position and read a different one 64/64, including a queen and king on other square colours and highlighted; about 35 ms per board.
- `chesslens benchmark` measures accuracy on boards saved with **Save labeled board…**: per square, per board, by confidence band, and how often a wrong reading would have been accepted.

**Phase 4: following the game.** Done:

- **Follow the game** tracks the game from the editor's position (from a middlegame it first asks you to confirm side to move, castling and en passant, which a screenshot can't show). Each still reading must be explained by the game: unchanged, legal moves from the current position, or a step back/forward through the game (Chess.com's review arrows).
- Moves are matched against the legal moves, so castling, en passant, promotions and under-promotions come out right, and side to move, castling rights, en passant, clocks and move numbers stay correct. If a reply came too fast to be seen, two moves are reconstructed, but only when exactly one pair fits.
- Nothing is guessed. A move is accepted only if every square it changes was read with confidence, or was read half-sure (best guess at least 40% likely) with that guess being exactly what the only legal move puts there. Squares below that, such as the promotion picker or a notification, make ChessLens wait.
- It keeps learning while following, as the consent at **Learn pieces** says: a square it wasn't sure about, once the game confirms what's on it, is added to the piece set if the confirmed piece was a close call. Live, the black king on dark squares read 0.38–0.50 (it's only learned on e8 at the start), and after one learned example it read 0.9986. Unreadable squares are learned only as pieces given at least 25% probability, never as empty, so overlays don't teach anything. A full piece type replaces its most similar example, so the originals aren't pushed out. If no legal moves explain the screen, it says so (and spots a wrong side to move) and offers **Use screen position**, or you correct the editor: any hand edit while following restarts from your correction.
- A board turned round on screen switches the orientation and is not a move. The start position appearing mid-game starts a new game.
- Session history: the game is kept as a PGN tree, so stepping back and playing something else adds a variation instead of losing moves. Earlier games of the session are kept too. **Undo move**, **New game**, **Copy PGN** and **Save PGN…** are in the Game panel, the move list shows the current move, and the editor highlights the last move. Engine analysis follows the tracked position.

**Phase 5: polish and packaging.** Done:

- **Source switching:** switching to another source forgets the old source's crop, board and readings (FR-03).
- **Outages while following:** if the screen drops (closed, minimised, covered, blank), following pauses and keeps the game. When the picture returns, the board is re-read and checked against the game; up to two missed moves are reconstructed.
- **Board not visible:** a region that no longer looks like a chessboard (AirScreen's "Waiting for iPhone", another app) is reported as not visible rather than read.
- **Privacy & data** (Settings menu):
  - shows what is kept and where;
  - deletes learned piece sets;
  - switches off learning while following;
  - offers an opt-in game history (SQLite: moves with time, position and reading confidence, plus PGN; no images) with a retention period and "delete all".
  - Uninstalling asks whether to delete `%APPDATA%\ChessLens`.
- **Explain the best move:** optional plain-language notes from rules of thumb (captures, checks, castling, development, threats, pins, retreats, sacrifices, material along the line), plus the evaluation in words. They're labelled as not the engine's reasoning.
- **Annotated PGN:** saved PGN carries `[%eval]` comments for analysed positions.
- **Menus and diagnostics:** File / Settings / Help menus, an About box with licence notes, and `chesslens selftest`, which also works on the installed exe from a terminal.
- **New game** clears everything about the games so far (moves, earlier games of the session, notices, highlights, analysis, tracking log), after asking if there are moves to lose. The opt-in saved history is kept; it has its own delete controls.
- **Bot games:** bots reply, and premoves fire, before the board settles. Following now catches up on up to four moves at once. It only tries moves that touch squares that changed, within a fixed search budget (usually 5–30 ms).
  - An explanation is accepted when every fitting order ends in the same position.
  - Chess.com's last-move highlight picks the order. It also rules out explanations with the wrong side to move: on screen, a3…a4 looks like a single a2–a4.
  - A new game shown turned round (playing Black) is recognised and the orientation switched.
  - A recorded fast bot game is followed move-perfectly through the full pipeline. With catch-up switched off, the same test loses sync.
- **File → Save tracking log…** saves the last 40 readings (what was seen and concluded, board squares only) for working out why tracking went wrong.
- **Windows build:** `scripts/build_windows.py` builds a PyInstaller app with an icon and the bundled libraries' licence texts, then an Inno Setup installer.

## Build the Windows installer

```bash
.venv\Scripts\python -m pip install -e ".[build]"
```

```bash
.venv\Scripts\python scripts\build_windows.py
```

This produces `dist\ChessLens\ChessLens.exe` and, with Inno Setup 6 installed, `dist\ChessLens-Setup-<version>.exe`. Check a build with `dist\ChessLens\ChessLens.exe selftest`. The icon is regenerated with `scripts\make_icon.py`.

## Licensing

The installer bundles python-chess, which is GPL-3.0, so a copy of ChessLens you share must be distributed under GPL-3.0-compatible terms. ChessLens itself has no licence chosen yet; pick one before sharing builds. The bundled libraries' licence texts are copied into `licenses\` in every build. Stockfish (GPL-3.0) is not bundled. Chess.com's piece artwork is never included; learned pieces stay on the user's computer.

## Development setup

Python 3.12+ on Windows. `C:\Python314\python.exe` on this machine is broken (0 bytes), so use the `py` launcher:

```bash
py -V:3.14-64 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Stockfish (GPL-3.0, installed separately, not bundled):

```bash
winget install --id Stockfish.Stockfish --exact
```

ChessLens finds it on PATH or in winget's install folder. To use a copy from elsewhere, set `path` under `[engine]` in the config, or use **Locate Stockfish…** in the app.

## Use

1. Start AirScreen and mirror the iPhone to it (Control Center → Screen Mirroring → AirScreen).
2. Keep the AirScreen window visible. ChessLens reads visible pixels, so nothing may cover it, including the ChessLens window. With two monitors, put them on different screens.
3. Run the dashboard and press **Start capture**:

```bash
.venv\Scripts\python -m chesslens
```

4. The view zooms to the phone and the green grid snaps onto the board. If it doesn't, drag a rectangle around the board, then drag the corner dots to fine-tune.
5. Check the a–h / 1–8 labels against the phone and press **Flip** if they're the wrong way round.
6. Fix the position: pick a piece in the palette and click squares on the preview or the editor board. Set side to move and castling, then **Copy FEN**.
7. Once: with the start position on the phone (and in the editor), press **Learn pieces**. After that, **Read board** fills the editor from the screen. If a square is marked "?", fix it in the editor; pressing **Learn pieces** again on that corrected board teaches ChessLens that case too.
8. To follow a game, make the editor match the phone (start position for a new game, or **Read board** and check side to move and castling) and tick **Follow the game**. Moves appear in the Game panel; save them with **Save PGN…**.
9. Tick **Engine analysis**. Use it only where engine help is allowed.

Command-line checks:

```bash
.venv\Scripts\python -m chesslens list
```

```bash
.venv\Scripts\python -m chesslens grab -o airscreen.png
```

```bash
.venv\Scripts\python -m chesslens analyse "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
```

```bash
.venv\Scripts\python -m chesslens benchmark path\to\labeled-boards
```

```bash
.venv\Scripts\python -m chesslens selftest
```

Other flags: `--window TEXT` selects a different mirroring app, `--video FILE` replays a recording, `--config FILE` uses another settings file, and `-v` logs capture events.

## Code layout

| Path | What it does |
| --- | --- |
| `src/chesslens/capture/` | `FrameSource` protocol, Win32 window capture, video replay, capture thread, view crop |
| `src/chesslens/vision/board_detector.py` | Automatic board finding |
| `src/chesslens/vision/board_warp.py` | Perspective warp to 800×800, screen cell ↔ chess square mapping |
| `src/chesslens/vision/calibration.py` | Board corners, rough piece census, orientation guess |
| `src/chesslens/vision/square_features.py` | Cuts each piece out of its square's background |
| `src/chesslens/vision/piece_set.py` | Learned examples per piece, stored in `%APPDATA%` |
| `src/chesslens/vision/piece_classifier.py` | 13-class probabilities per square, reading checks, side-to-move hint |
| `src/chesslens/vision/board_reader.py` | Background reading once the board holds still |
| `src/chesslens/vision/benchmark.py` | Accuracy and confidence report on labeled boards |
| `src/chesslens/chess/` | FEN parsing, position validation, castling / en passant options |
| `src/chesslens/chess/tracker.py` | Game tracking: legal transition matching, navigation, flips, new games, PGN tree |
| `src/chesslens/ui/game_panel.py` | Move list, notices, undo / new game / PGN |
| `src/chesslens/engine/stockfish_service.py` | Stockfish process, newest-position-only searching, discovery |
| `src/chesslens/engine/coordinator.py` | Position versions, what may reach the engine, stale-result filter |
| `src/chesslens/engine/results.py`, `analysis_cache.py` | Lines and scores from White's view, spec JSON, result cache |
| `src/chesslens/ui/analysis_panel.py` | Evaluation bar, candidate lines, engine status |
| `src/chesslens/domain/models.py` | `Orientation` |
| `src/chesslens/ui/main_window.py` | Dashboard wiring |
| `src/chesslens/ui/preview_widget.py` | Captured preview with grid overlay, corner handles, click-to-correct |
| `src/chesslens/ui/board_widget.py`, `position_panel.py` | Digital board editor, palette, game state, FEN |
| `src/chesslens/ui/settings_dialog.py` | Privacy & data dialog |
| `src/chesslens/explain/rule_explanations.py` | Rule-of-thumb notes on engine moves |
| `src/chesslens/persistence/sqlite_store.py` | Opt-in game history with retention |
| `src/chesslens/selftest.py` | `chesslens selftest` diagnostics |
| `scripts/build_windows.py`, `packaging/` | PyInstaller build, licence collection, Inno Setup installer |

The capture package never imports the UI or the engine (spec §11).

## Tests

```bash
.venv\Scripts\python -m pytest
```

Qt tests render offscreen. Engine tests talk UCI to a small scripted engine ([tests/fake_uci.py](tests/fake_uci.py)); one more test runs against real Stockfish when it's installed. Tests marked `desktop` briefly open small topmost windows at (240, 240) on the primary monitor and capture them; skip them with `-m "not desktop"`.

## Known limits

- Capture reads the screen, so a covered or minimized AirScreen window pauses capture (by design, spec §4.2).
- Windows 11 rounds window corners, so about 7 px in each bottom corner of the client area shows whatever is behind the window. The board is nowhere near them.
- Board finding assumes a flat 2D board drawn square-on, as phone chess apps show it. 3D or tilted boards need the manual corners.
- Recognition knows one look of each piece per learned set: if you change Chess.com's piece style or board theme, learn again under a new name.
- Following needs a correct starting point: from a middlegame, side to move, castling and en passant are whatever you confirm.
- More than four moves between two readings (or a position where four moves could change too much) can't be reconstructed; ChessLens reports it and you correct the editor or use the screen position.
- A jump straight to the start position from deep in a game (e.g. a "first move" button) is treated as a new game; the old one is kept in the PGN.
- The session's games live in memory until you save or copy the PGN, unless you turn on game history in Privacy & data.
- Explanations are rules of thumb: they describe what a move does, not why the engine prefers it, and they don't spot every idea (pins to the queen, quiet manoeuvres).
