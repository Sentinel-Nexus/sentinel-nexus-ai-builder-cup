"""Agent pipeline with a scripted backend: ledger, Critic, budget, cache, audit, approval."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from services.app.budget import RunBudget
from services.app.config import Limits, Settings
from services.app.ledger import harvest
from services.app.main import create_app
from services.app.models import Claim, Findings
from services.app.pipeline import UnknownAlert, investigate
from services.app.policy import PolicyIndex
from services.app.ratelimit import ForceLimiter
from services.app.runctx import RunContext
from services.app.tools import build_tools
from services.app.webhook import record_decision
from tests.unit.agent_fakes import FakeBackend, FakeGraph, good_findings, make_deps


def run(backend, alert_id="AL_W", **kw):
    deps, audit = make_deps(backend)
    return deps, audit, investigate(deps, alert_id, **kw)


# ---- ledger ----


def test_harvest_collects_p3_evidence_id_formats():
    ids = harvest("graph", FakeGraph().ring_subgraph("R_W001"))
    assert {"R_W001", "UsesDevice:W001>D1", "Account:W001", "Account:W002"} <= set(ids)


def test_tool_results_land_in_the_ledger_and_the_audit_with_engine_and_cached():
    backend = FakeBackend(good_findings())
    _, audit, res = run(backend)
    calls = [e for e in audit.events if e["event_type"] == "tool_call"]
    by_tool = {c["tool"]: c for c in calls}  # prefetched in parallel: order is not fixed
    assert sorted(by_tool) == ["get_alert_context", "graph", "trades_for"]
    assert (by_tool["graph"]["engine"], by_tool["graph"]["cached"]) == ("fallback", False)
    # a cached answer is labelled as cached, never presented as live
    assert (by_tool["trades_for"]["engine"], by_tool["trades_for"]["cached"]) == ("gql", True)
    assert res["report"]["decision"] == "escalate"


# ---- Critic ----


def test_critic_rejects_a_claim_whose_evidence_ids_are_not_in_the_ledger():
    """Adversarial: an injected claim with plausible but never-returned ids must be rejected."""
    bad = Claim(id="x", text="W001 funded W009 via a mixer", evidence_ids=["FundedBy:W001>FX9"])
    findings = good_findings()
    findings.claims.append(bad)
    backend = FakeBackend(findings, second=findings)
    _, audit, res = run(backend)
    report = res["report"]
    assert all("FundedBy:W001>FX9" not in c["evidence_ids"] for c in report["evidence_table"])
    assert any("mixer" in u for u in report["unsupported_claims"])
    rejections = [
        e for e in audit.events if e["event_type"] == "verdict" and "rejected" in e["summary"]
    ]
    assert rejections and "ledger" in rejections[0]["summary"]
    # the deterministic stage came first: the LLM Critic never saw the injected claim
    assert all(i["claim"] != bad.text for call in backend.critic_calls for i in call)


def test_claim_without_evidence_ids_is_rejected():
    findings = good_findings()
    findings.claims.append(Claim(id="n", text="trust me", evidence_ids=[]))
    _, _, res = run(FakeBackend(findings, second=findings))
    assert any("trust me" in u for u in res["report"]["unsupported_claims"])


def test_rejection_goes_back_to_the_investigator_exactly_once():
    first = good_findings()
    first.claims.append(Claim(id="x", text="bogus", evidence_ids=["Nope:1"]))
    backend = FakeBackend(first, second=good_findings())
    _, _, res = run(backend)
    assert backend.investigations == 2
    assert backend.feedbacks[1]["rejections"][0]["reason"].startswith("evidence ids not in")
    assert res["report"]["unsupported_claims"] == []
    assert res["report"]["meta"]["revised"] is True


def test_still_rejected_after_the_second_try_is_not_retried_again():
    findings = good_findings()
    findings.claims.append(Claim(id="x", text="bogus", evidence_ids=["Nope:1"]))
    backend = FakeBackend(findings, second=findings)
    run(backend)
    assert backend.investigations == 2


def test_llm_critic_can_reject_a_claim_with_valid_ids():
    backend = FakeBackend(good_findings(), second=good_findings(), critic_rejects={"c2"})
    _, _, res = run(backend)
    assert [r["claim_id"] for r in res["report"]["evidence_table"]] == ["c1"]
    assert "c2" in res["report"]["unsupported_claims"][0]


def test_escalation_needs_two_supported_claims():
    backend = FakeBackend(good_findings(), second=good_findings(), critic_rejects={"c1"})
    _, _, res = run(backend)
    assert res["report"]["decision"] == "uncertain"
    assert res["report"]["escalate_accounts"] == []


def test_merge_proposal_needs_evidence_from_both_rings_and_a_ring_the_tool_returned():
    merge = Claim(id="m", kind="merge_proposal", text="same market and window",
                  evidence_ids=["UsesDevice:W001>D1"])  # fmt: skip

    class B(FakeBackend):
        def investigate(self, ctx, tools, brief, feedback):
            f = super().investigate(ctx, tools, brief, feedback)
            {t.__name__: t for t in tools}["related_rings"](brief["ring_id"])
            return f

    findings = good_findings()
    findings.claims.append(merge)
    findings.merge_with = ["R_C001"]
    backend = B(findings, second=findings)
    _, _, res = run(backend)
    assert res["report"]["merge_proposals"] == []  # nothing from ring R_C001 in the evidence
    assert any("merge" in u or "ring R_C001" in u for u in res["report"]["unsupported_claims"])

    both = Claim(id="m", kind="merge_proposal", text="same market and window",
                 evidence_ids=["UsesDevice:W001>D1", "TradedIn:C001>M06"])  # fmt: skip
    findings2 = good_findings()
    findings2.claims.append(both)
    findings2.merge_with = ["R_C001"]
    _, _, res2 = run(B(findings2, second=findings2))
    assert res2["report"]["merge_proposals"] == [{"rings": ["R_W001", "R_C001"]}]


# ---- needs_review ----


def test_needs_review_alert_can_be_cleared_and_hint_is_stated_as_a_hypothesis():
    cleared = Findings(
        decision="clear", pattern="none", agrees_with_hint=False,
        cleared_accounts=["C001", "C002"],
        claims=[Claim(id="c1", text="aged accounts, independent trading",
                      evidence_ids=["Account:C001"])],
    )  # fmt: skip

    class B(FakeBackend):
        def investigate(self, ctx, tools, brief, feedback):
            {t.__name__: t for t in tools}["account_profile"]("C001")
            return super().investigate(ctx, tools, brief, feedback)

    deps, _ = make_deps(B(cleared, second=cleared))
    res = investigate(deps, "AL_C")
    report = res["report"]
    assert report["alert_status"] == "needs_review"
    assert report["decision"] == "clear" and report["recommended_action"] == "no_action"
    assert report["scenario_hint"] == "coordinated_accumulation"
    assert report["escalate_accounts"] == []


# ---- limits ----


def test_tool_calls_are_capped_at_eight_and_the_refusal_is_audited():
    deps, audit = make_deps(FakeBackend(good_findings()))
    ctx = RunContext(alert=deps.store.get_alert("AL_W"), settings=deps.settings, audit=audit)
    tools = {t.__name__: t for t in build_tools(ctx, deps.store, deps.graph, deps.policy)}
    results = [tools["check_policy"]("wash trading") for _ in range(10)]
    assert sum("error" not in r for r in results) == 8
    assert results[-1]["error"] == "tool_call_limit_reached"
    assert ctx.budget.tool_calls == 8 and ctx.budget.status == "budget_exhausted"
    assert [e["event_type"] for e in audit.events].count("limit") == 2


def test_token_budgets_total_and_per_agent():
    b = RunBudget(Limits(total_tokens=1000, agent_tokens={"critic": 300}))
    b.add_tokens("critic", 301)
    assert b.status == "budget_exhausted" and b.exhausted == "agent_tokens:critic"
    b2 = RunBudget(Limits(total_tokens=1000))
    b2.add_tokens("investigator", 1001)
    assert b2.exhausted == "total_tokens"


def test_only_the_alert_under_investigation_can_be_read():
    deps, audit = make_deps(FakeBackend(good_findings()))
    ctx = RunContext(alert=deps.store.get_alert("AL_W"), settings=deps.settings, audit=audit)
    tools = {t.__name__: t for t in build_tools(ctx, deps.store, deps.graph, deps.policy)}
    assert "error" in tools["get_alert_context"]("AL_C")


def test_account_profile_does_not_expose_raw_device_or_funding_ids():
    deps, audit = make_deps(FakeBackend(good_findings()))
    ctx = RunContext(alert=deps.store.get_alert("AL_W"), settings=deps.settings, audit=audit)
    tools = {t.__name__: t for t in build_tools(ctx, deps.store, deps.graph, deps.policy)}
    out = tools["account_profile"]("W001")
    assert out["device_shared_by"] == 3 and "D1" not in json.dumps(out)


# ---- cache, audit, rate limit ----


def test_repeat_request_returns_the_cached_case_without_calling_agents():
    backend = FakeBackend(good_findings())
    deps, audit = make_deps(backend)
    first = investigate(deps, "AL_W")
    n = backend.investigations
    again = investigate(deps, "AL_W")
    assert again["cached"] is True and backend.investigations == n
    assert again["report"]["decision"] == first["report"]["decision"]
    assert [e for e in audit.events if e["event_type"] == "cache"]
    forced = investigate(deps, "AL_W", force=True)
    assert forced["cached"] is False and backend.investigations > n


def test_every_event_has_the_audit_columns_and_the_run_id():
    _, audit, res = run(FakeBackend(good_findings()))
    cols = {
        "event_id",
        "run_id",
        "seq",
        "ts",
        "alert_id",
        "agent",
        "event_type",
        "tool",
        "args",
        "summary",
        "evidence_ids",
        "engine",
        "cached",
        "latency_ms",
        "tokens",
        "detail",
    }
    assert audit.events and all(set(e) == cols for e in audit.events)
    assert {e["run_id"] for e in audit.events} == {res["run_id"]}
    assert [e["seq"] for e in audit.events] == sorted(e["seq"] for e in audit.events)
    agents = {e["agent"] for e in audit.events}
    assert {"investigator", "critic", "policy", "orchestrator"} <= agents


def test_unknown_alert():
    deps, _ = make_deps(FakeBackend(good_findings()))
    with pytest.raises(UnknownAlert):
        investigate(deps, "AL_NOPE")


def test_force_limiter():
    t = [0.0]
    lim = ForceLimiter(2, 3, clock=lambda: t[0])
    assert lim.allow()[0] and lim.allow()[0]
    ok, retry = lim.allow()
    assert not ok and retry > 0
    t[0] = 3601
    assert lim.allow()[0]
    t[0] = 7300
    assert not lim.allow()[0]  # daily cap of 3 reached


# ---- policy and approval ----


def test_policy_retrieval_cites_clause_ids_and_records_the_mode():
    hits = PolicyIndex().search("reciprocal matched fills wash trading ring")
    assert hits[0]["clause_id"] == "SP-2.1" and hits[0]["retrieval"] == "keyword"


def test_webhook_fires_only_after_approval_of_an_escalation():
    deps, audit = make_deps(FakeBackend(good_findings()))
    res = investigate(deps, "AL_W")
    case = {"run_id": res["run_id"], "report": res["report"]}
    audit.events.clear()
    out = record_decision(audit, case, "analyst1", "reject")
    assert out["webhook_fired"] is False
    assert [e["event_type"] for e in audit.events] == ["approval"]
    out = record_decision(audit, case, "analyst1", "approve")
    assert out["webhook_fired"] is True and out["mock"] is True
    assert audit.events[-1]["event_type"] == "webhook_mock"
    with pytest.raises(ValueError):
        record_decision(audit, case, " ", "approve")


# ---- HTTP ----


def test_http_sse_cache_force_and_approve():
    backend = FakeBackend(good_findings())
    deps, audit = make_deps(backend)
    deps.settings = Settings(project="test-project-123", force_per_hour=1)
    with TestClient(create_app(deps)) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert {a["alert_id"] for a in client.get("/alerts").json()} == {"AL_W", "AL_C"}
        assert client.get("/cases/AL_W").status_code == 404
        r = client.post("/cases/AL_W/investigate")
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "event: step" in r.text and "event: result" in r.text
        result = json.loads(r.text.split("event: result\ndata: ")[1].split("\n\n")[0])
        assert result["cached"] is False
        assert client.get("/cases/AL_W").json()["report"]["decision"] == "escalate"
        again = client.post("/cases/AL_W/investigate").text
        assert '"cached": true' in again.split("event: result")[1]
        assert client.post("/cases/AL_NOPE/investigate").status_code == 404
        deps.limiter = ForceLimiter(0, 0)
        assert client.post("/cases/AL_W/investigate?force=true").status_code == 429
        ok = client.post("/cases/AL_W/approve", json={"analyst": "a1", "decision": "approve"})
        assert ok.json()["webhook_fired"] is True
        bad = client.post("/cases/AL_W/approve", json={"analyst": "a1", "decision": "maybe"})
        assert bad.status_code == 422


# ---- latency work: prefetch, deadline, status, numbers, ledger ----


def test_prefetch_runs_the_three_core_tools_before_the_investigator():
    backend = FakeBackend(good_findings())
    _, audit, _ = run(backend)
    assert set(backend.briefs[0]["prefetched"]) == {"get_alert_context", "graph", "trades_for"}
    kinds = [(e["event_type"], e["tool"]) for e in audit.events]
    assert kinds.index(("step", None)) < len(kinds)  # sanity
    first_call = next(i for i, e in enumerate(audit.events) if e["event_type"] == "tool_call")
    verdict = next(i for i, e in enumerate(audit.events) if e["event_type"] == "verdict")
    assert first_call < verdict
    assert any("prefetched 3 tools in parallel" in e["summary"] for e in audit.events)


def test_numbers_in_a_claim_must_appear_in_the_cited_rows():
    bad = Claim(id="x", text="W001 bought 9,999 USD of YES", evidence_ids=["TradedIn:W001>M05"])
    findings = good_findings()
    findings.claims.append(bad)
    _, _, res = run(FakeBackend(findings, second=findings))
    assert any("9,999" in u and "numbers not in the cited rows" in u
               for u in res["report"]["unsupported_claims"])  # fmt: skip


def test_ledger_trade_row_replaces_the_empty_listed_id():
    from services.app.ledger import Ledger

    ledger = Ledger()
    ledger.record(1, "get_alert_context", {"alert_id": "AL", "evidence_trade_ids": ["T1"]})
    ledger.record(2, "trades_for", {"trades": [{"id": "T1", "side": "BUY", "price": 0.5}]})
    assert "BUY" in ledger.resolve(["T1"])[0]["T1"]["row"]


def test_drafter_picks_policy_clauses_and_unknown_clause_ids_are_dropped():
    _, _, res = run(FakeBackend(good_findings()))
    refs = [r["clause_id"] for r in res["report"]["policy_references"]]
    assert refs == ["SP-2.1"]


def test_deadline_returns_timed_out_and_is_not_cached():
    from services.app.budget import CaseTimeout

    class Slow(FakeBackend):
        def investigate(self, ctx, tools, brief, feedback):
            raise CaseTimeout("too slow")

    deps, audit = make_deps(Slow(good_findings()))
    res = investigate(deps, "AL_W")
    assert res["status"] == "timed_out" and res["report"] is None
    assert deps.cases.get("AL_W") is None  # a repeat request tries again
    assert any(e["event_type"] == "limit" and "timed out" in e["summary"] for e in audit.events)


def test_a_failing_agent_gives_a_clean_error_status():
    class Broken(FakeBackend):
        def draft(self, ctx, material):
            raise RuntimeError("boom")

        def investigate(self, ctx, tools, brief, feedback):
            raise ValueError("bad output")

    deps, audit = make_deps(Broken(good_findings()))
    res = investigate(deps, "AL_W")
    assert res["status"] == "error" and "bad output" in res["error"]
    assert any(e["event_type"] == "error" for e in audit.events)


def test_cached_case_is_served_in_well_under_a_second():
    import time

    deps, _ = make_deps(FakeBackend(good_findings()))
    investigate(deps, "AL_W")
    t0 = time.monotonic()
    again = investigate(deps, "AL_W")
    assert again["cached"] is True and time.monotonic() - t0 < 1.0


def test_escalation_covers_every_alert_member_whatever_the_model_lists():
    """The model no longer names accounts: a model that only 'remembers' two of three members
    still escalates the whole ring, because membership comes from the alert and the graph."""
    # an old-style answer that still lists accounts: the extra field is ignored
    findings = Findings.model_validate(
        {**good_findings().model_dump(), "escalate_accounts": ["W001"]}
    )
    _, _, res = run(FakeBackend(findings, second=findings))
    report = res["report"]
    assert report["decision"] == "escalate" and report["lane"] == "escalated"
    assert report["escalate_accounts"] == ["W001", "W002", "W003"]
    assert report["members_alert"] == ["W001", "W002", "W003"] and report["members_linked"] == []


def test_accounts_linked_in_ring_subgraph_but_missing_from_the_alert_are_added():
    class Wider(FakeGraph):
        def ring_subgraph(self, ring_id):
            out = super().ring_subgraph(ring_id)
            out["members"] = [*out["members"], "W004"]
            return out

    deps, _ = make_deps(FakeBackend(good_findings(), second=good_findings()))
    deps.graph = Wider()
    report = investigate(deps, "AL_W")["report"]
    assert report["members_linked"] == ["W004"]
    assert report["escalate_accounts"] == ["W001", "W002", "W003", "W004"]


def test_exclusion_needs_a_supported_claim_naming_the_account():
    plain = good_findings()
    plain.exclusions = ["W003"]  # no exclusion claim: ignored
    _, _, res = run(FakeBackend(plain, second=plain))
    assert res["report"]["escalate_accounts"] == ["W001", "W002", "W003"]
    assert res["report"]["exclusions"][0]["accepted"] is False

    backed = good_findings()
    backed.exclusions = ["W003"]
    backed.claims.append(
        Claim(
            id="x",
            kind="exclusion",
            text="W003 trades independently",
            evidence_ids=["Account:W003"],
        )  # fmt: skip
    )
    _, _, res = run(FakeBackend(backed, second=backed))
    report = res["report"]
    assert report["escalate_accounts"] == ["W001", "W002"] and report["cleared_accounts"] == [
        "W003"
    ]
    assert report["exclusions"][0]["accepted"] is True


def test_clear_decision_is_stored_with_reason_evidence_and_its_own_lane():
    cleared = good_findings("clear")
    _, _, res = run(FakeBackend(cleared, second=cleared))
    report = res["report"]
    assert report["decision"] == "clear" and report["lane"] == "agent_cleared"
    assert (
        report["cleared_accounts"] == ["W001", "W002", "W003"] and report["escalate_accounts"] == []
    )
    assert report["clear_reason"] and report["clear_evidence_ids"]
    assert report["analyst_override_allowed"] and report["requires_analyst_approval"]
    assert res["lane"] == "agent_cleared" if "lane" in res else True


def test_removed_claims_are_visible_and_the_report_only_holds_resolvable_claims():
    findings = good_findings()
    findings.claims.append(Claim(id="x", text="bogus", evidence_ids=["Nope:1"]))
    _, _, res = run(FakeBackend(findings, second=findings))
    report = res["report"]
    assert [r["claim_id"] for r in report["removed_claims"]] == ["c3"]
    assert "removed" in report["unverified_note"]
    meta = report["meta"]
    assert (
        meta["report_claims"] == meta["report_claims_resolvable"] == len(report["evidence_table"])
    )
    assert all(row["evidence_ids"] for row in report["evidence_table"])


def test_summary_citation_of_a_removed_claim_is_stripped():
    class Citing(FakeBackend):
        def draft(self, ctx, material):
            out = super().draft(ctx, material)
            out["summary"] = "Shared device [c1]. Mixer funding [c3]."
            return out

    findings = good_findings()
    findings.claims.append(Claim(id="x", text="bogus", evidence_ids=["Nope:1"]))
    _, _, res = run(Citing(findings, second=findings))
    assert "[c3]" not in res["report"]["summary"] and "[c1]" in res["report"]["summary"]
    assert res["report"]["meta"]["citations_stripped"] == 1


def test_watchdog_ends_a_stuck_call_at_the_wall_clock_deadline():
    import asyncio
    import time

    from services.app.agents import _LoopThread
    from services.app.budget import CaseTimeout

    async def stuck():
        await asyncio.sleep(30)

    deadline = time.time() + 1.5
    t0 = time.time()
    try:
        _LoopThread.run(stuck(), lambda: deadline - time.time())
        raise AssertionError("expected CaseTimeout")
    except CaseTimeout:
        pass
    assert time.time() - t0 < 5


def test_agent_cleared_lane_and_analyst_override_over_http():
    cleared = good_findings("clear")
    deps, audit = make_deps(FakeBackend(cleared, second=cleared))
    with TestClient(create_app(deps)) as client:
        client.post("/cases/AL_W/investigate")
        lanes = client.get("/lanes").json()
        assert [c["alert_id"] for c in lanes["agent_cleared"]] == ["AL_W"]
        item = lanes["agent_cleared"][0]
        assert (
            item["clear_reason"] and item["clear_evidence_ids"] and item["analyst_decision"] is None
        )
        # approving a cleared case does not fire the webhook
        out = client.post(
            "/cases/AL_W/approve", json={"analyst": "a1", "decision": "approve"}
        ).json()
        assert out["webhook_fired"] is False
        # the analyst overrides: the case moves to the escalated lane, an audit event is written
        bad = client.post(
            "/cases/AL_W/override", json={"analyst": " ", "decision": "confirm_clear"}
        )
        assert bad.status_code == 422
        body = {"analyst": "a1", "decision": "override_to_escalate", "note": "household"}
        ok = client.post("/cases/AL_W/override", json=body)
        assert ok.json()["lane"] == "escalated"
        lanes = client.get("/lanes").json()
        assert [c["alert_id"] for c in lanes["escalated"]] == ["AL_W"] and not lanes[
            "agent_cleared"
        ]
        assert lanes["escalated"][0]["analyst_changed"] is True
        assert any(e["event_type"] == "override" for e in audit.events)
        # now approval fires the (mock) webhook for the whole ring
        out = client.post(
            "/cases/AL_W/approve", json={"analyst": "a1", "decision": "approve"}
        ).json()
        assert out["webhook_fired"] is True
        fired = [e for e in audit.events if e["event_type"] == "webhook_mock"][-1]
        assert json.loads(fired["detail"])["analyst_override"] is True
        assert json.loads(fired["detail"])["accounts"] == ["W001", "W002", "W003"]
        # an escalated case cannot be 'overridden'
        deps.cases.rows.clear()


def test_a_case_already_escalated_cannot_be_overridden():
    deps, _ = make_deps(FakeBackend(good_findings(), second=good_findings()))
    with TestClient(create_app(deps)) as client:
        client.post("/cases/AL_W/investigate")
        r = client.post("/cases/AL_W/override", json={"analyst": "a1", "decision": "confirm_clear"})
        assert r.status_code == 422
