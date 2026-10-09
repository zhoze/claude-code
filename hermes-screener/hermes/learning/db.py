"""Learning database (spec §8): DuckDB, append-only run/candidate/entry/macro tables.

Every name that reaches stage 3 is logged, picked or not, because the rejected names
are the counterfactual store (I5). Forward outcomes are not written as UPDATEs. They
go into a separate append-only `candidate_outcomes` table, filled by
`backfill_outcomes` as horizons complete; readers join on (run_id, ticker).
"""
from __future__ import annotations

import json
import os

import duckdb
import numpy as np
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id VARCHAR PRIMARY KEY, date DATE, pass VARCHAR, strategy_version VARCHAR,
  strategy_hash VARCHAR, engine_version VARCHAR, data_snapshot VARCHAR, seed BIGINT,
  regime_label VARCHAR, regime_probs JSON, universe_size INTEGER, funnel_counts JSON,
  solver_info JSON, warnings JSON, status VARCHAR, created_at TIMESTAMP DEFAULT now());
CREATE TABLE IF NOT EXISTS candidates (
  run_id VARCHAR, ticker VARCHAR, reached_stage INTEGER, fund_family_pcts JSON,
  fund_consensus DOUBLE, cluster_id INTEGER, er_score DOUBLE, risk_score DOUBLE,
  anomaly_flags JSON, in_book BOOLEAN, book_weight DOUBLE, ta_consensus DOUBLE,
  triggered BOOLEAN, veto_reason VARCHAR, macro_event_risk DOUBLE, final_weight DOUBLE,
  entry_ref_price DOUBLE, PRIMARY KEY (run_id, ticker));
CREATE TABLE IF NOT EXISTS candidate_outcomes (
  run_id VARCHAR, ticker VARCHAR, horizon_days INTEGER, fwd_ret DOUBLE, fwd_mdd DOUBLE,
  benchmark_rel DOUBLE, measured_on DATE, PRIMARY KEY (run_id, ticker, horizon_days));
CREATE TABLE IF NOT EXISTS entries (
  run_id VARCHAR, ticker VARCHAR, entry_date DATE, entry_ref_price DOUBLE,
  planned_weight DOUBLE, exposure_gate DOUBLE, est_cost_bps DOUBLE, exit_date DATE,
  exit_ref_price DOUBLE, realized_cost_bps DOUBLE, holding_days INTEGER,
  realized_return DOUBLE, realized_mdd DOUBLE, benchmark_rel_return DOUBLE,
  regime_at_entry VARCHAR, regime_at_exit VARCHAR, outcome_notes VARCHAR,
  PRIMARY KEY (run_id, ticker));
CREATE TABLE IF NOT EXISTS macro_outputs (
  run_id VARCHAR PRIMARY KEY, payload JSON, realized_vol_21d DOUBLE,
  gate_was_costly BOOLEAN, event_risk_hit BOOLEAN);
CREATE TABLE IF NOT EXISTS challengers (
  challenger_id VARCHAR PRIMARY KEY, created TIMESTAMP, proposed_by VARCHAR,
  config_diff JSON, wf_result JSON, deflated_sharpe DOUBLE,
  champion_deflated_sharpe DOUBLE, holdout_used BOOLEAN, decision VARCHAR,
  decided_by VARCHAR, decided_at TIMESTAMP);
"""


def _j(x) -> str:
    return json.dumps(x, default=str, sort_keys=True)


class LearningDB:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.con = duckdb.connect(path)
        self.con.execute(SCHEMA)

    def close(self):
        self.con.close()

    def log_run(self, row: dict) -> None:
        cols = ["run_id", "date", "pass", "strategy_version", "strategy_hash", "engine_version",
                "data_snapshot", "seed", "regime_label", "regime_probs", "universe_size",
                "funnel_counts", "solver_info", "warnings", "status"]
        vals = [(_j(row.get(c)) if c in ("regime_probs", "funnel_counts", "solver_info",
                                         "warnings") else row.get(c)) for c in cols]
        self.con.execute(f"INSERT INTO runs ({', '.join(cols)}) VALUES "
                         f"({', '.join('?' * len(cols))})", vals)

    def log_candidates(self, rows: list[dict]) -> None:
        if not rows:
            return
        df = pd.DataFrame(rows)
        for c in ("fund_family_pcts", "anomaly_flags"):
            df[c] = df[c].map(_j)
        self.con.register("cand_df", df)
        cols = ", ".join(df.columns)
        self.con.execute(f"INSERT INTO candidates ({cols}) SELECT {cols} FROM cand_df")
        self.con.unregister("cand_df")

    def log_entries(self, rows: list[dict]) -> None:
        for r in rows:
            cols = list(r)
            self.con.execute(f"INSERT INTO entries ({', '.join(cols)}) VALUES "
                             f"({', '.join('?' * len(cols))})", [r[c] for c in cols])

    def log_macro(self, run_id: str, payload: dict) -> None:
        self.con.execute("INSERT INTO macro_outputs (run_id, payload) VALUES (?, ?)",
                         [run_id, _j(payload)])

    def query(self, sql: str) -> pd.DataFrame:
        """Read-only SQL (learning_query). Anything but SELECT/WITH is rejected."""
        head = sql.lstrip().split(None, 1)[0].lower() if sql.strip() else ""
        if head not in ("select", "with"):
            raise PermissionError("learning_query is read-only (SELECT/WITH only)")
        return self.con.execute(sql).df()

    def backfill_outcomes(self, adjclose: pd.DataFrame, bench: pd.Series,
                          horizons=(5, 21, 63)) -> int:
        """Append forward returns for (run, ticker, horizon) windows that have completed."""
        cand = self.con.execute(
            "SELECT c.run_id, c.ticker, r.data_snapshot FROM candidates c "
            "JOIN runs r USING (run_id)").df()
        done = self.con.execute(
            "SELECT run_id, ticker, horizon_days FROM candidate_outcomes").df()
        have = set(map(tuple, done.to_numpy())) if len(done) else set()
        idx = adjclose.index
        rows = []
        for run_id, t, snap in cand.itertuples(index=False):
            if t not in adjclose.columns:
                continue
            k0 = idx.searchsorted(pd.Timestamp(snap), side="right") - 1
            if k0 < 0:
                continue
            for h in horizons:
                if (run_id, t, h) in have or k0 + h >= len(idx):
                    continue
                path = adjclose[t].iloc[k0:k0 + h + 1].to_numpy(float)
                if np.isnan(path).any():
                    continue
                ret = path[-1] / path[0] - 1
                mdd = float((path / np.maximum.accumulate(path) - 1).min())
                b = bench.iloc[k0:k0 + h + 1].to_numpy(float)
                rel = ret - (b[-1] / b[0] - 1) if not np.isnan(b).any() else None
                rows.append((run_id, t, h, float(ret), mdd, rel, str(idx[k0 + h].date())))
        if rows:
            self.con.executemany("INSERT INTO candidate_outcomes VALUES (?,?,?,?,?,?,?)", rows)
        return len(rows)
