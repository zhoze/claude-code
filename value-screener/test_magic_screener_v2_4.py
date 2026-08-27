#!/usr/bin/env python3
"""Regression tests for the four v2.4 corrections.

Each test pins a specific v2.3 defect. Run with:

    python3 -m pytest value-screener/test_magic_screener_v2_4.py -q
    python3 value-screener/test_magic_screener_v2_4.py          # no pytest needed
"""

from __future__ import annotations

import datetime as dt
import math
import random

import magic_screener_v2_4 as ms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def synthetic_bars(seed: int, n: int = 900, mu: float = 0.0, sigma: float = 0.018) -> list[ms.Bar]:
    """Geometric random walk with realistic intraday ranges. mu=0 means no edge."""
    rng = random.Random(seed)
    bars: list[ms.Bar] = []
    price = 100.0
    day = dt.date(2021, 1, 4)
    for _ in range(n):
        price *= math.exp(rng.gauss(mu, sigma))
        high = price * (1 + abs(rng.gauss(0, 0.006)))
        low = price * (1 - abs(rng.gauss(0, 0.006)))
        opn = low + (high - low) * rng.random()
        bars.append(ms.Bar(day, opn, max(high, opn, price), min(low, opn, price),
                           price, rng.uniform(1e6, 4e6)))
        day += dt.timedelta(days=1 if day.weekday() < 4 else 3)
    return bars


def bar(o: float, h: float, l: float, c: float, day: int = 2) -> ms.Bar:
    return ms.Bar(dt.date(2024, 1, day), o, h, l, c, 1e6)


# ---------------------------------------------------------------------------
# Fix 1 — ATR-normalized barriers
# ---------------------------------------------------------------------------


def test_atr_barriers_scale_with_volatility():
    """A quiet name and a wild name get the same barrier in ATR units."""
    quiet = ms._resolve_barriers(1.0, "atr", 3.0, 2.0, 0.05, 0.03)
    wild = ms._resolve_barriers(7.0, "atr", 3.0, 2.0, 0.05, 0.03)
    assert quiet is not None and wild is not None
    assert math.isclose(quiet[0], 0.03) and math.isclose(quiet[1], 0.02)
    assert math.isclose(wild[0], 0.21) and math.isclose(wild[1], 0.14)
    # The v2.3 fixed stop was 3 ATR on the quiet name and 0.43 ATR on the wild one.
    assert math.isclose(0.03 / (1.0 / 100), 3.0)
    assert math.isclose(0.03 / (7.0 / 100), 0.4285714, rel_tol=1e-5)


def test_barrier_fractions_are_clamped():
    """A degenerate ATR cannot produce an absurd barrier."""
    tiny = ms._resolve_barriers(0.001, "atr", 3.0, 2.0, 0.05, 0.03)
    huge = ms._resolve_barriers(90.0, "atr", 3.0, 2.0, 0.05, 0.03)
    assert tiny == (ms.MIN_BARRIER_FRACTION, ms.MIN_BARRIER_FRACTION)
    assert huge == (ms.MAX_BARRIER_FRACTION, ms.MAX_BARRIER_FRACTION)


def test_pct_mode_preserves_v23_behaviour():
    assert ms._resolve_barriers(2.5, "pct", 3.0, 2.0, 0.05, 0.03) == (0.05, 0.03)
    assert ms._resolve_barriers(None, "pct", 3.0, 2.0, 0.05, 0.03) == (0.05, 0.03)


def test_atr_mode_skips_signals_without_volatility():
    assert ms._resolve_barriers(None, "atr", 3.0, 2.0, 0.05, 0.03) is None
    assert ms._resolve_barriers(0.0, "atr", 3.0, 2.0, 0.05, 0.03) is None


def test_atr_barriers_reduce_stop_rate_vs_fixed_pct():
    """The v2.3 default stopped out most trades purely because 3% was ~1 ATR."""
    bars = synthetic_bars(102)
    common = dict(horizon=20, min_score=75.0, warmup=260, mode="trade")
    fixed = ms.backtest_entry_model(bars, barrier_mode="pct",
                                    profit_target=0.05, stop_loss=0.03, **common)
    scaled = ms.backtest_entry_model(bars, barrier_mode="atr",
                                     atr_target_mult=3.0, atr_stop_mult=2.0, **common)
    assert fixed["signals"] > 0 and scaled["signals"] > 0
    assert fixed["stopHitRate"] > scaled["stopHitRate"]
    assert scaled["avgStopPct"] > 0
    assert scaled["barrierMode"] == "atr"


