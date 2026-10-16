"""The six Investigator tools. Each runs through ``RunContext.call_tool`` (cap, ledger, audit).

The model sees a compact view; the ledger records the full result, so any id the tool returned
can be cited. Table tools report ``engine: "table"`` (stored detector output, ``as_of`` its
creation time). Graph tools report the real engine (gql | fallback) and ``cached``.
"""

from __future__ import annotations

from collections import defaultdict

from services.app.data import DataStore
from services.app.policy import PolicyIndex
from services.app.runctx import RunContext

MAX_EDGE_IDS = 120
MAX_TRADE_ROWS = 30
GRAPH_KINDS = ("neighbors", "ring_subgraph")


def compact_graph(full: dict) -> dict:
    edges = full.get("edges", [])
    shared: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e["type"] in ("UsesDevice", "FundedBy"):
            shared[e["target"]].append(e["source"].split(":", 1)[1])
    out = {
        k: full[k]
        for k in (
            "ring_id",
            "account_id",
            "hops",
            "engine",
            "cached",
            "fallback_reason",
            "truncated",
        )
        if k in full
    }
    out["members"] = full.get("members") or [
        n["key"] for n in full["nodes"] if n["type"] == "Account"
    ]
    if "neighbors" in full:
        out["neighbors"] = full["neighbors"][:60]
    out["shared_nodes"] = [
        {"node": node, "accounts": sorted(accts)} for node, accts in sorted(shared.items())
    ]
    out["edge_ids"] = [e["id"] for e in edges[:MAX_EDGE_IDS]]
    out["edge_count"] = len(edges)
    out["note"] = "edge ids are UsesDevice/FundedBy/TradedWith ids; cite them verbatim"
    return out


def compact_trades(full: dict) -> dict:
    trades = full["trades"]
    sides: dict[str, int] = defaultdict(int)
    for t in trades:
        sides[t["side"]] += 1
    return {
        "market_id": full["market_id"],
        "engine": full["engine"],
        "cached": full.get("cached", False),
        "fallback_reason": full.get("fallback_reason"),
        "edges": full["edges"],
        "n_trades": len(trades),
        "truncated": full["truncated"],
        "sides": dict(sides),
        "first_ts": trades[0]["ts"] if trades else None,
        "last_ts": trades[-1]["ts"] if trades else None,
        "trades": trades[:MAX_TRADE_ROWS],
        "note": f"first {MAX_TRADE_ROWS} of {len(trades)} trades shown; all ids are citable",
    }


