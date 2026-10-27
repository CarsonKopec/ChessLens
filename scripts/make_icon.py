"""Draw the ChessLens icon (a white knight on the board's green) and write assets/chesslens.ico,
plus the tile images the Game Bar widget's package needs (gamebar-widget/Assets).

The .ico holds PNG images at the usual Windows sizes. Run from the project root:
    .venv\\Scripts\\python scripts\\make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import chess  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from chesslens.ui.board_widget import draw_piece  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
# The widget package's VisualElements images: name -> (canvas width, height, icon size).
WIDGET_ASSETS = {
    "Square44x44Logo.png": (44, 44, 44),
    "Square150x150Logo.png": (150, 150, 120),
    "StoreLogo.png": (50, 50, 50),
    "SplashScreen.png": (620, 300, 200),
}


def render_image(size: int, canvas: tuple[int, int] | None = None) -> QImage:
    """The icon at `size`, centred on a transparent `canvas` (default: just the icon)."""
    width, height = canvas or (size, size)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate((width - size) / 2, (height - size) / 2)
    p.setPen(QPen(QColor(40, 70, 45), max(1, size / 40)))
    p.setBrush(QColor(110, 150, 86))
    radius = size * 0.18
    p.drawRoundedRect(QRectF(size * 0.03, size * 0.03, size * 0.94, size * 0.94), radius, radius)
    draw_piece(p, QRectF(size * 0.08, size * 0.06, size * 0.84, size * 0.84), chess.Piece(chess.KNIGHT, chess.WHITE))
    p.end()
    return image


def render(size: int) -> bytes:
    image = render_image(size)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_ico(path: Path, images: dict[int, bytes]) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images.items():
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    path.write_bytes(header + entries + blobs)


def main() -> None:
    app = QApplication.instance() or QApplication([])  # noqa: F841 (fonts need an application)
    root = Path(__file__).resolve().parents[1]
    out = root / "assets" / "chesslens.ico"
    out.parent.mkdir(exist_ok=True)
    write_ico(out, {size: render(size) for size in SIZES})
    print(f"wrote {out}")
    widget_assets = root / "gamebar-widget" / "Assets"
    widget_assets.mkdir(parents=True, exist_ok=True)
    for name, (width, height, size) in WIDGET_ASSETS.items():
        render_image(size, (width, height)).save(str(widget_assets / name), "PNG")
    # Game Bar's own folder in the package (PublicFolder in the manifest): the widget's menu icon.
    game_bar = root / "gamebar-widget" / "GameBar"
    game_bar.mkdir(exist_ok=True)
    render_image(64).save(str(game_bar / "ChessLens.png"), "PNG")
    print(f"wrote the widget images in {widget_assets} and {game_bar}")


if __name__ == "__main__":
    main()
