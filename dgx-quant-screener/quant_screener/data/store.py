"""Persistence: parquet price cache + SQLite learning database (spec §31-33, §47).

The SQLite DB is append-only for predictions: historical rows are NEVER updated
after outcomes are observed — outcomes live in their own table keyed by
(prediction_id, horizon).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pandas as pd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    run_timestamp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    payload TEXT NOT NULL,           -- full JSON snapshot of every score/signal
    final_score REAL,
    confidence REAL,
    final_rank INTEGER,
    selected INTEGER NOT NULL,       -- 1 selected / 0 rejected finalist
    model_version TEXT NOT NULL,
    frozen INTEGER NOT NULL DEFAULT 1,
    UNIQUE(run_date, ticker)
);
CREATE TABLE IF NOT EXISTS outcomes (
    prediction_id INTEGER NOT NULL,
    horizon_days INTEGER NOT NULL,
    scored_at TEXT NOT NULL,
    ret REAL, mae REAL, mfe REAL, vol REAL, drawdown REAL, bench_rel_ret REAL,
    PRIMARY KEY (prediction_id, horizon_days),
    FOREIGN KEY (prediction_id) REFERENCES predictions(prediction_id)
);
CREATE TABLE IF NOT EXISTS screen_performance (
    run_date TEXT NOT NULL,
    screen_kind TEXT NOT NULL,       -- fundamental | technical
    screen_name TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    oos_return REAL, alpha REAL, sharpe REAL, sortino REAL, cvar REAL,
    drawdown REAL, hit_rate REAL, information_coefficient REAL, n_obs INTEGER,
    PRIMARY KEY (run_date, screen_kind, screen_name, horizon_days)
);
CREATE TABLE IF NOT EXISTS model_changelog (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    old_version TEXT NOT NULL,
    new_version TEXT NOT NULL,
    change TEXT NOT NULL,
    reason TEXT NOT NULL,
    oos_evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_weights (
    kind TEXT NOT NULL,              -- fundamental_screens | technical | final_score
    name TEXT NOT NULL,
    weight REAL NOT NULL,
    model_version TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (kind, name)
);
"""


class Store:
    def __init__(self, storage_root: str | Path):
        self.root = Path(storage_root)
        self.cache_dir = self.root / "cache"
        self.snapshot_dir = self.root / "snapshots"
        self.report_dir = self.root / "reports"
        for d in (self.cache_dir, self.snapshot_dir, self.report_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "learning.db"
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- parquet price cache ----------
    def cache_path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("^", "IDX_").replace("=", "_")
        return self.cache_dir / f"{safe}.parquet"

    def load_cached(self, key: str) -> pd.DataFrame | None:
        p = self.cache_path(key)
        if p.exists():
            try:
                return pd.read_parquet(p)
            except Exception:
                p.unlink(missing_ok=True)
        return None

    def save_cache(self, key: str, df: pd.DataFrame) -> None:
        if df is not None and len(df):
            df.to_parquet(self.cache_path(key))

    # ---------- predictions (append-only, spec §31) ----------
    def save_prediction(self, run_date: dt.date, run_ts: dt.datetime, ticker: str,
                        payload: dict, final_score: float | None, confidence: float | None,
                        final_rank: int | None, selected: bool, model_version: str) -> int:
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO predictions
               (run_date, run_timestamp, ticker, payload, final_score, confidence,
                final_rank, selected, model_version)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_date.isoformat(), run_ts.isoformat(), ticker,
             json.dumps(payload, default=str), final_score, confidence,
             final_rank, int(selected), model_version),
        )
        self._conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = self._conn.execute(
            "SELECT prediction_id FROM predictions WHERE run_date=? AND ticker=?",
            (run_date.isoformat(), ticker)).fetchone()
        return row[0]

    def unscored_predictions(self, horizon_days: int, matured_before: dt.date) -> pd.DataFrame:
        q = """SELECT p.* FROM predictions p
               LEFT JOIN outcomes o ON o.prediction_id = p.prediction_id
                                    AND o.horizon_days = ?
               WHERE o.prediction_id IS NULL AND p.run_date <= ?"""
        return pd.read_sql_query(q, self._conn,
                                 params=(horizon_days, matured_before.isoformat()))

    def save_outcome(self, prediction_id: int, horizon_days: int, metrics: dict) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO outcomes
               (prediction_id, horizon_days, scored_at, ret, mae, mfe, vol, drawdown, bench_rel_ret)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (prediction_id, horizon_days, dt.datetime.now().isoformat(),
             metrics.get("return"), metrics.get("mae"), metrics.get("mfe"),
             metrics.get("vol"), metrics.get("drawdown"), metrics.get("bench_rel_ret")))
        self._conn.commit()

    def prediction_history(self, limit: int | None = None) -> pd.DataFrame:
        q = ("SELECT p.*, o.horizon_days, o.ret, o.bench_rel_ret FROM predictions p "
             "LEFT JOIN outcomes o ON o.prediction_id=p.prediction_id "
             "ORDER BY p.run_date DESC")
        df = pd.read_sql_query(q, self._conn)
        return df.head(limit) if limit else df

    # ---------- screen performance / adaptive weights (spec §33) ----------
    def save_screen_performance(self, run_date: dt.date, kind: str, name: str,
                                horizon: int, metrics: dict) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO screen_performance
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_date.isoformat(), kind, name, horizon,
             metrics.get("oos_return"), metrics.get("alpha"), metrics.get("sharpe"),
             metrics.get("sortino"), metrics.get("cvar"), metrics.get("drawdown"),
             metrics.get("hit_rate"), metrics.get("information_coefficient"),
             metrics.get("n_obs")))
        self._conn.commit()

    def screen_performance(self, kind: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM screen_performance WHERE screen_kind=? ORDER BY run_date",
            self._conn, params=(kind,))

    def get_active_weights(self, kind: str) -> dict[str, float]:
        rows = self._conn.execute(
            "SELECT name, weight FROM active_weights WHERE kind=?", (kind,)).fetchall()
        return {n: w for n, w in rows}

    def set_active_weights(self, kind: str, weights: dict[str, float], version: str) -> None:
        now = dt.datetime.now().isoformat()
        for name, w in weights.items():
            self._conn.execute(
                "INSERT OR REPLACE INTO active_weights VALUES (?,?,?,?,?)",
                (kind, name, float(w), version, now))
        self._conn.commit()

    # ---------- model changelog (spec §47) ----------
    def log_model_change(self, old: str, new: str, change: str, reason: str,
                         oos_evidence: str) -> None:
        self._conn.execute(
            "INSERT INTO model_changelog (date, old_version, new_version, change, reason, oos_evidence)"
            " VALUES (?,?,?,?,?,?)",
            (dt.date.today().isoformat(), old, new, change, reason, oos_evidence))
        self._conn.commit()

    def changelog(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM model_changelog ORDER BY change_id", self._conn)

    def close(self) -> None:
        self._conn.close()
