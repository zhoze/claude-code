import numpy as np

from quant_screener.technicals.evaluate import evaluate_finalist
from quant_screener.technicals.strategies import STRATEGY_REGISTRY
from quant_screener.technicals.backtest import run_backtest, robustness_score


def test_all_ten_strategies_produce_positions(synthetic_px):
    for name, fn in STRATEGY_REGISTRY.items():
        pos = fn(synthetic_px, {"bench_close": synthetic_px["close"] * 0.9})
        assert len(pos) == len(synthetic_px), name
        assert pos.dropna().isin([0.0, 1.0]).all(), name


def test_backtest_executes_with_costs(synthetic_px):
    pos = STRATEGY_REGISTRY["donchian_breakout"](synthetic_px, {})
    res = run_backtest(synthetic_px, pos, "donchian_breakout", 0.35, 1.0, 5.0)
    assert res.equity is not None
    assert res.n_trades_is + res.n_trades_oos > 0
    score, _ = robustness_score(res, min_trades=12)
    assert 0 <= score <= 100


def test_low_trade_count_capped(synthetic_px):
    import pandas as pd

    # a single trade over the whole OOS window must be capped as unselectable
    pos = pd.Series(0.0, index=synthetic_px.index)
    pos.iloc[-30:] = 1.0
    res = run_backtest(synthetic_px, pos, "one_trade", 0.35, 1.0, 5.0)
    score, reasons = robustness_score(res, min_trades=12)
    assert score <= 25
    assert any("OOS trades" in r for r in reasons)


def test_evaluate_finalist_full(synthetic_px, cfg):
    bench = synthetic_px["close"] * 0.95
    a = evaluate_finalist("TEST", synthetic_px, cfg, bench_close=bench)
    assert a.best_strategy in STRATEGY_REGISTRY
    assert a.signal in ("STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "AVOID")
    assert "entry_zone" in a.levels and "invalidation" in a.levels
    assert a.levels["invalidation"] < a.levels["price"]
    assert 0 <= a.technical_score <= 100
