"""Operator library for the 150 technical screens (Kakushadze arXiv:1601.00991 conventions).

Every operator takes and returns *wide* pandas DataFrames — index = DatetimeIndex of
trading days, columns = tickers — and obeys three frozen rules:

1. CAUSAL: an output value at date t depends only on inputs at dates <= t.
   Rolling windows use min_periods == window (a partial window is "cannot evaluate").
2. NaN = cannot evaluate, never 0. NaN propagates; +-inf is mapped to NaN before return.
   Comparisons must go through lt/le/gt/ge/eq and where() so that a NaN operand yields
   NaN, not a silently-False branch.
3. Cross-sectional ops (rank, scale, ind_neutralize) act per date across tickers.

Windowed statistics that pandas cannot vectorize (ts_rank, ts_argmax/argmin,
decay_linear, product) run on numpy sliding windows — pandas rolling.apply is two
orders of magnitude too slow for 101 alphas x ~1000 tickers.

Convention notes (documented deviations/choices where the paper is silent):
- ts_argmax(x, d) / ts_argmin(x, d) return the 1-based position of the extremum inside
  the window counted from its *oldest* element: 1 = oldest bar, d = today. This matches
  the widely used public implementations of the 101 alphas. Higher = more recent extremum.
- ts_rank(x, d) returns the average-method rank of today's value within the window,
  scaled to (0, 1].
- decay_linear(x, d) weights today by d, yesterday by d-1, ..., normalized to sum 1.
- adv(close, volume, d) is average daily *dollar* volume (close * volume), per the paper.
- scale(x, a) rescales each date's cross-section so sum(|x|) == a.
- ind_neutralize(x, groups) demeans each date's cross-section within groups (FMP sector
  is used for all of the paper's sector/industry/subindustry levels — an approximation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "clean", "rank", "delay", "delta", "correlation", "covariance", "stddev", "ts_sum",
    "product", "ts_min", "ts_max", "ts_argmax", "ts_argmin", "ts_rank",
    "decay_linear", "scale", "signedpower", "sign", "log", "abs_", "min_", "max_",
    "lt", "le", "gt", "ge", "eq", "or_", "and_", "not_", "where",
    "ind_neutralize", "adv",
]


def clean(x: pd.DataFrame) -> pd.DataFrame:
    """Map +-inf to NaN (div by zero in raw formula arithmetic, log(0), ...).

    Screen/alpha implementations should wrap their final expression in clean()
    whenever the formula contains a division not already inside a cleaning op.
    """
    return x.replace([np.inf, -np.inf], np.nan)


_clean = clean  # internal alias used by the operators below


# ---------------------------------------------------------------- cross-sectional

def rank(x: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank per date in (0, 1]. NaN stays NaN."""
    return x.rank(axis=1, pct=True, method="average")


def scale(x: pd.DataFrame, a: float = 1.0) -> pd.DataFrame:
    """Rescale each date's cross-section so sum(|x|) == a."""
    denom = x.abs().sum(axis=1)
    return _clean(x.mul(a).div(denom.replace(0.0, np.nan), axis=0))


