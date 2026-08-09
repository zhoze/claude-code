"""Prediction snapshots (spec §31): frozen BEFORE the open, never modified.

Each run writes one immutable JSON file per day plus append-only DB rows.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .. import MODEL_VERSION
from ..data.store import Store


def freeze_snapshot(store: Store, run_date: dt.date, run_ts: dt.datetime,
                    candidates: list[dict], selections: list[str],
                    market_context: dict) -> Path:
    """candidates: full per-ticker payloads (scores, signals, predictions)."""
    path = store.snapshot_dir / f"{run_date.isoformat()}.json"
    if path.exists():
        # a snapshot for today already exists — NEVER overwrite (spec §31, §49)
        raise FileExistsError(
            f"snapshot for {run_date} already frozen at {path}; refusing to modify")
    doc = {
        "DATE": run_date.isoformat(),
        "TIMESTAMP": run_ts.isoformat(),
        "MODEL_VERSION": MODEL_VERSION,
        "MARKET_CONTEXT": market_context,
        "SELECTIONS": selections,
        "CANDIDATES": candidates,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str))
    tmp.rename(path)

    for rank, cand in enumerate(sorted(candidates,
                                       key=lambda c: -(c.get("FINAL_SCORE") or 0)), 1):
        store.save_prediction(
            run_date=run_date, run_ts=run_ts, ticker=cand["TICKER"], payload=cand,
            final_score=cand.get("FINAL_SCORE"), confidence=cand.get("CONFIDENCE"),
            final_rank=rank, selected=cand["TICKER"] in selections,
            model_version=MODEL_VERSION)
    return path
