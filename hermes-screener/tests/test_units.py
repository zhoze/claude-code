"""Fast unit tests: vendoring, consensus, optimizer, resolve, scenarios, backend, data."""
import json
import logging

import numpy as np
import pandas as pd
import pytest

from hermes import backend as bk
from hermes import vendored
from hermes.config import load_config
from hermes.data import http
from hermes.data.massive import assemble_history
from hermes.data.snapshot import corwin_schultz_bp
from hermes.stages import fundamental as fs
from hermes.stages.macro import load_macro
from hermes.stages.optimizer import (Constraints, SolverPolicy, build_model, fingerprint,
                                     optimize_portfolio, portfolio_risk, solve_highs)
from hermes.stages.resolve import resolve_final
from hermes.stages.scenarios import allocate, bootstrap_scenarios, jump_probabilities


@pytest.fixture(scope="module")
def cfg():
    return load_config(overrides={"strategy": {"optimizer": {"frontier_points": 5}}})


def test_vendored_packages_are_isolated():
    f, t = vendored.fundamental(), vendored.ta()
    assert f.screen_lib._ns.__file__ != t.screen_lib._ns.__file__
    assert hasattr(f.screen_lib, "load_joined") and hasattr(t.screen_lib, "ScreenSpec")
    rows = t.catalog.ranked(t.panel.load_config())      # lazy imports inside must resolve
    assert len(rows) == 150
    assert "screen_lib" not in __import__("sys").modules


def test_fundamental_selftest_frame_runs_all_20(cfg, tmp_path):
    frame = vendored.fundamental().screen_lib.synthetic_frame()
    for name in ("buffett_quality_input.csv", "magic_formula_input.csv",
                 "piotroski_input.csv", "extended_input.csv"):
        frame.to_csv(tmp_path / name, index=False)
    c = load_config(overrides={"strategy": {"fundamental": {
        "piotroski": {"min_f_score": 0, "pb_quantile": 1.0}, "n_candidates": 5}}})
    out = fs.run_fundamental_screens(c, str(tmp_path), frame["ticker"].tolist())
    assert all(v["status"] == "ran" for v in out["per_screen"].values()), out["per_screen"]
    assert len(out["per_screen"]) == 20
    assert out["candidates"][0] == "VALU"       # the engineered best name


def test_family_consensus_rules(cfg):
    def res(score_map, higher=True):
        df = pd.DataFrame({"ticker": list(score_map), "s": list(score_map.values())})
        return {"full": df, "score": "s", "higher": higher, "skip": None}
    results = {"composite_value": res({"A": 3, "B": 2, "C": 1}),
               "fcf_yield": res({"A": 1, "B": 2}),
               "buffett_quality": res({"A": 2, "B": 1, "C": 3}),
               "piotroski": res({"A": 3, "B": 5, "C": 1}),
               "garp_peg": res({"A": 1, "C": 2}, higher=False),
               "altman_z": {"full": None, "score": "s", "higher": True, "skip": "x"}}
    cons, info = fs.family_consensus(cfg, results)
    # 4 families ran (value, quality, earnings_quality, growth) -> need all 4
    assert info["safety"]["ran"] == []
    got = cons.set_index("ticker")
    assert "B" not in got.index           # B has no growth_investment coverage
    assert set(got.index) == {"A", "C"}
    # value family for A: mean(pct composite 100, pct fcf 50) = 75
    assert got.at["A", "fam_value"] == pytest.approx(75.0)
    assert list(cons["fund_rank"]) == [1, 2]


def _R(S=3000, N=12, seed=0):
    r = np.random.default_rng(seed)
    mu = r.normal(0.01, 0.01, N)
    L = r.normal(0, 0.03, (N, N)) / np.sqrt(N) + np.eye(N) * 0.05
    return mu + r.standard_normal((S, N)) @ L.T