def build_tools(
    ctx: RunContext, store: DataStore, graph, policy: PolicyIndex, agent="investigator"
):
    """Return the tool callables for ADK (name, signature and docstring are the tool contract)."""
    alert = ctx.alert
    ctx.ring_members[alert["ring_id"]] = list(alert["member_accounts"])

    def run(name: str, args: dict, fn):
        return ctx.call_tool(agent, name, args, fn)

    def get_alert_context(alert_id: str) -> dict:
        """Get the detector alert under investigation: status, score, the detector's scenario hint
        (a hypothesis, not a fact), member accounts with their scores, main market and the
        detector's evidence ids. Only the current alert can be read."""

        def fn():
            if alert_id != alert["alert_id"]:
                raise ValueError("only the alert under investigation can be read")
            members = []
            for a in alert["member_accounts"]:
                s = store.score_of(a) or {}
                terms = sorted((s.get("contributions") or {}).items(), key=lambda kv: -kv[1])[:3]
                members.append(
                    {
                        "account_id": a,
                        "score": s.get("score"),
                        "flagged": s.get("flagged"),
                        "top_terms": {k: round(v, 1) for k, v in terms},
                    }
                )
            market = store.dominant_market(alert["member_accounts"])
            m = store.market(market) if market else None
            full = {
                "alert_id": alert["alert_id"],
                "ring_id": alert["ring_id"],
                "status": alert["status"],
                "scenario_hint": alert["scenario_hint"],
                "hint_is_hypothesis": True,
                "score": alert["score"],
                "threshold": alert["threshold"],
                "review_band": "needs_review = every member scored just below the threshold",
                "n_members": alert["n_members"],
                "n_flagged": alert["n_flagged"],
                "as_of": str(alert.get("created_ts")),
                "engine": "table",
                "cached": False,
                "members": members,
                "market_id": market,
                "market_resolve_ts": str(m["resolve_ts"]) if m else None,
                "evidence_trade_ids": alert["evidence_trade_ids"][:40],
                "evidence_edge_ids": alert["evidence_edge_ids"][:60],
                "top_features": alert["top_features"],
            }
            return full, full

        return run("get_alert_context", {"alert_id": alert_id}, fn)

    def graph_tool(kind: str, target: str, hops: int = 1) -> dict:
        """Query the account graph. kind='ring_subgraph' with target=<ring id like R_W001>
        returns the whole connected ring; kind='neighbors' with target=<account id> returns
        accounts within `hops` link hops (device, funding source, matched fills). The reply
        names the engine (gql or fallback) and whether it was cached."""

        def fn():
            if kind not in GRAPH_KINDS:
                raise ValueError(f"kind must be one of {GRAPH_KINDS}")
            full = (
                graph.ring_subgraph(target)
                if kind == "ring_subgraph"
                else graph.neighbors(target, hops)
            )
            return full, compact_graph(full)

        return run("graph", {"kind": kind, "target": target, "hops": hops}, fn)

    graph_tool.__name__ = "graph"

    def trades_for(account_ids: list[str], market_id: str) -> dict:
        """Get the trades of the given accounts in one market, with per-account TradedIn
        summaries (trade count, total USD). Use it to see timing, sides and sizes."""

        def fn():
            full = graph.trades_for(account_ids, market_id)
            return full, compact_trades(full)

        return run("trades_for", {"account_ids": account_ids, "market_id": market_id}, fn)

    def account_profile(account_id: str) -> dict:
        """Get one account: age, KYC tier, country, how many accounts share its device and its
        funding source, its detector score and feature contributions, and its main markets."""

        def fn():
            acc = store.account(account_id)
            if acc is None:
                raise ValueError(f"unknown account {account_id}")
            full = {
                "account_id": account_id,
                "score_id": f"Score:{account_id}",
                "created_ts": str(acc["created_ts"]),
                "age_days_at_data_end": store.account_age_days(account_id),
                "kyc_tier": acc["kyc_tier"],
                "country": acc["country"],
                **store.sharing(account_id),
                "detector": store.score_of(account_id),
                "markets": [
                    {
                        "market_id": r["market_id"],
                        "n_trades": int(r["n_trades"]),
                        "total_usd": round(float(r["total_usd"]), 2),
                    }
                    for r in store.markets_of(account_id)[:5]
                ],
                "engine": "table",
                "cached": False,
            }
            return full, full

        return run("account_profile", {"account_id": account_id}, fn)

    def check_policy(query: str) -> dict:
        """Search the surveillance policy. Returns the best clauses with clause ids (SP-x.y)
        and text. Cite the clause ids in policy references."""

        def fn():
            hits = policy.search(query, k=4)
            full = {"clauses": hits, "engine": "keyword", "cached": False, "retrieval": "keyword"}
            return full, full

        return run("check_policy", {"query": query}, fn)

    def related_rings(ring_id: str) -> dict:
        """Find other detector rings in the same market or in a market resolving in the same
        window. Use it when this ring looks like part of a larger scheme split by the detector.
        Merging needs evidence linking both rings."""

        def fn():
            if ring_id != alert["ring_id"]:
                raise ValueError("related_rings works on the ring under investigation")
            found = store.related_rings(alert)
            for r in found:
                ctx.ring_members[r["ring_id"]] = r["members"]
                r["market_id"] = r["dominant_market"]
            full = {
                "ring_id": ring_id,
                "related": found,
                "engine": "table",
                "cached": False,
                "note": "no related ring" if not found else "ids in edge_ids are citable",
            }
            return full, full

        return run("related_rings", {"ring_id": ring_id}, fn)

    return [get_alert_context, graph_tool, trades_for, account_profile, check_policy, related_rings]