# ---------------------------------------------------------------------------
# Fix 2 — MFE/MAE must stop at the exit
# ---------------------------------------------------------------------------


def test_excursions_exclude_the_range_after_a_stop():
    """v2.3 reported mfe=+30% on a trade that was stopped out at -3%."""
    future = [bar(100, 130, 70, 95)]
    path = ms._path_outcome(future, entry=100.0, profit_target=0.05, stop_loss=0.03)
    assert path["outcome"] == "STOP"
    assert math.isclose(path["exitPrice"], 97.0)
    # Only the open (0%) and the stop (-3%) were experienced while open.
    assert math.isclose(path["mfe"], 0.0, abs_tol=1e-9)
    assert math.isclose(path["mae"], -0.03, abs_tol=1e-9)


def test_excursions_exclude_the_range_after_a_target():
    future = [bar(100, 130, 70, 125)]
    path = ms._path_outcome(future, entry=100.0, profit_target=0.05, stop_loss=0.50)
    assert path["outcome"] == "TARGET"
    assert math.isclose(path["mfe"], 0.05, abs_tol=1e-9)
    assert math.isclose(path["mae"], 0.0, abs_tol=1e-9)


def test_gap_exit_credits_only_the_open():
    future = [bar(80, 130, 60, 90)]
    path = ms._path_outcome(future, entry=100.0, profit_target=0.05, stop_loss=0.03)
    assert path["outcome"] == "STOP" and path["hitDay"] == 1
    assert math.isclose(path["mae"], -0.20, abs_tol=1e-9)
    assert math.isclose(path["mfe"], -0.20, abs_tol=1e-9)


def test_surviving_bars_credit_the_full_range():
    """A bar that neither hits target nor stop contributes its whole range."""
    future = [bar(100, 104, 98, 103), bar(103, 104, 98, 99)]
    path = ms._path_outcome(future, entry=100.0, profit_target=0.10, stop_loss=0.10)
    assert path["outcome"] == "NEITHER"
    assert math.isclose(path["mfe"], 0.04, abs_tol=1e-9)
    assert math.isclose(path["mae"], -0.02, abs_tol=1e-9)


def test_excursions_never_exceed_the_barriers_they_exit_on():
    """Property check across random paths: MFE/MAE stay inside the barrier band."""
    rng = random.Random(5)
    for _ in range(300):
        future = []
        px = 100.0
        for d in range(1, 15):
            px *= math.exp(rng.gauss(0, 0.03))
            hi = px * (1 + abs(rng.gauss(0, 0.02)))
            lo = px * (1 - abs(rng.gauss(0, 0.02)))
            opn = lo + (hi - lo) * rng.random()
            future.append(bar(opn, max(hi, opn, px), min(lo, opn, px), px, day=d))
        p = ms._path_outcome(future, 100.0, 0.05, 0.03)
        if p["outcome"] == "TARGET":
            # Cannot have run further against us than the stop we never hit.
            assert p["mae"] > -0.03 - 1e-9, p
        elif p["outcome"] == "STOP":
            # Cannot have run further for us than the target we never hit.
            assert p["mfe"] < 0.05 + 1e-9, p


# ---------------------------------------------------------------------------
# Fix 3 — Sharpe annualization must charge for idle capital
# ---------------------------------------------------------------------------


def test_sharpe_annualizes_on_trade_frequency():
    returns = [0.02, -0.01, 0.03, -0.02, 0.01]
    sharpe_sparse, _ = ms._sharpe_sortino_approx(returns, trades_per_year=3.0)
    sharpe_dense, _ = ms._sharpe_sortino_approx(returns, trades_per_year=52.0)
    assert sharpe_sparse is not None and sharpe_dense is not None
    # Same trade distribution, but trading 3x/yr is not a 52x/yr Sharpe.
    assert sharpe_dense > sharpe_sparse
    assert math.isclose(sharpe_dense / sharpe_sparse, math.sqrt(52.0 / 3.0), rel_tol=1e-9)


def test_sharpe_rejects_degenerate_frequency():
    returns = [0.01, -0.01, 0.02]
    assert ms._sharpe_sortino_approx(returns, 0.0) == (None, None)
    assert ms._sharpe_sortino_approx(returns, None) == (None, None)
    assert ms._sharpe_sortino_approx([0.01], 5.0) == (None, None)


