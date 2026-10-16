"""Workbench endpoints with fakes: queue, detail, graph, trades, timeline, export, limits."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from scripts import build_ui_data
from services.app.main import create_app
from services.app.ratelimit import ForceLimiter
from tests.unit.agent_fakes import FakeBackend, FakeGraph, good_findings, make_deps


def client_with_case(**kw):
    deps, audit = make_deps(FakeBackend(good_findings(), second=good_findings()), **kw)
    return deps, audit, TestClient(create_app(deps))


def test_index_and_static_are_served():
    _, _, c = client_with_case()
    with c:
        r = c.get("/")
        assert r.status_code == 200 and "MarketSentinel" in r.text
        assert "integrity=" in r.text  # pinned CDN scripts carry SRI hashes
        assert c.get("/static/css/app.css").status_code == 200
        assert c.get("/static/js/app.js").status_code == 200


def test_queue_summary_and_detail_before_and_after_a_case():
    _, _, c = client_with_case()
    with c:
        q = c.get("/queue").json()
        assert {a["alert_id"] for a in q} == {"AL_W", "AL_C"}
        assert next(a for a in q if a["alert_id"] == "AL_C")["severity"] == "review"
        assert c.get("/summary").json()["no_case"] == 2
        assert c.get("/cases/AL_W/detail").json()["cached"] is False
        c.post("/cases/AL_W/investigate")
        d = c.get("/cases/AL_W/detail").json()
        assert d["cached"] is True and d["effective_lane"] == "escalated"
        s = c.get("/summary").json()
        assert s["lanes"]["escalated"] == 1 and s["open"] == 2 and s["detector"]["precision"]
        assert c.get("/cases/NOPE/detail").status_code == 404


def test_graph_trades_timeline_and_export():
    _, _, c = client_with_case()
    with c:
        c.post("/cases/AL_W/investigate")
        g = c.get("/cases/AL_W/graph").json()
        assert g["engine"] == "fallback" and g["evidence_edge_ids"] and g["nodes"]
        assert g["trade_accounts"] == {"T1": "W001"}
        t = c.get("/cases/AL_W/trades", params={"account": "W001"}).json()
        assert t["market_id"] == "M05" and [x["id"] for x in t["trades"]] == ["T1"]
        assert c.get("/cases/AL_W/trades", params={"account": "ZZZ"}).status_code == 404
        tl = c.get("/cases/AL_W/timeline").json()
        assert tl["summary"]["tool_calls"] == 3 and tl["events"][0]["seq"] == 1
        ex = c.get("/cases/AL_W/export")
        assert "attachment" in ex.headers["content-disposition"]
        body = json.loads(ex.text)
        assert body["synthetic_data"] and body["case"]["report"] and body["timeline"]


def test_audit_governance_and_benchmark():
    _, _, c = client_with_case()
    with c:
        c.post("/cases/AL_W/investigate")
        assert c.get("/audit", params={"limit": 5}).json()["n"] == 5
        assert c.get("/audit", params={"event_type": "tool_call"}).json()["n"] == 3
        assert c.get("/audit", params={"limit": 9999}).status_code == 422
        g = c.get("/governance").json()
        assert g["limits"]["max_tool_calls"] == 8 and g["limits"]["wall_clock_s"] == 120
        assert {x["name"] for x in g["guards"]} >= {"Evidence ledger", "Model Armor prompt guard"}
        assert g["live"]["client_per_hour"] == 0 or g["live"]["client_per_hour"] >= 0
        b = c.get("/benchmark").json()
        assert b["kpi"]["detector"]["recall"] == 97.0 and len(b["limits"]) >= 5


def test_a_reason_is_required_and_decisions_reach_the_audit_and_the_queue():
    _, audit, c = client_with_case()
    with c:
        c.post("/cases/AL_W/investigate")
        no_reason = c.post("/cases/AL_W/approve", json={"analyst": "a1", "decision": "approve"})
        assert no_reason.status_code == 422 and "reason" in no_reason.json()["detail"]
        body = {"analyst": "a1", "decision": "reject", "note": "not convinced"}
        assert c.post("/cases/AL_W/approve", json=body).json()["recorded"]
        events = [e for e in audit.events if e["event_type"] == "approval"]
        assert json.loads(events[-1]["detail"])["note"] == "not convinced"
        assert c.get("/summary").json()["open"] == 1
        row = next(a for a in c.get("/queue").json() if a["alert_id"] == "AL_W")
        assert row["analyst_decision"] == "reject"


def test_live_runs_are_limited_per_client_and_per_service_with_a_clear_message():
    deps, _, c = client_with_case()
    with c:
        c.post("/cases/AL_W/investigate")  # fills the cache
        deps.limiter = ForceLimiter(5, 10, per_client_hour=1)
        h1, h2 = {"x-forwarded-for": "1.1.1.1"}, {"x-forwarded-for": "2.2.2.2"}
        assert c.post("/cases/AL_W/investigate?force=true", headers=h1).status_code == 200
        r = c.post("/cases/AL_W/investigate?force=true", headers=h1)
        assert r.status_code == 429 and "this hour" in r.json()["detail"]
        assert int(r.headers["retry-after"]) > 0
        assert "Cached cases are still available" in r.json()["detail"]
        assert c.post("/cases/AL_W/investigate?force=true", headers=h2).status_code == 200
        assert c.post("/cases/AL_W/investigate", headers=h1).status_code == 200  # cached: free
        deps.limiter = ForceLimiter(1, 10, per_client_hour=5)
        c.post("/cases/AL_W/investigate?force=true")
        r = c.post("/cases/AL_W/investigate?force=true", headers=h2)
        assert r.status_code == 429 and "service-wide" in r.json()["detail"]


def test_a_live_result_does_not_replace_the_cached_case():
    deps, _, c = client_with_case()
    with c:
        c.post("/cases/AL_W/investigate")
        first = deps.cases.get("AL_W")["run_id"]
        r = c.post("/cases/AL_W/investigate?force=true")
        assert '"cached": false' in r.text.split("event: result")[1]
        assert deps.cases.get("AL_W")["run_id"] == first


def test_a_rate_limited_model_gives_a_clean_error_result_not_a_crash():
    class Busy(FakeBackend):
        def investigate(self, ctx, tools, brief, feedback):
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota")

    deps, _ = make_deps(Busy(good_findings()))
    with TestClient(create_app(deps)) as c:
        r = c.post("/cases/AL_W/investigate")
        res = json.loads(r.text.split("event: result\ndata: ")[1].split("\n\n")[0])
        assert res["status"] == "error" and "RESOURCE_EXHAUSTED" in res["error"]
        assert c.get("/cases/AL_W").status_code == 404  # nothing half-stored


def test_graph_failure_is_a_502_with_a_message():
    class Down(FakeGraph):
        def ring_subgraph(self, ring_id):
            raise RuntimeError("boom")

    deps, _, c = client_with_case()
    deps.graph = Down()
    with c:
        r = c.get("/cases/AL_W/graph")
        assert r.status_code == 502 and "graph read failed" in r.json()["detail"]


def test_ui_data_file_matches_the_docs():
    assert build_ui_data.main(["--check"]) == 0
