"""Stage 8 — macro/context agent (stub in v0.1, as agreed).

The production design (spec §5) is a local LLM producing strict JSON with an
exposure gate in {1.0, 0.5, 0.0} and per-name event_risk. v0.1 does not run an
LLM. It reads an optional JSON file that follows the §5 schema (for example one
written by an external Hermes macro run). With no valid file it uses a neutral gate
of 1.0 and flags macro_stale. The macro output can only reduce or delay positions.
"""
from __future__ import annotations

import json
import os

ALLOWED_GATES = (1.0, 0.5, 0.0)


def load_macro(path: str | None, prev_gate: float | None = None) -> dict:
    base = {"exposure_gate": 1.0 if prev_gate is None else prev_gate, "event_risk": [],
            "regime_view": None, "watch": [], "rationale": "", "macro_stale": True,
            "source": "stub (no macro agent in v0.1)"}
    if not path or not os.path.exists(path):
        return base
    try:
        with open(path) as f:
            d = json.load(f)
        gate = float(d["exposure_gate"])
        if gate not in ALLOWED_GATES:
            raise ValueError(f"exposure_gate {gate} not in {ALLOWED_GATES}")
        er = [e for e in d.get("event_risk", [])
              if isinstance(e, dict) and 0.0 <= float(e.get("score", -1)) <= 1.0]
        return {**base, **{k: d[k] for k in ("regime_view", "watch", "rationale", "as_of",
                                              "prompt_version", "model") if k in d},
                "exposure_gate": gate, "event_risk": er, "macro_stale": False,
                "source": os.path.basename(path)}
    except Exception as e:  # noqa: BLE001 — schema failure -> carry gate, flag stale
        return {**base, "error": f"{type(e).__name__}: {e}"}
