"""Eval scoring and cost estimate, on made-up run results (no cloud, no Gemini)."""

from __future__ import annotations

from evals.agents import metrics
from services.app.config import Settings
from services.app.cost import estimate


def run(decision, escalate, cleared=(), **meta):
    report = {
        "decision": decision,
        "escalate_accounts": list(escalate),
        "cleared_accounts": list(cleared),
        "merge_proposals": [],
        "meta": {"claims_total": 4, "claims_accepted": 3, "first_pass_claims": 4,
                 "first_pass_accepted": 2, "critic_reviewed": 8, "critic_rejected": 3,
                 "agrees_with_hint": True, **meta},
    }  # fmt: skip
    return {"report": report, "tokens": 30000, "tool_calls": 4, "budget_status": "ok",
            "latency_ms": 90000, "engines": "gql", "cached_graph_calls": 0}  # fmt: skip


LABELS = {
    "attackers": {"W1": "wash", "W2": "wash", "C1": "coord"},
    "evaded": {"W2"},
    "decoy_groups": {"DG1": ["H1", "H2"], "DG2": ["E1"]},
}


def results():
    return [
        {"kind": "real", "alert_id": "A1", "run": run("escalate", ["W1"], ["W2"])},
        {"kind": "real", "alert_id": "A2", "run": run("escalate", ["C1"])},
        {"kind": "decoy", "alert_id": "EV_DG1", "run": run("clear", [], ["H1", "H2"])},
        {
            "kind": "decoy",
            "alert_id": "EV_DG2",
            "run": run("escalate", ["E1"], agrees_with_hint=False),
        },
        {
            "kind": "borderline_attacker",
            "alert_id": "EV_C1",
            "run": run("escalate", ["C1"]),
            "repeat_ms": 40,
        },  # fmt: skip
    ]


def test_recall_is_per_attacker_account_and_split_by_scenario():
    m = metrics.compute(results(), LABELS)
    assert m["attacker_recall"]["caught"] == 2 and m["attacker_recall"]["of"] == 3
    assert m["attacker_recall"]["missed"] == ["W2"]
    assert m["attacker_recall"]["by_scenario"]["wash"]["rate"] == 0.5
    assert m["evasion_caught"] == {"caught": 0, "of": 1, "rate": 0.0}


def test_decoys_borderline_support_and_critic_rates():
    m = metrics.compute(results(), LABELS)
    assert m["decoys"]["alerts_escalated"] == 1 and m["decoys"]["accounts_escalated"] == 1
    assert m["decoys"]["accounts"] == 3
    assert m["borderline"]["attackers_escalated"] == 1
    assert m["borderline"]["decoy_groups_cleared"] == 1
    assert m["claim_support_rate"]["final"] == 0.75 and m["claim_support_rate"]["first_pass"] == 0.5
    assert m["critic_rejection_rate"]["rate"] == 0.375
    assert m["hint"]["disagreeing_alerts"] == ["EV_DG2"]


def test_latency_is_split_by_engine_and_cache_state():
    cached = {"kind": "real", "alert_id": "A9", "run": {**run("clear", []), "cached": True,
                                                         "latency_ms": 5}}  # fmt: skip
    m = metrics.compute([*results(), cached], LABELS)
    groups = m["latency_ms"]
    assert "engine=gql, graph_live" in groups and "case_cache_hit" in groups
    assert groups["case_cache_hit"]["n"] == 2  # one cached run and one measured repeat


def test_estimate_scales_with_runs_and_stays_under_the_hard_cap():
    one, ten = estimate(1, Settings()), estimate(10, Settings())
    assert ten["tokens_cap"] == 10 * one["tokens_cap"]
    assert one["tokens_per_run_expected"] <= one["tokens_per_run_cap"]
    assert 0 < one["usd_expected"] <= one["usd_worst_case"]
