# ChessLens Xbox Game Bar widget

Shows ChessLens's engine analysis in Xbox Game Bar (**Win+G**): the evaluation
bar and score, the best move, the top three lines, the search depth, the game
status and the fair-play reminder. It can be pinned so it stays on screen when
Game Bar is closed.

Game Bar only hosts widgets that are UWP apps, so this is a small C# app of its
own. ChessLens (`src/chesslens/gamebar.py`) keeps `analysis.json` up to date in
this app's LocalState folder, the one place a UWP app can read without extra
permissions, and the widget re-reads it every 0.3 s. Nothing goes over the
network.

## Requirements (once)

- **Visual Studio 2026** with the **WinUI application development** workload
  and, under its optional parts, **Universal Windows Platform tools**
  (Visual Studio Installer → Modify).
- **Developer Mode** on (Settings → System → Advanced → Developer Mode), so
  Windows installs the widget without the Microsoft Store or a certificate.

## Build and install

From the repository root:

```bash
.venv/Scripts/python scripts/build_gamebar_widget.py
```

It builds a Release package (.NET Native), unpacks it to
`gamebar-widget\bin\x64\Release\Layout` and registers it for your user. Run it
again after changing the widget. The widget's NuGet package,
`Microsoft.Gaming.XboxGameBar`, comes under Microsoft's own licence.

## Use

1. In ChessLens, tick **Engine analysis** (and **Follow the game** to follow a game).
2. Press **Win+G**, open **Widgets** and choose **ChessLens analysis**. Pin it to keep it on screen.
3. Keep it off the board: ChessLens reads the board from the screen, and stops while anything covers the mirroring window.

The widget says when ChessLens isn't running, is closed, or has analysis off.
Starting **ChessLens widget** from the Start menu opens the same view in a
normal window, which is handy for checking the analysis comes through.

## Uninstall

```bash
powershell -NoProfile -Command "Get-AppxPackage ChessLens.GameBarWidget | Remove-AppxPackage"
```