def test_lp_objective_equals_ru_cvar(cfg):
    R = _R()
    names = [f"N{i}" for i in range(R.shape[1])]
    cons = Constraints(alpha=0.95, w_max=0.3)
    m, _ = build_model(R, names, cons, "min_cvar")
    sol = solve_highs(m)
    assert sol.status == "optimal"
    w = sol.x[m.idx["w"]]
    rk = portfolio_risk(R, w, 0.95)
    assert sol.objective == pytest.approx(rk["cvar"], rel=1e-6, abs=1e-7)
    assert rk["component_cvar"].sum() == pytest.approx(rk["cvar"], rel=1e-9)
    assert w.sum() == pytest.approx(1.0) and w.max() <= 0.3 + 1e-9


def test_milp_respects_constraints_and_fingerprint_is_stable(cfg):
    R = _R(N=16)
    names = [f"N{i:02d}" for i in range(16)]
    sectors = {t: ("A" if i < 8 else "B") for i, t in enumerate(names)}
    cons = Constraints(alpha=0.95, w_max=0.2, w_min=0.05, cardinality=(6, 8),
                       sector_of=sectors, sector_cap=0.55, cluster_of={}, cluster_cap=None)
    out = optimize_portfolio(cfg, R, names, cons, mip=True, policy=SolverPolicy(cfg))
    w = out["weights"]
    assert 6 <= len(w) <= 8
    assert all(0.05 - 1e-6 <= x <= 0.2 + 1e-6 for x in w.values())
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-5)
    assert sum(x for t, x in w.items() if sectors[t] == "A") <= 0.55 + 1e-6
    m1, _ = build_model(R, names, cons, "min_cvar", mip=True)
    m2, _ = build_model(R.copy(), list(names), cons, "min_cvar", mip=True)
    assert fingerprint(m1) == fingerprint(m2)
    R2 = R.copy()
    R2[0, 0] += 1e-9
    m3, _ = build_model(R2, names, cons, "min_cvar", mip=True)
    assert fingerprint(m3) != fingerprint(m1)


def test_turnover_constraint(cfg):
    R = _R(N=10)
    names = [f"N{i}" for i in range(10)]
    prev = {"N0": 0.5, "N1": 0.5}
    cons = Constraints(alpha=0.95, w_max=0.5, prev_weights=prev, turnover_cap=0.4)
    m, _ = build_model(R, names, cons, "min_cvar")
    sol = solve_highs(m)
    w = sol.x[m.idx["w"]]
    turn = sum(abs(w[i] - prev.get(t, 0)) for i, t in enumerate(names))
    assert turn <= 0.4 + 1e-6


def test_resolve_relaxes_caps_minimally_and_flags(cfg):
    R = _R(N=10)
    names = [f"N{i}" for i in range(10)]
    book = {t: 0.1 for t in names}
    sectors = {t: "Same" for t in names}
    base = Constraints(alpha=0.95, w_max=0.15, w_min=0.02, cardinality=(10, 20),
                       sector_of=sectors, sector_cap=0.30, cluster_of={}, cluster_cap=None)
    res = resolve_final(cfg, R, names, book, ["N1", "N4", "N7"], base, 0.5)
    assert set(res["final_entries"]) == {"N1", "N4", "N7"}
    assert sum(res["final_entries"].values()) == pytest.approx(0.5, abs=1e-5)  # gate applied
    assert any("position cap" in r for r in res["relaxations"])
    assert any("sector cap" in r for r in res["relaxations"])
    assert res["basket"]["cvar"] == pytest.approx(
        portfolio_risk(R[:, [1, 4, 7]], np.array([res["basket"]["weights"][t]
                                                  for t in ["N1", "N4", "N7"]]),
                       0.95)["cvar"], rel=1e-6)
    none = resolve_final(cfg, R, names, book, [], base, 1.0)
    assert none["status"] == "no_entries" and none["final_entries"] == {}


def test_scenario_allocation_and_determinism():
    c = allocate(100001, {"kde": 0.4, "block_bootstrap": 0.4, "historical": 0.1, "stress": 0.1})
    assert sum(c.values()) == 100001
    daily = np.random.default_rng(1).normal(0, 0.01, (500, 5))
    a = bootstrap_scenarios(daily, 21, 1000, 10, np.random.default_rng(7), np)
    b = bootstrap_scenarios(daily, 21, 1000, 10, np.random.default_rng(7), np)
    assert np.array_equal(a, b)


