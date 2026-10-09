"""End-to-end nightly + premarket passes on the offline fake market (no network, no keys)."""
import json
import os

import duckdb
import pytest

from fakes import FakeFMP, FakeMarket, FakeMassive
from hermes.config import load_config
from hermes.data.snapshot import SnapshotExistsError, build_snapshot, verify_manifest
from hermes.orchestrator import Orchestrator

SESSION = "2026-10-08"
FAST = {"strategy": {"scenarios": {"n": 4000, "n_cpu": 4000},
                     "optimizer": {"frontier_points": 5, "cvar_cap": 0.25}},
        "infra": {"validation": {"min_fundamental_rows": 50}}}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("hermes"))
    cfg = load_config(overrides=FAST, root=root)
    m = FakeMarket(n=90, session=SESSION)
    orch = Orchestrator(cfg, FakeFMP(m), FakeMassive(m))
    out = orch.run_nightly(session=SESSION, limit=90)
    return cfg, orch, out, m


def test_nightly_produces_top3(run):
    cfg, _, out, _ = run
    t3 = out["top3"]
    assert out["status"] in ("ok", "fewer_than_3"), t3.get("status_reason")
    fc = t3["funnel_counts"]
    assert fc["universe"] == 90 and fc["fund_candidates"] == 75 and fc["risk_carried"] == 40
    assert 10 <= fc["book"] <= 20                           # cardinality
    assert len(t3["top3"]) <= 3
    w = [e["final_weight"] for e in t3["top3"]]
    assert w == sorted(w, reverse=True)
    assert sum(t3["all_entries"].values()) == pytest.approx(t3["exposure_gate"], abs=1e-4)
    for e in t3["top3"]:
        assert e["ticker"] not in {n["ticker"] for n in t3["not_triggered"]}
    md = open(out["paths"]["markdown"]).read()
    assert "not investment advice" in md and "Blend screen" in md


def test_snapshot_is_immutable_and_checksummed(run):
    cfg, _, _, m = run
    snap = os.path.join(cfg.path("snapshots"), SESSION)
    assert verify_manifest(snap) == []
    with pytest.raises(SnapshotExistsError):
        build_snapshot(cfg, FakeFMP(m), FakeMassive(m), SESSION, limit=90)


def test_rerun_is_deterministic(run):
    cfg, orch, out, _ = run
    again = Orchestrator(cfg).run_nightly(session=SESSION, build=False)
    a, b = out["top3"], again["top3"]
    assert again["top3"]["run_id"].endswith("-002")
    assert a["all_entries"] == b["all_entries"]
    assert a["basket_cvar95_21d"] == b["basket_cvar95_21d"]


def test_learning_db_logs_whole_funnel(run):
    cfg, *_ = run
    con = duckdb.connect(cfg.path("db"), read_only=True)
    n = con.execute("SELECT count(*) FROM candidates WHERE run_id LIKE '%-001'").fetchone()[0]
    assert n == 75                                          # every stage-3 name, picked or not
    assert con.execute("SELECT count(*) FROM runs").fetchone()[0] >= 1
    con.close()


def test_audit_log_is_hash_chained(run):
    cfg, *_ = run
    recs = [json.loads(x) for x in open(os.path.join(cfg.path("audit"), "tool_calls.jsonl"))]
    assert recs[0]["prev"] == "0" * 64
    for a, b in zip(recs, recs[1:]):
        assert b["prev"] == a["hash"]
    assert [r["tool"] for r in recs[:3]] == ["build_snapshot", "screen_universe",
                                             "run_fundamental_screens"]


def test_premarket_macro_delay(run, tmp_path):
    cfg, orch, out, _ = run
    entries = list(out["top3"]["all_entries"])
    if not entries:
        pytest.skip("no entries to delay")
    p = tmp_path / "macro.json"
    p.write_text(json.dumps({"exposure_gate": 0.5,
                             "event_risk": [{"ticker": entries[0], "score": 0.9}]}))
    pm = orch.run_premarket(SESSION, str(p))
    t3 = pm["top3"]
    assert entries[0] not in t3["all_entries"]
    assert t3["exposure_gate"] == 0.5
    if t3["all_entries"]:
        assert sum(t3["all_entries"].values()) == pytest.approx(0.5, abs=1e-4)
