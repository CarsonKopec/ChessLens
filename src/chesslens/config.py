"""Settings loaded from config/default.toml (or a file passed with --config)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from chesslens.resources import resource_path

DEFAULT_CONFIG_PATH = resource_path("config/default.toml")


@dataclass
class CaptureConfig:
    fps: float = 5.0
    window_hint: str = "AirScreen"
    check_occlusion: bool = True
    initial_backoff_s: float = 0.5
    max_backoff_s: float = 5.0


@dataclass
class EngineConfig:
    path: str = ""  # empty: look on PATH and in winget's install folder
    threads: int = 0  # 0: half the CPU cores, at most 4
    hash_mb: int = 128
    multipv: int = 3
    think_time_s: float = 1.0


@dataclass
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)


def load_config(path: Path | None = None) -> Config:
    """Read the TOML file, falling back to built-in defaults for anything missing."""
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return Config(capture=_section(CaptureConfig, raw.get("capture", {})), engine=_section(EngineConfig, raw.get("engine", {})))


def _section(cls, values: dict):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in values.items() if k in known})
