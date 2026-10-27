# Privacy and fair play

## What ChessLens processes

ChessLens reads the pixels of one window you choose: the mirroring app showing your phone. Frames are processed in memory, a few per second, and dropped. The screen is never recorded, and nothing is sent anywhere: ChessLens makes no network connections. Stockfish runs on this computer too.

While capture is on, the status bar shows a red **● CAPTURING SCREEN** sign, and **Stop capture** ends it.

## What is kept on this computer

Everything is in `%APPDATA%\ChessLens`, apart from files you save yourself.

| What | When | Contents | How to remove it |
| --- | --- | --- | --- |
| Learned pieces (`pieces\*.npz`) | You press **Learn pieces** and agree; then, while following, unsure squares the game confirms (the same consent; can be switched off) | Small images (48×48) of board squares, one piece or empty square each; no other part of the screen | **Settings → Privacy & data → Delete selected** |
| Game history (`sessions.sqlite3`) | Only if you turn on **Keep a history of followed games** | Each confirmed move with its time, the position after it and how sure the reading was, plus each game's PGN; no images | Deleted automatically after the retention period you choose (default 30 days); **Delete all saved games** at any time |
| Settings (`settings.json`) | You change a setting | Stockfish location and the privacy switches | Delete the file |
| Labeled boards, PGN files | You use **Save labeled board…** or **Save PGN…** | A straightened board image and its FEN, or the games | Wherever you saved them |
| Tracking log | You use **File → Save tracking log…** (otherwise it's only in memory, the last 40 readings while following, and **New game** clears it) | What each reading saw and concluded, and the board-square images read | Wherever you saved it |

Uninstalling ChessLens asks whether to delete `%APPDATA%\ChessLens` as well.

## Fair play

ChessLens is meant for study, puzzles, games against the computer, and reviewing finished games. Using engine suggestions during a live or rated game against another person breaks the fair-play rules of Chess.com and other chess sites.

To support that:

- Engine analysis is **off** until you turn it on, and the first time each session it shows a reminder you have to accept.
- The analysis panel always shows a fair-play note.
- ChessLens never operates the phone or makes moves (spec FR-14). It doesn't try to get round any app's restrictions, and it reports protected or blank video rather than working around it.
