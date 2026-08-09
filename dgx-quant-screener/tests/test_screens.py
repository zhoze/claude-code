import numpy as np

from conftest import make_record
from quant_screener.screens.fundamental import SCREEN_REGISTRY, run_all_screens
from quant_screener.screens.ensemble import build_ensemble, overlap_tier


def _records(n_good=6, n_bad=6):
    recs = {}
    for i in range(n_good):
        recs[f"GOOD{i}"] = make_record(f"GOOD{i}", good=True)
    for i in range(n_bad):
        recs[f"BAD{i}"] = make_record(f"BAD{i}", good=False)
    return recs


def test_all_ten_screens_run():
    recs = _records()
    results = run_all_screens(recs)
    assert len(results) == 10
    for name, df in results.items():
        assert set(df.columns) == {"raw", "passed", "confidence"}, name
        assert len(df) == len(recs)


def test_good_records_outrank_bad():
    recs = _records()
    results = run_all_screens(recs)
    overlap_good = overlap_bad = 0
    for df in results.values():
        overlap_good += int(df.loc[[t for t in df.index if t.startswith("GOOD")], "passed"].sum())
        overlap_bad += int(df.loc[[t for t in df.index if t.startswith("BAD")], "passed"].sum())
    assert overlap_good > overlap_bad


def test_missing_data_never_invented():
    """A record with no metrics must produce NaN raw scores and zero-ish confidence."""
    from quant_screener.data.fundamentals import FundamentalRecord
    import datetime as dt

    empty = FundamentalRecord(ticker="EMPTY", as_of=dt.date(2026, 8, 7))
    recs = {**_records(2, 2), "EMPTY": empty}
    results = run_all_screens(recs)
    for name, df in results.items():
        assert not df.loc["EMPTY", "passed"], name
        assert df.loc["EMPTY", "confidence"] <= 0.5, name


def test_ensemble_overlap_and_finalists(cfg):
    recs = _records()
    ens = build_ensemble(run_all_screens(recs), cfg,
                         sectors={t: "Technology" for t in recs})
    assert ens.table["overlap"].max() <= 10
    assert len(ens.finalists) <= cfg.screens.max_finalists
    # finalists must clear min_overlap — never filled by loosening
    for t in ens.finalists:
        assert ens.table.loc[t, "overlap"] >= cfg.screens.min_overlap


def test_overlap_tiers():
    assert overlap_tier(10)[0] == "EXCEPTIONAL"
    assert overlap_tier(8)[0] == "EXTREMELY_STRONG"
    assert overlap_tier(6)[0] == "STRONG"
    assert overlap_tier(4)[0] == "ACCEPTABLE"
    assert overlap_tier(3)[0] == "REJECT"
    assert overlap_tier(3)[1] == 0.0
