"""Earnings / PEAD screens for the ta-screener 150 catalog (Batch B, 12 screens).

Shared event mechanics (per the frozen briefing):
- A usable announcement is an earnings row with `eps_actual` present and
  date <= the last trading day. Each announcement is mapped to `pos` = the first
  trading-day position >= its calendar date.
- An event value is stamped at its first *causal* availability day and
  forward-filled for cfg["earnings"]["pead_hold_days"] trading days; the next
  announcement's value overwrites (a fresh non-NaN stamp stops the fill).
  Values that use post-announcement data (EAR needs day +1) are stamped at the
  first day the value is computable, so no score ever uses future data.
  Caveat: an announcement whose value is NaN (e.g. SUE without enough history)
  does not stop a previous fill — irrelevant in practice because quarterly
  spacing (~63 trading days) exceeds the 60-day hold.
- SUE = (eps_actual - eps_est) / max(std of the trailing sue_lookback_quarters
  *prior* surprises, sue_std_floor_frac * |eps_est|), requiring >= min_quarters
  prior quarters.
- EAR = stock cumulative return minus SPY's over trading-day window
  ear_window = [-1, +1] around the announcement.
- preearnings_runup is the one screen allowed to use *scheduled* future
  announcement dates (known ex ante); historical announcement dates proxy the
  ex-ante schedule for the backfilled history.

Documented deviations (also in ScreenSpec.notes):
- Announcements dated before the panel's first trading day are history-only:
  they feed trailing-quarter windows (SUE/streak/surprise-vol) but are never
  stamped — their hold window is anchored off-panel, and stamping them at
  day 0 would score a stale event as fresh.
- beat_streak's cap ("capped at 8") reuses cfg sue_lookback_quarters=8 so no
  magic number lives in code. An event with a NaN eps_est stamps NaN (cannot
  evaluate) and conservatively breaks the streak for later events.
- pead_car_smallcap: a CAR window containing a missing abnormal return is NaN
  (cannot evaluate) — missing days are never counted as 0 contribution.
- surprise_volatility: the literal "std of trailing SUEs" needs >= 8 events per
  name (4 prior quarters to define each SUE + 4 SUEs for the std) — zero
  coverage on the 400-day synthetic fixture and very thin on 3y real history.
  Implemented as -std of the trailing surprise_vol_quarters *scaled* surprises
  (eps_actual - eps_est)/|eps_est| (min_periods = min_quarters), the same
  series SUE standardizes, scale-free across names.
- announcement_volume_shock uses mean *dollar* volume over [ann, ann+1] over
  dollar adv (panel.adv), keeping numerator and denominator unit-consistent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from panel import Panel, load_config, synthetic_panel
from screen_lib import MissingInputError, ScreenSpec

# Conditional screens: legitimately sparse coverage -> selftest fallback
# assertion (>= 2 non-NaN names on some recent date) per the briefing.
FALLBACK_COVERAGE = ("beat_and_drop", "double_beat", "preearnings_runup")


# ------------------------------------------------------------- event plumbing

def _events(panel: Panel) -> pd.DataFrame:
    """Usable past announcements with trading-day `pos` and column index `col`.

    Announcements dated before the panel's first trading day keep their row
    (they feed trailing-quarter history for SUE / streak / surprise-vol) but get
    pos = -1 so `_stamp` never scores them: their true hold window is anchored
    off-panel, and stamping them at day 0 would misdate the event.
    """
    e = panel.require_earnings()
    idx = panel.close.index
    cols = pd.Index(panel.close.columns)
    e = e[e["eps_actual"].notna() & (e["date"] <= idx[-1]) & e["ticker"].isin(cols)]
    e = e.sort_values(["ticker", "date"]).reset_index(drop=True)
    e["pos"] = idx.searchsorted(e["date"].to_numpy())
    e.loc[e["date"] < idx[0], "pos"] = -1
    e = e[e["pos"] < len(idx)].reset_index(drop=True)
    e["col"] = cols.get_indexer(e["ticker"])
    return e


def _stamp(panel: Panel, pos: np.ndarray, col: np.ndarray, values: np.ndarray,
           hold: int) -> pd.DataFrame:
    """Place per-event values at (pos, col), forward-fill for `hold` trading days.

    Non-finite values are dropped (NaN = cannot evaluate; inf = bad division).
    Events are assumed sorted by (ticker, date) so a later event at the same
    cell wins, and a newer stamp overwrites an older fill.
    """
    T, N = panel.close.shape
    pos = np.asarray(pos, dtype=int)
    col = np.asarray(col, dtype=int)
    vals = np.asarray(values, dtype=float)
    arr = np.full((T, N), np.nan)
    ok = (pos >= 0) & (pos < T) & (col >= 0) & np.isfinite(vals)
    arr[pos[ok], col[ok]] = vals[ok]
    frame = pd.DataFrame(arr, index=panel.close.index, columns=panel.close.columns)
    return frame.ffill(limit=hold - 1) if hold > 1 else frame


def _market_prices(panel: Panel, cfg: dict) -> pd.Series:
    sym = cfg["benchmarks"]["market"]
    if sym not in panel.benchmarks.columns:
        raise MissingInputError(f"benchmark {sym} missing from benchmarks")
    return panel.benchmarks[sym].reindex(panel.close.index)


# ------------------------------------------------------- per-event value math

def _sue_events(panel: Panel, cfg: dict) -> tuple[pd.DataFrame, np.ndarray]:
    """(events, SUE values). SUE needs >= min_quarters prior surprises."""
    ec = cfg["earnings"]
    ev = _events(panel)
    surp = ev["eps_actual"] - ev["eps_est"]
    prior_std = surp.groupby(ev["ticker"]).transform(
        lambda s: s.shift(1).rolling(ec["sue_lookback_quarters"],
                                     min_periods=ec["min_quarters"]).std())
    denom = np.maximum(prior_std.to_numpy(dtype=float),
                       ec["sue_std_floor_frac"] * ev["eps_est"].abs().to_numpy(dtype=float))
    with np.errstate(divide="ignore", invalid="ignore"):
        sue = surp.to_numpy(dtype=float) / denom
    return ev, sue


def _ear_events(panel: Panel, cfg: dict,
                ev: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(EAR values, availability positions). EAR over ear_window=[start, end]:
    cumulated stock return minus SPY's, i.e. price relatives from the close at
    pos+start-1 to the close at pos+end. Available (causal) at pos+max(end, 0)."""
    start, end = (int(v) for v in cfg["earnings"]["ear_window"])
    a = panel.adjclose.to_numpy(dtype=float)
    m = _market_prices(panel, cfg).to_numpy(dtype=float)
    T = a.shape[0]
    p = ev["pos"].to_numpy(dtype=int)
    c = ev["col"].to_numpy(dtype=int)
    lo, hi = p + start - 1, p + end
    ok = (p >= 0) & (lo >= 0) & (hi <= T - 1)   # p = -1 marks pre-panel history rows
    vals = np.full(len(ev), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        srel = a[hi[ok], c[ok]] / a[lo[ok], c[ok]]
        mrel = m[hi[ok]] / m[lo[ok]]
    vals[ok] = srel - mrel
    return vals, p + max(end, 0)


# ------------------------------------------------------- shared score frames

def _sue_frame(panel: Panel, cfg: dict) -> pd.DataFrame:
    ev, sue = _sue_events(panel, cfg)
    return _stamp(panel, ev["pos"].to_numpy(), ev["col"].to_numpy(), sue,
                  cfg["earnings"]["pead_hold_days"])


def _ear_frame(panel: Panel, cfg: dict) -> pd.DataFrame:
    ev = _events(panel)
    vals, avail = _ear_events(panel, cfg, ev)
    return _stamp(panel, avail, ev["col"].to_numpy(), vals,
                  cfg["earnings"]["pead_hold_days"])


def _rev_frame(panel: Panel, cfg: dict) -> pd.DataFrame:
    ev = _events(panel)
    with np.errstate(divide="ignore", invalid="ignore"):
        vals = ((ev["rev_actual"] - ev["rev_est"])
                / ev["rev_est"].abs()).to_numpy(dtype=float)
    return _stamp(panel, ev["pos"].to_numpy(), ev["col"].to_numpy(), vals,
                  cfg["earnings"]["pead_hold_days"])


def _streak_frame(panel: Panel, cfg: dict) -> pd.DataFrame:
    ec = cfg["earnings"]
    ev = _events(panel)
    est_ok = ev["eps_est"].notna()
    # NaN estimate: the event itself cannot be evaluated (stamped NaN below);
    # for later events it conservatively breaks the streak (unknown != beat).
    beat = (ev["eps_actual"] > ev["eps_est"]) & est_ok
    cum = beat.groupby(ev["ticker"]).cumsum()
    base = cum.where(~beat).groupby(ev["ticker"]).ffill().fillna(0)
    streak = (cum - base).clip(upper=ec["sue_lookback_quarters"]).astype(float)
    streak[~est_ok] = np.nan
    return _stamp(panel, ev["pos"].to_numpy(), ev["col"].to_numpy(),
                  streak.to_numpy(), ec["pead_hold_days"])


def _zscore(x: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z per date."""
    return ops.clean(x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1), axis=0))


# ---------------------------------------------------------------- the screens

def sue_decile(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Standardized unexpected earnings, held pead_hold_days (Bernard-Thomas)."""
    return _sue_frame(panel, cfg)


def ear_3day(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Earnings announcement abnormal return over ear_window vs SPY (Kishore et al.)."""
    return _ear_frame(panel, cfg)


def sue_ear_combo(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Mean of per-date cross-sectional pct-ranks of SUE and EAR."""
    return (ops.rank(_sue_frame(panel, cfg)) + ops.rank(_ear_frame(panel, cfg))) / 2.0


def beat_and_drop(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Where SUE>0 and EAR<0: rank(SUE)*(1-rank(EAR)); else NaN."""
    sue = _sue_frame(panel, cfg)
    ear = _ear_frame(panel, cfg)
    cond = ops.and_(ops.gt(sue, 0.0), ops.lt(ear, 0.0))
    raw = ops.rank(sue) * (1.0 - ops.rank(ear))
    return ops.where(cond, raw, np.nan)


def revenue_surprise(panel: Panel, cfg: dict) -> pd.DataFrame:
    """(rev_actual - rev_est)/|rev_est| held pead_hold_days (Jegadeesh-Livnat)."""
    return _rev_frame(panel, cfg)


def double_beat(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Both surprises positive: min(rank(SUE), rank(rev_surprise)); else NaN."""
    sue = _sue_frame(panel, cfg)
    rev = _rev_frame(panel, cfg)
    cond = ops.and_(ops.gt(sue, 0.0), ops.gt(rev, 0.0))
    return ops.where(cond, ops.min_(ops.rank(sue), ops.rank(rev)), np.nan)


def beat_streak(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Consecutive quarters with eps_actual > eps_est, capped (Loh-Warachka)."""
    return _streak_frame(panel, cfg)


def pead_car_smallcap(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Running post-announcement CAR vs SPY (frozen after car_days), blended
    with a small-cap tilt: (1-w)*rank(CAR) + w*rank(-cap)."""
    ec = cfg["earnings"]
    hold, car_days, w = ec["pead_hold_days"], ec["car_days"], ec["car_small_cap_weight"]
    ev = _events(panel)
    ev = ev[ev["pos"] >= 0].reset_index(drop=True)   # stampable events only
    mret = _market_prices(panel, cfg).pct_change()
    abn = ops.clean(np.log1p(panel.returns).sub(np.log1p(mret), axis=0))
    # cumsum with NaN->0 is only an anchor-difference device; the nancum mask
    # below restores NaN discipline (a window containing a missing abnormal
    # return is "cannot evaluate", never "contributed 0").
    cum = abn.fillna(0.0).cumsum()
    nancum = abn.isna().cumsum().astype(float)
    p = ev["pos"].to_numpy(dtype=int)
    c = ev["col"].to_numpy(dtype=int)
    anchor = _stamp(panel, p, c, cum.to_numpy()[p, c], hold)
    nan_anchor = _stamp(panel, p, c, nancum.to_numpy()[p, c], hold)
    start = _stamp(panel, p, c, p.astype(float), hold)
    rowpos = pd.DataFrame(
        np.broadcast_to(np.arange(len(cum), dtype=float)[:, None], cum.shape).copy(),
        index=cum.index, columns=cum.columns)
    dsa = rowpos - start                      # trading days since announcement
    running = (cum - anchor).where(dsa <= car_days)
    running_nan = (nancum - nan_anchor).where(dsa <= car_days)
    car = running.ffill(limit=hold).where(anchor.notna())   # freeze after car_days
    nan_frozen = running_nan.ffill(limit=hold).where(anchor.notna())
    car = car.where(nan_frozen <= 0)          # gap inside the (frozen) window -> NaN
    cap = panel.cap().reindex(car.columns)
    negcap = pd.DataFrame(
        np.broadcast_to(-cap.to_numpy(dtype=float), car.shape).copy(),
        index=car.index, columns=car.columns).where(car.notna())
    return (1.0 - w) * ops.rank(car) + w * ops.rank(negcap)


def preearnings_runup(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Names with an announcement within runup_days trading days ahead:
    trailing runup_days abnormal return vs SPY; else NaN. Uses the ex-ante
    schedule (the documented causality exception); historical announcement
    dates proxy the schedule for the backfilled history."""
    runup = cfg["earnings"]["runup_days"]
    e = panel.require_earnings()
    idx = panel.close.index
    cols = pd.Index(panel.close.columns)
    e = e[e["ticker"].isin(cols)]
    d = pd.to_datetime(e["date"]).to_numpy()
    pos = np.asarray(idx.searchsorted(d), dtype=int)
    last = idx.to_numpy()[-1]
    beyond = d > last
    if beyond.any():   # scheduled dates past the panel: business-day distance
        extra = np.busday_count(last.astype("datetime64[D]"),
                                d[beyond].astype("datetime64[D]"))
        pos[beyond] = (len(idx) - 1) + extra
    col = cols.get_indexer(e["ticker"])
    T, N = panel.close.shape
    mask = np.zeros((T, N), dtype=bool)
    for p_, c_ in zip(pos, col):
        lo, hi = max(p_ - runup, 0), min(p_ - 1, T - 1)
        if c_ >= 0 and lo <= hi:
            mask[lo:hi + 1, c_] = True
    mp = _market_prices(panel, cfg)
    score = ops.clean((panel.adjclose / panel.adjclose.shift(runup))
                      .sub(mp / mp.shift(runup), axis=0))
    return score.where(pd.DataFrame(mask, index=idx, columns=panel.close.columns))


def surprise_volatility(panel: Panel, cfg: dict) -> pd.DataFrame:
    """-std of trailing surprise_vol_quarters scaled surprises (low = better)."""
    ec = cfg["earnings"]
    ev = _events(panel)
    with np.errstate(divide="ignore", invalid="ignore"):
        scaled = (ev["eps_actual"] - ev["eps_est"]) / ev["eps_est"].abs()
    scaled = scaled.replace([np.inf, -np.inf], np.nan)
    vol = scaled.groupby(ev["ticker"]).transform(
        lambda s: s.rolling(ec["surprise_vol_quarters"],
                            min_periods=ec["min_quarters"]).std())
    return _stamp(panel, ev["pos"].to_numpy(), ev["col"].to_numpy(),
                  (-vol).to_numpy(dtype=float), ec["pead_hold_days"])


def earnings_momentum_composite(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Equal-weight blend of per-date cross-sectional z-scores of SUE, EAR,
    revenue surprise and beat streak (linearized Ye-Schuller template)."""
    frames = (_sue_frame(panel, cfg), _ear_frame(panel, cfg),
              _rev_frame(panel, cfg), _streak_frame(panel, cfg))
    zs = [_zscore(f) for f in frames]
    return ops.clean(sum(zs) / float(len(zs)))


def announcement_volume_shock(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Mean dollar volume over [ann, ann+1] / adv at announcement, signed by
    sign(EAR) (Gervais-Kaniel-Mingelgrin high-volume premium)."""
    ec = cfg["earnings"]
    ev = _events(panel)
    ear_vals, ear_avail = _ear_events(panel, cfg, ev)
    p = ev["pos"].to_numpy(dtype=int)
    c = ev["col"].to_numpy(dtype=int)
    T = len(panel.close.index)
    dollar = (panel.close * panel.volume).to_numpy(dtype=float)
    advf = panel.adv(cfg["adv_days"]).to_numpy(dtype=float)
    vals = np.full(len(ev), np.nan)
    ok = (p >= 0) & (p + 1 <= T - 1)          # p = -1 marks pre-panel history rows
    with np.errstate(divide="ignore", invalid="ignore"):
        num = (dollar[p[ok], c[ok]] + dollar[p[ok] + 1, c[ok]]) / 2.0
        vals[ok] = num / advf[p[ok], c[ok]]
    vals = vals * np.sign(ear_vals)
    avail = np.maximum(ear_avail, p + 1)      # needs day-after volume and EAR
    return _stamp(panel, avail, c, vals, ec["pead_hold_days"])


# --------------------------------------------------------------------- specs

SPECS = [
    ScreenSpec(
        key="sue_decile", family="earnings",
        title="Standardized unexpected earnings (SUE)",
        citation="Bernard & Thomas (1989 JAR); survey arXiv:2606.29734",
        arxiv=None, runner=sue_decile,
        validation=1.0, us_applicability=0.7, persistence=0.3, overfit_risk=0.2,
        perf_bucket=0.6, turnover="low", needs=("earnings",),
        notes="usa 0.7 + per 0.3 encode Martineau (2021): PEAD absent for "
              "non-microcaps since ~2006"),
    ScreenSpec(
        key="ear_3day", family="earnings",
        title="Earnings announcement return (EAR, [-1,+1] vs SPY)",
        citation="Kishore, Brandt, Santa-Clara, Venkatachalam (2008), SSRN 909563",
        arxiv=None, runner=ear_3day,
        validation=0.8, us_applicability=1.0, persistence=0.6, overfit_risk=0.2,
        perf_bucket=0.6, turnover="low", needs=("earnings", "benchmarks"),
        notes="7.55%/yr abnormal, non-reversing per paper; stamped at the "
              "first causal day (ann+1)"),
    ScreenSpec(
        key="sue_ear_combo", family="earnings",
        title="SUE + EAR combined cross-sectional rank",
        citation="Kishore et al. (2008), SSRN 909563 + Bernard & Thomas (1989 JAR)",
        arxiv=None, runner=sue_ear_combo,
        validation=0.8, us_applicability=0.9, persistence=0.6, overfit_risk=0.3,
        perf_bucket=0.8, turnover="low", needs=("earnings", "benchmarks"),
        notes="combined ~12.5%/yr per paper; mean of per-date pct-ranks of the "
              "SUE and EAR frames"),
    ScreenSpec(
        key="beat_and_drop", family="earnings",
        title="Beat-and-drop divergence (SUE>0, EAR<0)",
        citation="Kishore et al. EAR-SUE divergence; overreaction literature",
        arxiv=None, runner=beat_and_drop,
        validation=0.5, us_applicability=0.9, persistence=0.5, overfit_risk=0.5,
        perf_bucket=0.4, turnover="low", needs=("earnings", "benchmarks"),
        notes="the user's research cohort: beats the market sold; higher = "
              "stronger divergence; conditional screen (fallback coverage)"),
    ScreenSpec(
        key="revenue_surprise", family="earnings",
        title="Revenue surprise",
        citation="Jegadeesh & Livnat (2006 JAE)",
        arxiv=None, runner=revenue_surprise,
        validation=1.0, us_applicability=0.8, persistence=0.5, overfit_risk=0.2,
        perf_bucket=0.6, turnover="low", needs=("earnings",),
        notes="(rev_actual - rev_est)/|rev_est| held pead_hold_days"),
    ScreenSpec(
        key="double_beat", family="earnings",
        title="Double beat (EPS and revenue)",
        citation="Jegadeesh & Livnat (2006 JAE)",
        arxiv=None, runner=double_beat,
        validation=0.6, us_applicability=0.9, persistence=0.5, overfit_risk=0.3,
        perf_bucket=0.6, turnover="low", needs=("earnings",),
        notes="min(rank(SUE), rank(rev_surprise)) where both beat; conditional "
              "screen (fallback coverage)"),
    ScreenSpec(
        key="beat_streak", family="earnings",
        title="Consecutive EPS beat streak",
        citation="Loh & Warachka (2012 MS)",
        arxiv=None, runner=beat_streak,
        validation=0.7, us_applicability=0.9, persistence=0.5, overfit_risk=0.3,
        perf_bucket=0.6, turnover="low", needs=("earnings",),
        notes="cap of 8 reuses cfg sue_lookback_quarters (no magic numbers)"),
    ScreenSpec(
        key="pead_car_smallcap", family="earnings",
        title="PEAD running CAR vs SPY, small-cap tilted",
        citation="Bernard & Thomas (1989 JAR) + Martineau (2021) small-cap "
                 "concentration",
        arxiv=None, runner=pead_car_smallcap,
        validation=0.8, us_applicability=0.6, persistence=0.3, overfit_risk=0.3,
        perf_bucket=0.4, turnover="low", needs=("earnings", "benchmarks", "cap"),
        notes="CAR cumulates post-announcement (log) returns, frozen after "
              "car_days; blend (1-w)*rank(CAR) + w*rank(-cap)"),
    ScreenSpec(
        key="preearnings_runup", family="earnings",
        title="Pre-earnings run-up (announcement premium)",
        citation="Frazzini & Lamont (2007) earnings announcement premium",
        arxiv=None, runner=preearnings_runup,
        validation=0.7, us_applicability=0.9, persistence=0.5, overfit_risk=0.4,
        perf_bucket=0.6, turnover="medium", needs=("earnings", "benchmarks"),
        notes="uses future scheduled dates — legitimately ex-ante info; "
              "conditional screen (fallback coverage)"),
    ScreenSpec(
        key="surprise_volatility", family="earnings",
        title="Earnings surprise volatility (low = better)",
        citation="Cao & Narayanamoorthy (2012) earnings volatility",
        arxiv=None, runner=surprise_volatility,
        validation=0.5, us_applicability=0.9, persistence=0.6, overfit_risk=0.4,
        perf_bucket=0.4, turnover="low", needs=("earnings",),
        notes="low uncertainty = better; -std of trailing scaled surprises "
              "((act-est)/|est|) — literal trailing-SUE std needs >=8 events "
              "per name and has no coverage on 3y history"),
    ScreenSpec(
        key="earnings_momentum_composite", family="earnings",
        title="Earnings momentum composite (z-blend)",
        citation="Ye & Schuller, arXiv:2009.03094 (linearized template)",
        arxiv="2009.03094", runner=earnings_momentum_composite,
        validation=0.6, us_applicability=0.9, persistence=0.5, overfit_risk=0.5,
        perf_bucket=0.6, turnover="low", needs=("earnings", "benchmarks"),
        notes="transparent stand-in for the XGBoost PEAD model; equal-weight "
              "z-blend of SUE, EAR, revenue surprise, beat streak"),
    ScreenSpec(
        key="announcement_volume_shock", family="earnings",
        title="Announcement volume shock, EAR-signed",
        citation="Gervais, Kaniel, Mingelgrin (2001 JF) high-volume premium",
        arxiv=None, runner=announcement_volume_shock,
        validation=0.6, us_applicability=0.8, persistence=0.5, overfit_risk=0.4,
        perf_bucket=0.4, turnover="low", needs=("earnings", "benchmarks"),
        notes="mean dollar volume over [ann, ann+1] / dollar adv at "
              "announcement (unit-consistent), signed by sign(EAR)"),
]


# ------------------------------------------------------------------- selftest

def selftest() -> int:
    panel = synthetic_panel()
    cfg = load_config()
    hold = cfg["earnings"]["pead_hold_days"]

    assert len(SPECS) == 12, f"expected 12 earnings screens, got {len(SPECS)}"
    keys = [s.key for s in SPECS]
    assert len(set(keys)) == len(keys), "duplicate screen keys"

    for spec in SPECS:
        spec.validate()
        assert spec.family == "earnings", spec.key
        assert "earnings" in spec.needs, spec.key
        score = spec.runner(panel, cfg)
        assert isinstance(score, pd.DataFrame), spec.key
        assert score.shape == panel.close.shape, (spec.key, score.shape)
        assert score.index.equals(panel.close.index), spec.key
        assert list(score.columns) == list(panel.close.columns), spec.key
        arr = score.to_numpy(dtype=float)
        assert not np.isinf(arr).any(), f"{spec.key}: inf in output"
        assert np.isfinite(arr).any(), f"{spec.key}: no evaluable score at all"
        cov = float(score.iloc[-1].notna().mean())
        if spec.key in FALLBACK_COVERAGE:
            recent = score.tail(hold).notna().sum(axis=1)
            assert (recent >= 2).any(), \
                f"{spec.key}: fallback failed (<2 names on every recent date)"
            tag = f"fallback, last-row cov {cov:.0%}"
        else:
            assert cov >= 0.25, f"{spec.key}: last-row coverage {cov:.0%} < 25%"
            tag = f"cov {cov:.0%}"
        print(f"ok {spec.key} ({tag})")

    print(f"screens_earnings.py selftest: OK — {len(SPECS)} screens; "
          f"fallback-coverage screens: {list(FALLBACK_COVERAGE)}")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
