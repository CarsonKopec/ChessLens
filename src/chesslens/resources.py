"""Files shipped with ChessLens (default config, docs, icon), found both from a source checkout and in the packaged app."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    """`relative` under the project root, or under PyInstaller's unpack folder when frozen."""
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) if base else Path(__file__).resolve().parents[2]) / relative
