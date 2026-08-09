import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from quant_screener.data.integrity import LookAheadError, Provenance, assert_point_in_time
from quant_screener.data.store import Store
from quant_screener.regime.detector import detect_regime
from quant_screener.scoring.confidence import compute_confidence
from quant_screener.scoring.final_score import CandidateScores, compute_final_score, gate_candidate
from quant_screener.report.daily_report import render_report


def test_lookahead_guard():
    assert_point_in_time(dt.date(2026, 8, 7), dt.date(2026, 8, 6), "ok")
    with pytest.raises(LookAheadError):
        assert_point_in_time(dt.date(2026, 8, 7), dt.date(2026, 8, 8), "future record")


def test_store_predictions_append_only(tmp_path):
    store = Store(tmp_path)
    pid = store.save_prediction(dt.date(2026, 8, 7), dt.datetime(2026, 8, 7, 8, 0),
                                "AAPL", {"FINAL_SCORE": 80}, 80.0, 70.0, 1, True, "V1.0")
    # same (date, ticker) insert is ignored, id stable — history is immutable
    pid2 = store.save_prediction(dt.date(2026, 8, 7), dt.datetime(2026, 8, 7, 9, 0),
                                 "AAPL", {"FINAL_SCORE": 99}, 99.0, 99.0, 1, True, "V1.0")
    assert pid == pid2
    hist = store.prediction_history()
    assert float(hist.iloc[0]["final_score"]) == 80.0
    store.save_outcome(pid, 5, {"return": 0.03, "bench_rel_ret": 0.01})
    hist = store.prediction_history()
    assert float(hist.iloc[0]["ret"]) == 0.03
    store.close()


def test_snapshot_refuses_overwrite(tmp_path):
    from quant_screener.learning.snapshot import freeze_snapshot

    store = Store(tmp_path)
    args = (store, dt.date(2026, 8, 7), dt.datetime(2026, 8, 7, 8, 0),
            [{"TICKER": "AAPL", "FINAL_SCORE": 80, "CONFIDENCE": 70}], ["AAPL"], {})
    p = freeze_snapshot(*args)
    assert p.exists()
    with pytest.raises(FileExistsError):
        freeze_snapshot(*args)
    store.close()


def test_regime_detection_states():
    idx = pd.bdate_range("2024-01-01", periods=300)
    up = pd.Series(np.linspace(4000, 5200, 300), index=idx)
    calm = {"vix": pd.Series(14.0, index=idx), "vix3m": pd.Series(17.0, index=idx),
            "sp500": up, "russell2000": up * 0.5,
            "hyg": pd.Series(np.linspace(75, 80, 300), index=idx),
            "lqd": pd.Series(np.linspace(105, 106, 300), index=idx),
            "ust10y": pd.Series(4.2, index=idx)}
    a = detect_regime(calm, dt.date(2026, 8, 7))
    assert a.regime in ("RISK_ON", "NEUTRAL")

    down = pd.Series(np.linspace(5200, 4000, 300), index=idx)
    stressed = dict(calm)
    stressed.update({"vix": pd.Series(38.0, index=idx), "vix3m": pd.Series(30.0, index=idx),
                     "sp500": down,
                     "hyg": pd.Series(np.linspace(80, 70, 300), index=idx)})
    b = detect_regime(stressed, dt.date(2026, 8, 7))
    assert b.regime == "HIGH_STRESS"
    assert b.tilt < 0


def test_final_score_missing_evidence_does_not_inflate(cfg):
    w = dict(cfg.scoring.weights)
    full = CandidateScores(ticker="A", fundamental=80, overlap=80, ml_expected_return=80,
                           mean_cvar=80, technical=80, technical_robustness=80, macro=80,
                           options=80, liquidity=80, catalyst=80)
    sparse = CandidateScores(ticker="B", fundamental=80, overlap=80)
    assert compute_final_score(full, w) > compute_final_score(sparse, w)


def test_gate_never_lowers_standards():
    s = CandidateScores(ticker="A")
    ok = gate_candidate(s, {"in_universe": True})  # everything else missing/false
    assert not ok
    assert "has_listed_options" in s.gate_failures
    assert "technical_signal_now" in s.gate_failures


def test_confidence_independent_of_final_score():
    hi = compute_confidence(data_completeness=1, model_agreement=1,
                            historical_reliability=1, regime_similarity=1,
                            model_uncertainty=0, options_liquidity=1,
                            fundamental_overlap=10, technical_confirmed=True,
                            macro_compatible=True)
    lo = compute_confidence(data_completeness=0.2, model_agreement=0.2,
                            historical_reliability=0.5, regime_similarity=0.4,
                            model_uncertainty=0.9, options_liquidity=0.1,
                            fundamental_overlap=4, technical_confirmed=False,
                            macro_compatible=False)
    assert hi > 90 and lo < 40


def test_report_renders_no_opportunity(cfg):
    ctx = {
        "date": "2026-08-07", "run_time": "2026-08-07T07:30:00-04:00",
        "market_open": "2026-08-07T09:30:00-04:00", "data_cutoff": "2026-08-07T07:29",
        "model_version": "SCREENER_MODEL_V1.0", "backend": "cpu",
        "regime": {"regime": "NEUTRAL", "score": 0.0, "sub_regimes": {}, "notes": []},
        "dashboard": {"vix": {"value": 15.2, "change_pct": -1.0, "signal": "NEUTRAL"}},
        "global_markets": {}, "calendar": [], "screen_results": [],
        "finalists": [], "selections": [], "portfolio": None,
        "system_performance": {}, "screen_leaderboard": [], "changelog": [],
        "provenance": [],
    }
    md = render_report(ctx)
    assert "NO HIGH-CONFIDENCE OPPORTUNITY TODAY" in md
    assert "DAILY QUANTITATIVE PRE-MARKET REPORT" in md
