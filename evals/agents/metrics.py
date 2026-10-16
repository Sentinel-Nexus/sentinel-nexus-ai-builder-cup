"""Scoring of agent runs against labels. Pure functions (no cloud calls), unit tested."""

from __future__ import annotations

from collections import defaultdict


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def compute(results: list[dict], labels: dict) -> dict:
    """``results`` hold: kind (real | borderline_attacker | decoy), alert_id, run (the dict
    returned by ``investigate``) and optionally ``repeat_ms`` (latency of a cached repeat)."""
    real = [r for r in results if r["kind"] == "real"]
    attackers: dict[str, str] = labels["attackers"]
    escalated = {a for r in real for a in r["run"]["report"]["escalate_accounts"]}
    by_scenario: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for a, scenario in attackers.items():
        by_scenario[scenario][1] += 1
        by_scenario[scenario][0] += a in escalated
    evaded = labels["evaded"]

    decoys = [r for r in results if r["kind"] == "decoy"]
    decoy_accounts = {a for ms in labels["decoy_groups"].values() for a in ms}
    decoy_escalated = {a for r in decoys for a in r["run"]["report"]["escalate_accounts"]}
    border = [r for r in results if r["kind"] == "borderline_attacker"]
    border_attackers = {a for r in border for a in r["run"]["report"]["escalate_accounts"]}
    border_members = {a for r in border for a in _members(r)}

    runs = [r["run"] for r in results if not r["run"].get("cached")]
    meta = [r["run"]["report"].get("meta", {}) for r in results]
    final_c, final_ok = (
        sum(m.get("claims_total", 0) for m in meta),
        sum(m.get("claims_accepted", 0) for m in meta),
    )
    first_c, first_ok = (
        sum(m.get("first_pass_claims", 0) for m in meta),
        sum(m.get("first_pass_accepted", 0) for m in meta),
    )
    reviewed, rejected = (
        sum(m.get("critic_reviewed", 0) for m in meta),
        sum(m.get("critic_rejected", 0) for m in meta),
    )

    agree = [r["run"]["report"]["meta"].get("agrees_with_hint") for r in results]
    return {
        "alerts_run": len(results),
        "attacker_recall": {
            "caught": sum(a in escalated for a in attackers),
            "of": len(attackers),
            "rate": _rate(sum(a in escalated for a in attackers), len(attackers)),
            "by_scenario": {
                s: {"caught": c, "of": n, "rate": _rate(c, n)} for s, (c, n) in by_scenario.items()
            },
            "missed": sorted(a for a in attackers if a not in escalated),
        },
        "evasion_caught": {
            "caught": sum(a in escalated for a in evaded),
            "of": len(evaded),
            "rate": _rate(sum(a in escalated for a in evaded), len(evaded)),
        },
        "claim_support_rate": {
            "final": _rate(final_ok, final_c),
            "first_pass": _rate(first_ok, first_c),
            "claims": final_c,
        },
        "critic_rejection_rate": {
            "rejected": rejected,
            "reviewed": reviewed,
            "rate": _rate(rejected, reviewed),
        },  # fmt: skip
        "decoys": {
            "groups": len(decoys),
            "alerts_escalated": sum(r["run"]["report"]["decision"] == "escalate" for r in decoys),
            "alerts_cleared": sum(r["run"]["report"]["decision"] == "clear" for r in decoys),
            "alerts_uncertain": sum(r["run"]["report"]["decision"] == "uncertain" for r in decoys),
            "accounts_escalated": len(decoy_escalated & decoy_accounts),
            "accounts": len(decoy_accounts),
            "false_escalation_rate": _rate(
                len(decoy_escalated & decoy_accounts), len(decoy_accounts)
            ),
        },
        "borderline": {
            "attackers_in_band": len(border_members),
            "attackers_escalated": len(border_attackers & border_members),
            "decoy_groups_cleared": sum(r["run"]["report"]["decision"] == "clear" for r in decoys),
            "decoy_groups": len(decoys),
        },
        "merge": {
            "alerts_with_accepted_merge": sum(
                bool(r["run"]["report"]["merge_proposals"]) for r in real
            ),
        },
        "hint": {
            "agrees": sum(a is True for a in agree),
            "disagrees": sum(a is False for a in agree),
            "disagreeing_alerts": [
                r["alert_id"]
                for r in results
                if r["run"]["report"]["meta"].get("agrees_with_hint") is False
            ],
        },
        "cost": {
            "avg_tokens": _avg([r["tokens"] for r in runs]),
            "avg_tool_calls": _avg([r["tool_calls"] for r in runs]),
            "budget_exhausted": sum(r["budget_status"] != "ok" for r in runs),
        },
        "latency_ms": _latency(results),
    }


def _members(r: dict) -> list[str]:
    rep = r["run"]["report"]
    return rep["escalate_accounts"] + rep["cleared_accounts"]


def _latency(results: list[dict]) -> dict:
    """Average latency split by graph engine and by cache state."""
    groups: dict[str, list[float]] = defaultdict(list)
    for r in results:
        run = r["run"]
        if run.get("cached"):
            groups["case_cache_hit"].append(run["latency_ms"])
            continue
        engines = run.get("engines") or "none"
        live = "graph_cached" if run.get("cached_graph_calls") else "graph_live"
        groups[f"engine={engines}, {live}"].append(run["latency_ms"])
        if "repeat_ms" in r:
            groups["case_cache_hit"].append(r["repeat_ms"])
    return {k: {"n": len(v), "avg": _avg(v), "max": max(v)} for k, v in sorted(groups.items())}
