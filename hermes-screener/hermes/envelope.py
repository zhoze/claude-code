"""The response envelope every engine tool returns (spec §3.1).

Large arrays never travel in `payload`; they are written to Parquet and returned
as `artifacts` paths.
"""
from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from . import __version__


def engine_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001 — git absent is fine
        sha = ""
    return f"{__version__}+{sha}" if sha else __version__


@dataclass
class Envelope:
    run_id: str
    tool: str
    engine_version: str
    strategy_version: str
    data_snapshot: str
    seed: int
    timings_ms: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    payload: Any = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunContext:
    """Provenance shared by every tool call in one pass."""
    run_id: str
    snapshot: str           # session date YYYY-MM-DD == snapshot directory name
    seed: int
    strategy_version: str
    engine_version: str = field(default_factory=engine_version)

    @contextmanager
    def tool(self, name: str):
        env = Envelope(self.run_id, name, self.engine_version, self.strategy_version,
                       self.snapshot, self.seed)
        t0 = time.perf_counter()
        try:
            yield env
        finally:
            env.timings_ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
