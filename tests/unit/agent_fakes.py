"""Fixtures for the agent tests: a tiny market, a fake graph and a scripted LLM backend."""

from __future__ import annotations

from services.app.audit import MemoryAudit
from services.app.cases import MemoryCases
from services.app.config import Settings
from services.app.data import DataStore
from services.app.deps import Deps
from services.app.models import Claim, Findings, PolicyRef
from services.app.policy import PolicyIndex
from services.app.ratelimit import ForceLimiter

MEMBERS = ["W001", "W002", "W003"]
OTHER = ["C001", "C002"]


def fixtures() -> dict[str, list[dict]]:
    def alert(aid, ring, status, hint, members, n_flagged):
        return {
            "alert_id": aid, "ring_id": ring, "status": status, "scenario_hint": hint,
            "member_accounts": members, "n_members": len(members), "score": 60.0,
            "top_features": "{}", "evidence_trade_ids": ["T1", "T2"],
            "evidence_edge_ids": [f"UsesDevice:{members[0]}>D1"], "threshold": 43.0,
            "run_id": "r", "created_ts": "2026-10-16 00:00:00", "n_flagged": n_flagged,
        }  # fmt: skip

    accounts = [
        {"account_id": a, "created_ts": "2026-09-01T00:00:00Z", "kyc_tier": 1, "country": "US",
         "device_id": "D1", "funding_source_id": "F1", "source": "synthetic"}
        for a in MEMBERS + OTHER
    ]  # fmt: skip
    scores = [
        {"account_id": a, "ring_id": "R_W001", "score": 55.0, "flagged": True, "threshold": 43.0,
         "features": "{}", "contributions": '{"link_size": 12.0}'}
        for a in MEMBERS + OTHER
    ]  # fmt: skip
    return {
        "alerts": [
            alert("AL_W", "R_W001", "flagged", "wash_trading", MEMBERS, 3),
            alert("AL_C", "R_C001", "needs_review", "coordinated_accumulation", OTHER, 0),
        ],
        "accounts": accounts,
        "scores": scores,
        "markets": [
            {"market_id": "M05", "title": "m", "resolve_ts": "2026-10-03T16:00:00Z"},
            {"market_id": "M06", "title": "m", "resolve_ts": "2026-10-03T18:00:00Z"},
        ],
        "traded_in": [
            {"account_id": a, "market_id": "M05", "n_trades": 4, "total_usd": 400.0}
            for a in MEMBERS
        ]
        + [{"account_id": a, "market_id": "M06", "n_trades": 2, "total_usd": 90.0} for a in OTHER],
        "uses_device": [{"account_id": a, "device_id": "D1"} for a in MEMBERS],
        "funded_by": [{"account_id": a, "funding_source_id": "F1"} for a in MEMBERS],
    }


class FakeGraph:
    def __init__(self):
        self.calls = 0

    def warm(self):
        pass

    def ring_subgraph(self, ring_id):
        self.calls += 1
        edges = [
            {"id": f"UsesDevice:{a}>D1", "type": "UsesDevice", "source": f"Account:{a}",
             "target": "Device:D1"}
            for a in MEMBERS
        ]  # fmt: skip
        nodes = [{"id": f"Account:{a}", "type": "Account", "key": a} for a in MEMBERS]
        return {"ring_id": ring_id, "members": MEMBERS, "nodes": nodes, "edges": edges,
                "truncated": False, "engine": "fallback", "cached": False,
                "fallback_reason": "test"}  # fmt: skip

    def neighbors(self, account_id, hops=1):
        return {**self.ring_subgraph("R_W001"), "account_id": account_id, "neighbors": []}

    def trades_for(self, account_ids, market_id):
        return {"market_id": market_id, "account_ids": account_ids,
                "edges": [{"id": f"TradedIn:{a}>{market_id}", "account_id": a, "n_trades": 4,
                           "total_usd": 400.0} for a in account_ids],
                "trades": [{"id": "T1", "ts": "2026-10-03 15:00:00", "account_id": account_ids[0],
                            "side": "BUY", "outcome": "YES", "price": 0.5, "size_usd": 100.0}],
                "truncated": False, "engine": "gql", "cached": True}  # fmt: skip


class FakeBackend:
    """Scripted agents. ``first`` / ``second`` are the Investigator's two possible answers."""

    models = {"investigator": "fake-fast", "policy": "fake-fast", "drafter": "fake-fast",
              "critic": "fake-strong"}  # fmt: skip

    def __init__(self, first: Findings, second: Findings | None = None, critic_rejects=()):
        self.first, self.second = first, second
        self.critic_rejects = set(critic_rejects)
        self.investigations = 0
        self.critic_calls: list[list[dict]] = []
        self.feedbacks: list[dict | None] = []

    def investigate(self, ctx, tools, brief, feedback):
        self.investigations += 1
        self.feedbacks.append(feedback)
        by_name = {t.__name__: t for t in tools}
        if feedback is None:
            by_name["get_alert_context"](brief["alert_id"])
            by_name["graph"]("ring_subgraph", brief["ring_id"])
            by_name["trades_for"](["W001", "W002"], "M05")
        return (self.second if feedback and self.second else self.first).model_copy(deep=True)

    def choose_policy(self, ctx, summary, candidates):
        return [PolicyRef(clause_id=c["clause_id"], title=c["title"]) for c in candidates[:2]]

    def critic_check(self, ctx, items):
        self.critic_calls.append(items)
        return [
            {"claim_id": i["claim_id"], "supported": i["claim_id"] not in self.critic_rejects,
             "reason": "fake"}
            for i in items
        ]  # fmt: skip

    def draft(self, ctx, material):
        return {"summary": "Summary [c1].", "pattern": "p", "hint_note": "agrees",
                "recommended_action": "analyst_review", "confidence": 0.9}  # fmt: skip


def good_findings(decision="escalate") -> Findings:
    return Findings(
        decision=decision, pattern="wash_trading", agrees_with_hint=True,
        escalate_accounts=MEMBERS if decision == "escalate" else [],
        cleared_accounts=[] if decision == "escalate" else MEMBERS,
        claims=[
            Claim(id="c1", text="W001-W003 share device D1",
                  evidence_ids=["UsesDevice:W001>D1", "UsesDevice:W002>D1"]),
            Claim(id="c2", text="W001 and W002 traded M05", evidence_ids=["TradedIn:W001>M05"]),
        ],
    )  # fmt: skip


def make_deps(backend, **kw) -> tuple[Deps, MemoryAudit]:
    audit = MemoryAudit()
    settings = kw.pop("settings", Settings(project="test-project-123"))
    deps = Deps(
        settings=settings,
        store=DataStore("test-project-123", fixtures=fixtures(), cache_dir=None),
        graph=FakeGraph(),
        policy=PolicyIndex(),
        backend=backend,
        cases=MemoryCases(),
        audit_factory=lambda: audit,
        limiter=ForceLimiter(2, 5),
    )
    return deps, audit