def test_backtest_reports_time_in_market_and_is_penalized_for_idleness():
    bars = synthetic_bars(102)
    bt = ms.backtest_entry_model(bars, horizon=20, min_score=75.0, warmup=260, mode="trade")
    assert bt["signals"] > 0
    assert 0.0 < bt["timeInMarketPct"] <= 100.0
    assert bt["tradesPerYear"] > 0
    # The v2.3 convention; the new one must not be more generous than it.
    old_basis = 252.0 / bt["avgHoldingDays"]
    assert bt["tradesPerYear"] <= old_basis + 1e-9
    if bt["sharpeApprox"] is not None and bt["sharpeApprox"] > 0:
        old_sharpe, _ = ms._sharpe_sortino_approx(
            [d["netReturn"] for d in bt["details"]], old_basis)
        assert bt["sharpeApprox"] < old_sharpe


# ---------------------------------------------------------------------------
# Fix 4 — technical coverage must be a real measurement
# ---------------------------------------------------------------------------


def test_short_history_produces_low_coverage():
    """v2.3 reported 86% coverage on 60 bars because every component faked a 50."""
    bars = synthetic_bars(7, n=1200, mu=0.0005)
    short = ms.compute_entry_model(ms.compute_technical_features(bars[:70]))
    full = ms.compute_entry_model(ms.compute_technical_features(bars))
    assert short["technicalCoverage"] < 70.0, short["technicalCoverage"]
    assert full["technicalCoverage"] >= 85.0
    assert short["technicalConfidence"] == "LOW"


def test_coverage_gate_actually_blocks_a_setup():
    bars = synthetic_bars(7, n=1200, mu=0.0005)
    short = ms.compute_entry_model(ms.compute_technical_features(bars[:70]))
    assert short["eodSetup"] == "WAIT"
    assert "technical coverage below 70%" in short["entryReasons"]


def test_absent_components_are_none_not_neutral():
    bars = synthetic_bars(7, n=1200, mu=0.0005)
    comps = ms.compute_entry_model(ms.compute_technical_features(bars[:70]))["technicalComponents"]
    # 70 bars cannot support SMA100/200, their slopes, or a 30-week trend.
    assert comps["trend"] is None, comps
    # No benchmark was supplied, so relative strength is genuinely unobserved.
    assert comps["relativeStrength"] is None, comps
    # These are all computable on 70 bars and must stay real measurements —
    # the fix is about reporting absence, not about suppressing what is present.
    for observed in ("breakout", "momentum", "volume", "oscillator"):
        assert comps[observed] is not None, (observed, comps)


def test_weekly_trend_is_none_without_enough_weekly_history():
    bars = synthetic_bars(7, n=1200, mu=0.0005)
    assert ms.compute_technical_features(bars[:70])["weeklyTrend"] is None
    assert ms.compute_technical_features(bars)["weeklyTrend"] in {"bull", "bear", "neutral"}


def test_full_history_coverage_reflects_benchmark_presence():
    bars = synthetic_bars(7, n=1200, mu=0.0005)
    without = ms.compute_entry_model(ms.compute_technical_features(bars))
    with_bm = ms.compute_entry_model(
        ms.compute_technical_features(bars, benchmark=synthetic_bars(9, n=1200, mu=0.0003)))
    assert with_bm["technicalCoverage"] > without["technicalCoverage"]
    assert math.isclose(with_bm["technicalCoverage"], 100.0)


# ---------------------------------------------------------------------------
# Guard: the fixes must not break the existing engine contract
# ---------------------------------------------------------------------------


def test_report_renders_with_missing_weekly_trend():
    bars = synthetic_bars(7, n=1200, mu=0.0005)
    text = ms.format_report(ms.analyze("TEST", bars[:70]))
    assert "Weekly trend          : n/a" in text


def test_backtest_report_renders_in_both_barrier_modes():
    bars = synthetic_bars(102)
    for mode, kwargs in (("atr", {}), ("pct", {"profit_target": 0.05, "stop_loss": 0.03})):
        bt = ms.backtest_entry_model(bars, horizon=20, min_score=75.0, warmup=260,
                                     barrier_mode=mode, **kwargs)
        text = ms.format_backtest(bt, 20, 0.05, 0.03, 75.0)
        assert "Barriers" in text
        assert ("ATR-scaled" in text) == (mode == "atr")


def test_invalid_barrier_mode_is_rejected():
    bars = synthetic_bars(102)
    try:
        ms.backtest_entry_model(bars, barrier_mode="nonsense")
    except ValueError as exc:
        assert "barrier_mode" in str(exc)
    else:
        raise AssertionError("expected ValueError")


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - standalone runner reports everything
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"ok   {name}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
