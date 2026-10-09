"""Load strategy.yaml (the versioned champion) and infra.yaml.

The engine only ever reads the strategy config; challengers live in
config/challengers/ and are never auto-loaded (spec I4).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")


@dataclass
class Config:
    strategy: dict
    infra: dict
    root: str = ROOT
    overrides: dict = field(default_factory=dict)

    @property
    def strategy_version(self) -> str:
        return f"champion@{self.strategy['version']}"

    @property
    def strategy_hash(self) -> str:
        blob = json.dumps(self.strategy, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def path(self, key: str) -> str:
        p = self.infra["paths"][key]
        return p if os.path.isabs(p) else os.path.join(self.root, p)

    def seed_for(self, session_date: str) -> int:
        return int(self.infra.get("seed_base", 0)) + int(session_date.replace("-", ""))


def _deep_update(base: dict, upd: dict) -> dict:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def load_config(strategy_path: str | None = None, infra_path: str | None = None,
                overrides: dict | None = None, root: str | None = None) -> Config:
    with open(strategy_path or os.path.join(CONFIG_DIR, "strategy.yaml")) as f:
        strategy = yaml.safe_load(f)
    with open(infra_path or os.path.join(CONFIG_DIR, "infra.yaml")) as f:
        infra = yaml.safe_load(f)
    overrides = overrides or {}
    if overrides:
        strategy = _deep_update(copy.deepcopy(strategy), overrides.get("strategy", {}))
        infra = _deep_update(copy.deepcopy(infra), overrides.get("infra", {}))
    return Config(strategy=strategy, infra=infra, root=root or ROOT, overrides=overrides)