def test_jump_probabilities():
    e = pd.DataFrame({"ticker": ["A", "B", "C"], "date": ["2026-10-15", "2026-12-30", "2026-01-01"],
                      "eps_actual": [None, None, 1.0]})
    p = jump_probabilities(e, ["A", "B", "C"], "2026-10-08", 21, 0.33)
    assert list(p) == [1.0, 0.0, 0.33]


def test_backend_policy():
    assert bk.choose("cpu", 1e12, 1, True) == "cpu"
    assert bk.choose("auto", 10, 100, True) == "cpu"          # below crossover
    assert bk.choose("auto", 1000, 100, False) == "cpu"       # CUDA unhealthy
    assert bk.choose("auto", 1000, 100, True) == "gpu"
    with pytest.raises(RuntimeError):
        bk.choose("gpu", 1000, 100, False)


def test_assemble_history_total_return_factor():
    raw = pd.DataFrame({"date": ["2026-01-02", "2026-01-05", "2026-01-06"],
                        "open": [100, 50, 51], "high": [101, 51, 52], "low": [99, 49, 50],
                        "close": [100.0, 50.0, 51.0], "volume": [10, 20, 20], "vwap": [100, 50, 51]})
    adj = raw.assign(close=[50.0, 50.0, 51.0])            # 2:1 split on 2026-01-05
    divs = pd.DataFrame({"ex_dividend_date": ["2026-01-06"], "cash_amount": [0.51],
                         "historical_adjustment_factor": [0.99]})
    h = assemble_history(raw, adj, divs)
    assert list(h["split_close"]) == [50.0, 50.0, 51.0]
    assert list(h["adjclose"].round(6)) == [49.5, 49.5, 51.0]


def test_corwin_schultz_is_positive_and_small():
    r = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(r.normal(0, 0.01, 60)))
    bp = corwin_schultz_bp(pd.Series(c * 1.003), pd.Series(c * 0.997))
    assert 0 <= bp < 200


def test_api_key_never_logged(monkeypatch, caplog):
    monkeypatch.delenv("HERMES_TEST_KEY", raising=False)
    with pytest.raises(http.MissingKeyError) as e:
        http.require_key("HERMES_TEST_KEY")
    assert "HERMES_TEST_KEY" in str(e.value)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    c = http.JsonClient("https://127.0.0.1:9", "SECRET-123", retries=2, timeout=0.2)
    with caplog.at_level(logging.DEBUG):
        assert c.get("stable/quote?symbol=X") is None
    assert "SECRET-123" not in caplog.text
    assert c.failures == 1


def test_macro_stub_and_schema(tmp_path):
    assert load_macro(None)["macro_stale"] and load_macro(None)["exposure_gate"] == 1.0
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"exposure_gate": 0.7}))
    bad = load_macro(str(p), prev_gate=0.5)
    assert bad["macro_stale"] and bad["exposure_gate"] == 0.5     # carried forward
    p.write_text(json.dumps({"exposure_gate": 0.5, "event_risk": [{"ticker": "A", "score": 0.8}]}))
    ok = load_macro(str(p))
    assert not ok["macro_stale"] and ok["exposure_gate"] == 0.5 and ok["event_risk"][0]["ticker"] == "A"


def test_endpoint_family_strips_tickers_and_dates():
    assert http.endpoint_family("v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2026-01-01") == \
        "v2/aggs/ticker"
    assert http.endpoint_family("stocks/v1/dividends") == "stocks/v1/dividends"
    assert http.endpoint_family("https://x.com/stable/key-metrics?symbol=A") == "key-metrics"


def test_429_is_retried_with_retry_after(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    hits = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits["n"] += 1
            if hits["n"] <= 2:
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.JsonClient(f"http://127.0.0.1:{srv.server_port}", "K", retries=1,
                            max_rpm=6000)
        assert c.get("v2/aggs/ticker/AAPL") == {"ok": True}
        assert c.status_summary() == {"v2/aggs/ticker 200": 1, "v2/aggs/ticker 429": 2}
    finally:
        srv.shutdown()
