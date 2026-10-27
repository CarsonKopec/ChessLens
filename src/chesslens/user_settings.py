"""Per-user choices made in the app (e.g. the Stockfish location), kept in %APPDATA%\\ChessLens\\settings.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Privacy defaults (spec §6.7, §11): learning from confirmed games is covered by the consent at
# Learn pieces; keeping game history is off until the user turns it on.
PRIVACY_DEFAULTS = {"keep_learning": True, "save_games": False, "retention_days": 30}


def privacy_setting(name: str):
    return load_user_settings().get(name, PRIVACY_DEFAULTS[name])


def settings_path() -> Path:
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home()) / "ChessLens" / "settings.json"


def load_user_settings() -> dict:
    path = settings_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return {}


def save_user_settings(**values: object) -> None:
    """Merge `values` into the settings file."""
    path = settings_path()
    merged = load_user_settings() | values
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
