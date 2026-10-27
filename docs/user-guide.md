# ChessLens user guide

ChessLens reads a chess app mirrored from your phone, follows the game move by move, and analyses it with Stockfish. Everything runs on this computer.

## Before you start

1. **Mirror the phone.** Start AirScreen (or another mirroring app) and mirror the phone to it (iPhone: Control Center → Screen Mirroring → AirScreen).
2. **Keep the mirror visible.** ChessLens reads what's on screen, so nothing may cover the mirroring window, including ChessLens itself. With two monitors, put them on different screens.
3. **Stockfish** (for analysis), if you didn't tick it in the installer:

   ```
   winget install --id Stockfish.Stockfish --exact
   ```

## First time: learn your pieces

1. Open a game on the phone at the **starting position**.
2. In ChessLens, check the mirroring window is selected at the top and press **Start capture**. The view zooms to the phone and a green grid snaps onto the board. If it doesn't, drag a rectangle around the board on the preview, then drag the corner dots to fine-tune.
3. Check the a–h / 1–8 labels match the phone; press **Flip** if not.
4. The editor on the right starts at the starting position too. Press **Learn pieces** and confirm.

Learned pieces are saved under the name in the **Pieces** box (default "Chess.com"). If you change Chess.com's piece style or board, type a new name and learn again.

If ChessLens warns that the editor doesn't match the screen, answer **No** and make them match first: learning from a wrong position teaches it wrong pieces. Examples that can't be right, such as a blank square learned as a piece or a covered square learned as empty, are refused, and left out of sets learned by earlier versions.

## Reading and following a game

- **Read board** puts what's on the screen into the editor once. Squares it isn't sure about are marked amber with "?" on the preview; check them.
- **Follow the game** follows from the editor's position, move by move. Start it at the starting position, or press **Read board** first and set side to move and castling. From a middlegame position it asks you to confirm them, because a screenshot can't show them.
- The **Game** panel lists the moves. If ChessLens can't explain what it sees, it says why and offers **Use screen position**; you can also just correct the editor, and following carries on from your correction.
- **Undo move** takes back a misread move. **Copy PGN** / **Save PGN…** export every game of the session, with engine evaluations where they were analysed.
- **New game** starts over from the starting position and clears everything about the games so far: moves, earlier games of the session, notices, highlights and analysis. It asks first if there are moves to lose, so save the PGN before if you want to keep them. (If the phone itself starts a new game, ChessLens notices the starting position and starts a new game too, keeping the earlier one in the session.)
- Stepping through the game with Chess.com's ◀ ▶ arrows is recognised; it doesn't change the game. Playing something else after stepping back keeps the old moves as a variation.
- Against bots, replies and premoves often land before the board has settled. ChessLens catches up on up to four moves at once. It uses Chess.com's last-move highlight to tell which move came last, and says so if it had to guess the order. Starting a new bot game as Black (board turned round) switches the orientation by itself.
- While you drag a piece, the squares under it can't be read, so ChessLens waits for the move to land rather than guessing. If it ever does take a wrong turn, a clear move played from up to four moves back puts it right; the moves it had are kept as a side line.

## Analysis

Tick **Engine analysis**. The first time, ChessLens reminds you of fair play (see below). You get three candidate moves with scores from White's point of view (`+0.32`, or `#3` for mate), the line that follows, an evaluation bar and an arrow for the best move. **Explain the best move** adds plain-language notes from rules of thumb; they describe the move, not the engine's reasoning.

### In Xbox Game Bar

If the ChessLens Game Bar widget is installed (see `gamebar-widget/README.md` in the source), press **Win+G**, open the widget menu and choose **ChessLens analysis**. It shows the evaluation bar, the best move, the top three lines and the game status, and can be pinned so it stays on screen when Game Bar is closed. It shows what ChessLens's analysis panel shows, so **Engine analysis** must be on in ChessLens. Keep the widget off the board: ChessLens reads the board from the screen, and the mirroring window must not be covered.

## Fair play

Use engine suggestions only where engine help is allowed: study, puzzles, games against the computer, or reviewing finished games. Using them in a live or rated game against another person breaks the rules of Chess.com and other chess sites.

## Privacy

Nothing is uploaded, and the screen is never recorded. **Settings → Privacy & data** shows what is kept on this computer and lets you delete it: learned pieces, the keep-learning switch, and an opt-in game history with a retention period. See [privacy-fair-play.md](privacy-fair-play.md).

## Troubleshooting

| You see | What to do |
| --- | --- |
| "No usable frames: covered by …" | Move that window off the mirroring window, or put them on different monitors. |
| "No chessboard is visible…" | The phone disconnected or left the board. Following pauses and keeps the game; it carries on when the board is back. |
| "Board: not set" | Press **Detect board**, or drag a rectangle around the board on the preview. |
| Squares marked "?" | Usually a piece on a square colour it hasn't been learned on yet. While following, ChessLens learns these by itself once the game confirms them; or fix the editor and press **Learn pieces** again. |
| "It looks like Black moved, but the game has White to move" | Side to move was wrong when following started. Fix it in the editor. |
| Following loses track | Use **File → Save tracking log…** right away. It saves what ChessLens saw over its last 40 readings (board squares only), which shows why. Then press **Use screen position** or correct the editor to carry on. |
| "Stockfish isn't installed" | Run the winget command above, then turn analysis off and on, or use **Settings → Locate Stockfish…**. |

## Keyboard

The editor board takes the keyboard when clicked. Use the arrows to move, `K Q R B N P` for white pieces and `k q r b n p` for black, Delete to clear, Space to apply the palette piece. Ctrl+S saves the PGN, F1 opens this guide.

## Command line

`ChessLens.exe` also runs from a terminal:

| Command | What it does |
| --- | --- |
| `ChessLens.exe selftest` | Checks this copy works (vision, tracking, Stockfish). |
| `ChessLens.exe list` | Lists windows that can be captured. |
| `ChessLens.exe grab -o shot.png` | Saves one frame of the mirroring window. |
| `ChessLens.exe analyse "<FEN>"` | Prints Stockfish's analysis as JSON. |
| `ChessLens.exe benchmark <folder>` | Measures recognition on boards saved with **Save labeled board…**. |