def ind_neutralize(x: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    """Subtract the per-date group mean (groups: ticker -> label, e.g. sector)."""
    g = groups.reindex(x.columns)
    labeled = g.notna()
    xt = x.loc[:, labeled.values].T                     # ticker x date
    means = xt.groupby(g[labeled.values]).transform("mean").T
    out = x.copy()
    out.loc[:, labeled.values] = x.loc[:, labeled.values] - means
    out.loc[:, ~labeled.values] = np.nan                # unknown group: cannot evaluate
    return out


# ---------------------------------------------------------------- time-series (pandas)

def delay(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.shift(d)


def delta(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x - x.shift(d)


def correlation(x: pd.DataFrame, y: pd.DataFrame, d: int) -> pd.DataFrame:
    return _clean(x.rolling(d, min_periods=d).corr(y))


def covariance(x: pd.DataFrame, y: pd.DataFrame, d: int) -> pd.DataFrame:
    return _clean(x.rolling(d, min_periods=d).cov(y))


def stddev(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).std()


def ts_sum(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).sum()


def ts_min(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).min()


def ts_max(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).max()


def adv(close: pd.DataFrame, volume: pd.DataFrame, d: int) -> pd.DataFrame:
    """Average daily dollar volume over the past d days."""
    return (close * volume).rolling(d, min_periods=d).mean()


# ---------------------------------------------------------------- time-series (numpy windows)

def _sliding(x: pd.DataFrame, d: int):
    """(T-d+1, N, d) windows over time, or None if the frame is too short."""
    a = x.to_numpy(dtype=float)
    if a.shape[0] < d:
        return None
    return np.lib.stride_tricks.sliding_window_view(a, d, axis=0)


def _window_op(x: pd.DataFrame, d: int, fn) -> pd.DataFrame:
    """Apply fn(windows)-> (T-d+1, N) with any-NaN-in-window -> NaN."""
    out = np.full(x.shape, np.nan)
    w = _sliding(x, d)
    if w is not None:
        vals = fn(w)
        vals = np.where(np.isnan(w).any(axis=-1), np.nan, vals)
        out[d - 1:] = vals
    return pd.DataFrame(out, index=x.index, columns=x.columns)


def ts_argmax(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """1-based position of the window max: 1 = oldest bar, d = today."""
    return _window_op(x, d, lambda w: np.argmax(w, axis=-1) + 1.0)


def ts_argmin(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """1-based position of the window min: 1 = oldest bar, d = today."""
    return _window_op(x, d, lambda w: np.argmin(w, axis=-1) + 1.0)


def ts_rank(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Average-method rank of today's value inside the window, scaled to (0, 1]."""
    def _fn(w):
        last = w[..., -1:]
        less = (w < last).sum(axis=-1)
        equal = (w == last).sum(axis=-1)
        return (less + (equal + 1.0) / 2.0) / w.shape[-1]
    return _window_op(x, d, _fn)


def decay_linear(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Linearly-decayed weighted mean: today weighted d, oldest weighted 1."""
    wts = np.arange(1.0, d + 1.0)
    wts /= wts.sum()
    return _window_op(x, d, lambda w: (w * wts).sum(axis=-1))


def product(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return _window_op(x, d, lambda w: np.prod(w, axis=-1))


# ---------------------------------------------------------------- elementwise

def signedpower(x: pd.DataFrame, a: float) -> pd.DataFrame:
    return np.sign(x) * x.abs() ** a


def sign(x: pd.DataFrame) -> pd.DataFrame:
    return np.sign(x)


def log(x: pd.DataFrame) -> pd.DataFrame:
    """Natural log; non-positive input -> NaN (cannot evaluate)."""
    if not isinstance(x, pd.DataFrame):
        x = pd.DataFrame(x)
    return _clean(np.log(x.where(x > 0)))


def abs_(x: pd.DataFrame) -> pd.DataFrame:
    return x.abs()


def _align(x, y):
    """Broadcast scalars; align frames. Returns (x, y, either_nan_mask)."""
    if isinstance(x, pd.DataFrame) and isinstance(y, pd.DataFrame):
        nan = x.isna() | y.isna()
    elif isinstance(x, pd.DataFrame):
        nan = x.isna()
    elif isinstance(y, pd.DataFrame):
        nan = y.isna()
    else:
        raise TypeError("at least one operand must be a DataFrame")
    return x, y, nan


def min_(x, y) -> pd.DataFrame:
    x, y, nan = _align(x, y)
    return pd.DataFrame(np.minimum(x, y)).mask(nan)


def max_(x, y) -> pd.DataFrame:
    x, y, nan = _align(x, y)
    return pd.DataFrame(np.maximum(x, y)).mask(nan)


# ---------------------------------------------------------------- NaN-safe comparisons

def _cmp(x, y, op) -> pd.DataFrame:
    """Comparison as float 1.0/0.0 with NaN where either operand is NaN."""
    x, y, nan = _align(x, y)
    res = op(x, y)
    return pd.DataFrame(res).astype(float).mask(nan)


def lt(x, y):
    return _cmp(x, y, lambda a, b: a < b)


def le(x, y):
    return _cmp(x, y, lambda a, b: a <= b)


def gt(x, y):
    return _cmp(x, y, lambda a, b: a > b)


def ge(x, y):
    return _cmp(x, y, lambda a, b: a >= b)


def eq(x, y):
    return _cmp(x, y, lambda a, b: a == b)


def or_(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Logical OR on 1/0/NaN frames; NaN in either operand -> NaN."""
    nan = a.isna() | b.isna()
    return ((a > 0) | (b > 0)).astype(float).mask(nan)


def and_(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    nan = a.isna() | b.isna()
    return ((a > 0) & (b > 0)).astype(float).mask(nan)


def not_(a: pd.DataFrame) -> pd.DataFrame:
    return (1.0 - (a > 0).astype(float)).mask(a.isna())


def where(cond: pd.DataFrame, a, b) -> pd.DataFrame:
    """Ternary `cond ? a : b`. cond is a 1/0/NaN frame from lt/gt/...; NaN cond -> NaN."""
    if not isinstance(a, pd.DataFrame):
        a = pd.DataFrame(np.full(cond.shape, float(a)), index=cond.index, columns=cond.columns)
    if not isinstance(b, pd.DataFrame):
        b = pd.DataFrame(np.full(cond.shape, float(b)), index=cond.index, columns=cond.columns)
    out = a.where(cond > 0, b)
    return out.mask(cond.isna())


# ---------------------------------------------------------------- golden selftest

def selftest() -> int:
    """Hand-computed golden values on toy frames for every operator."""
    idx = pd.date_range("2024-01-01", periods=6, freq="B")
    A = pd.DataFrame({"X": [1.0, 2, 3, 4, 5, 6],
                      "Y": [6.0, 5, 4, 3, 2, 1],
                      "Z": [2.0, 2, 2, 2, 2, np.nan]}, index=idx)

    def ok(name, got, want, atol=1e-9):
        got_a, want_a = np.asarray(got, dtype=float), np.asarray(want, dtype=float)
        assert got_a.shape == want_a.shape, f"{name}: shape {got_a.shape} != {want_a.shape}"
        same = np.isclose(got_a, want_a, atol=atol, equal_nan=True)
        assert same.all(), f"{name}: got {got_a}, want {want_a}"
        print(f"  ok {name}")

    # rank: row 0 of A is [1, 6, 2] -> pct ranks [1/3, 1, 2/3]
    ok("rank", rank(A).iloc[0], [1 / 3, 1.0, 2 / 3])
    # rank with NaN: last row [6, 1, NaN] -> [1.0, 0.5, NaN]
    ok("rank_nan", rank(A).iloc[-1], [1.0, 0.5, np.nan])

    ok("delay", delay(A, 2)["X"], [np.nan, np.nan, 1, 2, 3, 4])
    ok("delta", delta(A, 1)["Y"], [np.nan, -1, -1, -1, -1, -1])

    # correlation(X, Y, 3): perfectly anti-correlated -> -1 from the 3rd row on
    ok("correlation", correlation(A[["X"]], A[["Y"]].rename(columns={"Y": "X"}), 3)["X"],
       [np.nan, np.nan, -1, -1, -1, -1])
    # covariance of X with itself over 3 = var([k,k+1,k+2]) = 1
    ok("covariance", covariance(A[["X"]], A[["X"]], 3)["X"],
       [np.nan, np.nan, 1, 1, 1, 1])
    ok("stddev", stddev(A, 3)["X"], [np.nan, np.nan, 1, 1, 1, 1])
    ok("ts_sum", ts_sum(A, 2)["X"], [np.nan, 3, 5, 7, 9, 11])
    ok("product", product(A, 3)["X"], [np.nan, np.nan, 6, 24, 60, 120])
    ok("ts_min", ts_min(A, 3)["Y"], [np.nan, np.nan, 4, 3, 2, 1])
    ok("ts_max", ts_max(A, 3)["X"], [np.nan, np.nan, 3, 4, 5, 6])
    # Z has NaN at the end: any-NaN window -> NaN
    ok("ts_max_nan", ts_max(A, 3)["Z"], [np.nan, np.nan, 2, 2, 2, np.nan])

    # ts_argmax on rising X: max is today -> position d = 3
    ok("ts_argmax", ts_argmax(A, 3)["X"], [np.nan, np.nan, 3, 3, 3, 3])
    # ts_argmin on falling Y: min is today -> 3; argmax of falling Y: oldest -> 1
    ok("ts_argmin", ts_argmin(A, 3)["Y"], [np.nan, np.nan, 3, 3, 3, 3])
    ok("ts_argmax_falling", ts_argmax(A, 3)["Y"], [np.nan, np.nan, 1, 1, 1, 1])

    # ts_rank rising X: today is max of window -> rank 3/3 = 1; constant Z -> avg rank 2/3 of 3 -> (0 + (3+1)/2)/3
    ok("ts_rank", ts_rank(A, 3)["X"], [np.nan, np.nan, 1, 1, 1, 1])
    ok("ts_rank_const", ts_rank(A, 3)["Z"], [np.nan, np.nan, 2 / 3, 2 / 3, 2 / 3, np.nan])

    # decay_linear over [1,2,3] with weights [1,2,3]/6 -> (1+4+9)/6
    ok("decay_linear", decay_linear(A, 3)["X"].iloc[2], (1 + 4 + 9) / 6)

    # scale: row 0 [1, 6, 2] -> sum|.| = 9
    ok("scale", scale(A).iloc[0], [1 / 9, 6 / 9, 2 / 9])
    ok("scale_a2", scale(A, a=2.0).iloc[0], [2 / 9, 12 / 9, 4 / 9])

    B = A - 3.0  # has negatives
    ok("signedpower", signedpower(B, 2.0)["X"], [-4, -1, 0, 1, 4, 9])
    ok("sign", sign(B)["X"], [-1, -1, 0, 1, 1, 1])
    ok("log", log(A)["X"].iloc[0], 0.0)
    ok("log_nonpos", log(B)["X"].iloc[:3], [np.nan, np.nan, np.nan])
    ok("abs", abs_(B)["X"], [2, 1, 0, 1, 2, 3])

    ok("min2", min_(A[["X"]], A[["Y"]].rename(columns={"Y": "X"}))["X"], [1, 2, 3, 3, 2, 1])
    ok("max2_scalar", max_(A, 4.0)["Y"], [6, 5, 4, 4, 4, 4])
    ok("max2_nan", max_(A, 1.0)["Z"], [2, 2, 2, 2, 2, np.nan])

    # comparisons: NaN operand -> NaN, not False
    ok("lt", lt(A["X"].to_frame(), 3.5)["X"], [1, 1, 1, 0, 0, 0])
    ok("gt_nan", gt(A[["Z"]], 1.0)["Z"], [1, 1, 1, 1, 1, np.nan])
    ok("eq", eq(A[["Z"]], 2.0)["Z"], [1, 1, 1, 1, 1, np.nan])

    c1 = gt(A[["Z"]], 1.0)
    c0 = lt(A[["Z"]], 1.0)
    ok("or", or_(c1, c0)["Z"], [1, 1, 1, 1, 1, np.nan])
    ok("and", and_(c1, c0)["Z"], [0, 0, 0, 0, 0, np.nan])
    ok("not", not_(c1)["Z"], [0, 0, 0, 0, 0, np.nan])

    # where: cond NaN -> NaN even if branches are fine
    w = where(gt(A[["Z"]], 1.0), A[["X"]].rename(columns={"X": "Z"}), -1.0)
    ok("where", w["Z"], [1, 2, 3, 4, 5, np.nan])
    ok("where_scalar", where(lt(A[["X"]], 3.5), 1.0, -1.0)["X"], [1, 1, 1, -1, -1, -1])

    # ind_neutralize: groups {X,Y}:g1, {Z}:g2 -> row 0: X,Y demeaned by 3.5; Z by itself
    groups = pd.Series({"X": "g1", "Y": "g1", "Z": "g2"})
    ok("ind_neutralize", ind_neutralize(A, groups).iloc[0], [-2.5, 2.5, 0.0])
    # unknown group -> NaN
    ok("ind_neutralize_missing",
       ind_neutralize(A, pd.Series({"X": "g1", "Y": "g1"})).iloc[0], [-2.5, 2.5, np.nan])

    # adv: dollar volume mean
    vol = pd.DataFrame(10.0, index=idx, columns=A.columns)
    ok("adv", adv(A, vol, 2)["X"], [np.nan, 15, 25, 35, 45, 55])

    # causality: shifting input shifts output (spot check on a stateful op)
    tr = ts_rank(A, 3)["X"]
    tr_shifted = ts_rank(A.shift(1), 3)["X"]
    assert np.isclose(tr.iloc[2], tr_shifted.iloc[3], equal_nan=True), "ts_rank not causal"
    print("  ok causality")

    print("ops.py selftest: all golden checks passed")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
