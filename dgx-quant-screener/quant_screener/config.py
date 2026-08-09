"""Configuration loading. Single source of truth is config.yaml at project root."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class Config(dict):
    """Dot-accessible nested config: cfg.portfolio.cvar_alpha"""

    def __getattr__(self, key: str) -> Any:
        try:
            val = self[key]
        except KeyError as e:
            raise AttributeError(key) from e
        if isinstance(val, dict) and not isinstance(val, Config):
            val = Config(val)
            self[key] = val
        return val


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path) as f:
        raw = yaml.safe_load(f)
    cfg = Config(raw)
    cfg["project_root"] = str(PROJECT_ROOT)
    storage = PROJECT_ROOT / cfg["run"]["storage_dir"]
    for sub in ("cache", "snapshots", "reports"):
        (storage / sub).mkdir(parents=True, exist_ok=True)
    cfg["storage_root"] = str(storage)
    return cfg


def fmp_api_key(cfg: Config) -> str | None:
    """FMP key from environment; never stored in config or logs."""
    return os.environ.get(cfg.data.fmp_api_key_env) or None
