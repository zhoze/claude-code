"""Per-stock strategy backtester with realistic execution (spec §13).

Signals form at bar t close; fills happen at bar t+1 OPEN with commission and
slippage. Metrics reported separately for in-sample and out-of-sample segments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..portfolio.stress import portfolio_metrics


@dataclass
class BacktestResult:
    strategy: str
    metrics_is: dict = field(default_factory=dict)
    metrics_oos: dict = field(default_factory=dict)
    n_trades_is: int = 0
    n_trades_oos: int = 0
    avg_trade_oos: float = np.nan
    equity: pd.Series | None = None
    positions: pd.Series | None = None


def run_backtest(px: pd.DataFrame, positions: pd.Series, strategy_name: str,
                 oos_fraction: float, commission_bps: float,
                 slippage_bps: float) -> BacktestResult:
    pos = positions.reindex(px.index).fillna(0.0).clip(0, 1)
    # execute next open: position held during day t+1 is signal from t
    held = pos.shift(1).fillna(0.0)
    open_ret = px["close"] / px["open"] - 1          # entry day: open -> close
    close_ret = px["close"].pct_change().fillna(0.0)
    entry = (held > held.shift(1).fillna(0.0))
    exit_ = (held < held.shift(1).fillna(0.0))
    # daily strategy return: full close-to-close when held both days,
    # open-to-close on entry day
    strat_ret = np.where(entry, open_ret, np.where(held > 0, close_ret, 0.0))
    cost = (commission_bps + slippage_bps) / 1e4
    strat_ret = strat_ret - (entry.astype(float) + exit_.astype(float)) * cost
    strat_ret = pd.Series(strat_ret, index=px.index)

    split = int(len(px) * (1 - oos_fraction))
    res = BacktestResult(strategy=strategy_name)
    res.positions = held
    res.equity = (1 + strat_ret).cumprod()
    seg_is, seg_oos = strat_ret.iloc[:split], strat_ret.iloc[split:]
    res.metrics_is = portfolio_metrics(seg_is[held.iloc[:split] > 0]) if (held.iloc[:split] > 0).any() else {}
    res.metrics_oos = portfolio_metrics(seg_oos[held.iloc[split:] > 0]) if (held.iloc[split:] > 0).any() else {}
    # also report full-period equity metrics on all days (cash days = 0)
    res.metrics_is["PERIOD_METRICS"] = portfolio_metrics(seg_is)
    res.metrics_oos["PERIOD_METRICS"] = portfolio_metrics(seg_oos)
    res.n_trades_is = int(entry.iloc[:split].sum())
    res.n_trades_oos = int(entry.iloc[split:].sum())

    # average trade PnL (OOS)
    trades = []
    in_pos, entry_px = False, math.nan
    opens, closes = px["open"], px["close"]
    for i in range(split, len(px)):
        if entry.iloc[i] and not in_pos:
            in_pos, entry_px = True, opens.iloc[i]
        elif exit_.iloc[i] and in_pos:
            trades.append(opens.iloc[i] / entry_px - 1 - 2 * cost)
            in_pos = False
    if in_pos and math.isfinite(entry_px):
        trades.append(closes.iloc[-1] / entry_px - 1 - cost)
    res.avg_trade_oos = float(np.mean(trades)) if trades else np.nan
    return res


def robustness_score(res: BacktestResult, min_trades: int) -> tuple[float, list[str]]:
    """TECHNICAL_ROBUSTNESS_SCORE 0-100 (spec §14): OOS-first, penalizing tiny
    samples and IS/OOS divergence. Returns (score, reject_reasons)."""
    reasons: list[str] = []
    oos = res.metrics_oos.get("PERIOD_METRICS", {})
    if not oos:
        return 0.0, ["no OOS data"]
    if res.n_trades_oos < min_trades:
        reasons.append(f"only {res.n_trades_oos} OOS trades (<{min_trades})")
    sharpe = oos.get("SHARPE", np.nan)
    sortino = oos.get("SORTINO", np.nan)
    pf = oos.get("PROFIT_FACTOR", np.nan)
    dd = oos.get("MAX_DRAWDOWN", np.nan)
    cvar = oos.get("CVAR", np.nan)
    tot = oos.get("TOTAL_RETURN", np.nan)

    pts = 0.0
    pts += np.clip((sharpe or 0) * 20, -10, 30) if np.isfinite(sharpe) else 0
    pts += np.clip((sortino or 0) * 8, -5, 15) if np.isfinite(sortino) else 0
    pts += np.clip(((pf or 1) - 1) * 20, -10, 15) if np.isfinite(pf) else 0
    pts += np.clip(tot * 50, -10, 15) if np.isfinite(tot) else 0
    pts += np.clip((0.25 + (dd or 0)) * 40, -10, 10) if np.isfinite(dd) else 0   # dd is negative
    pts += np.clip((0.03 + (cvar or 0)) * 200, -10, 5) if np.isfinite(cvar) else 0
    pts += min(res.n_trades_oos, 30) / 30 * 10                                   # sample size
    # IS/OOS consistency: penalize spectacular IS with weak OOS (overfit smell)
    is_m = res.metrics_is.get("PERIOD_METRICS", {})
    if np.isfinite(is_m.get("SHARPE", np.nan)) and np.isfinite(sharpe):
        gap = is_m["SHARPE"] - sharpe
        if gap > 1.0:
            pts -= min((gap - 1.0) * 10, 15)
    score = float(np.clip(pts, 0, 100))
    if res.n_trades_oos < min_trades:
        score = min(score, 25.0)   # capped, effectively unselectable
    return score, reasons
