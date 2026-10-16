"""The investigation pipeline: prefetch -> Investigator -> Critic (-> one revision) -> Drafter.

Synchronous on purpose: it runs in a worker thread, streams its steps through
``RunContext.listeners`` and writes each one to the audit sink. Nothing here acts on an
account; the only outbound path is the mocked webhook in ``webhook.py``, after approval.

Policy: keyword retrieval runs as its own visible step, and the Drafter picks and explains the
clauses (no separate LLM call). A run that passes the case deadline ends as ``timed_out``; a run
that fails ends as ``error``. Neither is cached, so a repeat request tries again.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from services.app.agents import ACTIONS
from services.app.budget import BudgetExceeded, CaseTimeout
from services.app.critic import review
from services.app.deps import Deps
from services.app.models import CaseReport, Claim, ClaimVerdict, Findings, PolicyRef
from services.app.policy import RETRIEVAL
from services.app.runctx import RunContext, now_iso
from services.app.tools import build_tools

MIN_ACCEPTED_FOR_ESCALATION = 2  # SP-4.1: at least two independent, supported signals
MAX_PREFETCH_ACCOUNTS = 200


class UnknownAlert(LookupError):
    pass


def _renumber(findings: Findings) -> Findings:
    """Claim ids must be unique so verdicts map one to one."""
    for i, c in enumerate(findings.claims, 1):
        c.id = f"c{i}"
    return findings


def _brief(alert: dict, prefetched: dict) -> dict:
    return {
        "alert_id": alert["alert_id"],
        "ring_id": alert["ring_id"],
        "status": alert["status"],
        "scenario_hint": alert["scenario_hint"],
        "n_members": alert["n_members"],
        "members": alert["member_accounts"],
        "prefetched": prefetched,
    }


def _prefetch(deps: Deps, ctx: RunContext, tools: list, alert: dict) -> dict:
    """The three tool calls every investigation needs, run in parallel through the same audited,
    capped tool wrappers the Investigator uses."""
    by = {t.__name__: t for t in tools}
    members = alert["member_accounts"][:MAX_PREFETCH_ACCOUNTS]
    market = deps.store.dominant_market(alert["member_accounts"])
    jobs = {
        "get_alert_context": lambda: by["get_alert_context"](alert["alert_id"]),
        "graph": lambda: by["graph"]("ring_subgraph", alert["ring_id"]),
    }
    if market:
        jobs["trades_for"] = lambda: by["trades_for"](members, market)
    t0 = time.monotonic()
    with ThreadPoolExecutor(len(jobs)) as ex:
        futures = {name: ex.submit(fn) for name, fn in jobs.items()}
        out = {name: f.result() for name, f in futures.items()}
    ctx.emit(
        "investigator",
        "step",
        f"prefetched {len(out)} tools in parallel",
        latency_ms=int((time.monotonic() - t0) * 1000),
        tools=sorted(out),
    )
    return out


def _fallback_text(alert: dict, decision: str, accepted: list[Claim]) -> dict:
    cites = " ".join(f"[{c.id}]" for c in accepted) or "(no supported claims)"
    return {
        "summary": f"Automatic draft: decision {decision} for alert {alert['alert_id']}. "
        f"The detector hint {alert['scenario_hint']} is a hypothesis. Supported claims: {cites}.",
        "pattern": "see claims",
        "hint_note": f"Detector hint: {alert['scenario_hint']} (hypothesis).",
        "recommended_action": "analyst_review" if decision != "clear" else "no_action",
        "confidence": 0.3,
    }


def run_investigation(deps: Deps, alert: dict, ctx: RunContext) -> CaseReport:
    backend = deps.backend
    ctx.emit(
        "orchestrator",
        "step",
        f"investigating {alert['alert_id']} ({alert['status']})",
        status=alert["status"],
        scenario_hint=alert["scenario_hint"],
    )
    tools = build_tools(ctx, deps.store, deps.graph, deps.policy)

    # 1. Investigator (prefetched evidence, then follow-up tools if it needs them)
    ctx.emit("investigator", "step", "start")
    prefetched = _prefetch(deps, ctx, tools, alert)
    brief = _brief(alert, prefetched)
    try:
        findings = _renumber(backend.investigate(ctx, tools, brief, None))
    except BudgetExceeded as e:
        findings = Findings(decision="uncertain", notes=f"budget exhausted: {e.what}")
    ctx.emit(
        "investigator",
        "step",
        f"decision {findings.decision}, {len(findings.claims)} claims",
        decision=findings.decision,
        claims=[c.model_dump() for c in findings.claims],
    )

    # 2. Critic, with one send-back to the Investigator
    verdicts = _review(ctx, backend, findings)
    first_pass = {"claims": len(findings.claims), "accepted": sum(v.accepted for v in verdicts)}
    reviewed, rejected_n = len(verdicts), sum(not v.accepted for v in verdicts)
    revised = False
    rejected = [v for v in verdicts if not v.accepted]
    if rejected and not ctx.budget.exhausted:
        revised = True
        ctx.emit(
            "orchestrator",
            "step",
            f"{len(rejected)} claim(s) rejected; sent back once",
            rejected=[v.model_dump() for v in rejected],
        )
        feedback = {
            "previous": findings.model_dump(),
            "rejections": [v.model_dump() for v in rejected],
        }
        try:
            findings = _renumber(backend.investigate(ctx, tools, brief, feedback))
            verdicts = _review(ctx, backend, findings)
            reviewed += len(verdicts)
            rejected_n += sum(not v.accepted for v in verdicts)
        except BudgetExceeded as e:
            ctx.emit("orchestrator", "limit", f"revision stopped: {e.what}", limit=e.what)
    by_id = {v.claim_id: v for v in verdicts}
    accepted = [
        c
        for c in findings.claims
        if by_id.get(c.id, ClaimVerdict(claim_id=c.id, accepted=False, stage="llm")).accepted
    ]
    unsupported = [c for c in findings.claims if c not in accepted]

    # Deterministic guards on the decision (SP-4.1)
    members = set(alert["member_accounts"])
    escalate = sorted(set(findings.escalate_accounts) & members)
    decision = findings.decision
    # a claim that only cites policy clauses is a reading of the policy, not a signal about the case
    signals = [c for c in accepted if any(not i.startswith("SP-") for i in c.evidence_ids)]
    if decision == "escalate" and (len(signals) < MIN_ACCEPTED_FOR_ESCALATION or not escalate):
        ctx.emit(
            "orchestrator",
            "step",
            "escalation downgraded to uncertain: needs "
            f"{MIN_ACCEPTED_FOR_ESCALATION} supported signals and named accounts",
            rule="SP-4.1",
        )
        decision = "uncertain"
    if decision != "escalate":
        escalate = []
    cleared = sorted(members - set(escalate)) if decision in ("escalate", "clear") else []
    merge_ok = any(c.kind == "merge_proposal" for c in accepted)
    merge_with = findings.merge_with if merge_ok else []

    # 3. Policy: keyword retrieval as its own visible step; the Drafter picks the clauses
    query = f"{findings.pattern} {decision} {alert['scenario_hint']} evidence standard approval"
    hits = deps.policy.search(query, k=6)
    ctx.ledger.record(0, "policy_search", {"clauses": hits})
    ctx.emit(
        "policy",
        "step",
        f"retrieved {len(hits)} clauses ({RETRIEVAL}); the Drafter selects",
        evidence_ids=[h["clause_id"] for h in hits],
        engine=RETRIEVAL,
        cached=False,
    )

    # 4. Drafter (the evidence table is built from accepted claims, never by the model)
    material = {
        "alert": {k: alert[k] for k in ("alert_id", "ring_id", "status", "scenario_hint", "score")},
        "decision": decision,
        "pattern": findings.pattern,
        "agrees_with_hint": findings.agrees_with_hint,
        "accepted_claims": [c.model_dump() for c in accepted],
        "n_rejected": len(unsupported),
        "policy_candidates": [{k: h[k] for k in ("clause_id", "title", "text")} for h in hits],
        "merge_with": merge_with,
        "budget_status": ctx.budget.status,
    }
    try:
        text = backend.draft(ctx, material)
    except CaseTimeout:
        raise
    except Exception as e:  # noqa: BLE001  never lose the case because the Drafter failed
        ctx.emit("drafter", "error", f"drafter unavailable: {type(e).__name__}: {str(e)[:120]}")
        text = _fallback_text(alert, decision, accepted)
    refs = _policy_refs(deps, text.get("policy_refs"), hits)
    action = text.get("recommended_action")
    if action not in ACTIONS:
        action = "no_action" if decision == "clear" else "analyst_review"
    if decision == "clear":
        action = "no_action"
    elif action == "no_action":
        action = "analyst_review"
    try:
        conf = max(0.0, min(1.0, float(text.get("confidence", 0.3))))
    except (TypeError, ValueError):
        conf = 0.3
    support = len(accepted) / max(len(findings.claims), 1)
    conf = round(min(conf, 0.35 + 0.65 * support, 0.5 if ctx.budget.exhausted else 1.0), 2)

    report = CaseReport(
        alert_id=alert["alert_id"],
        ring_id=alert["ring_id"],
        alert_status=alert["status"],
        scenario_hint=alert["scenario_hint"],
        decision=decision,
        summary=str(text.get("summary", ""))[:2000],
        pattern=str(text.get("pattern", findings.pattern))[:500],
        hint_note=str(text.get("hint_note", ""))[:500],
        evidence_table=[
            {"claim_id": c.id, "kind": c.kind, "claim": c.text, "evidence_ids": c.evidence_ids}
            for c in accepted
        ],
        policy_references=refs,
        recommended_action=action,
        confidence=conf,
        escalate_accounts=escalate,
        cleared_accounts=cleared,
        unsupported_claims=[
            f"{c.id}: {c.text} ({by_id[c.id].reason})" if c.id in by_id else c.text
            for c in unsupported
        ],
        merge_proposals=[{"rings": [alert["ring_id"], *merge_with]}] if merge_with else [],
    )
    report_meta = {
        "first_pass_claims": first_pass["claims"],
        "first_pass_accepted": first_pass["accepted"],
        "critic_reviewed": reviewed,
        "critic_rejected": rejected_n,
        "revised": revised,
        "claims_total": len(findings.claims),
        "claims_accepted": len(accepted),
        "pattern_agent": findings.pattern,
        "agrees_with_hint": findings.agrees_with_hint,
    }
    ctx.emit(
        "orchestrator",
        "step",
        f"report ready: {decision}",
        decision=decision,
        confidence=conf,
        **report_meta,
    )
    ctx.meta = report_meta
    return report


def _policy_refs(deps: Deps, chosen, hits: list[dict]) -> list[PolicyRef]:
    refs = []
    for r in chosen or []:
        clause = deps.policy.get(str(r.get("clause_id"))) if isinstance(r, dict) else None
        if clause:
            refs.append(
                PolicyRef(clause_id=clause.clause_id, title=clause.title, why=r.get("why", ""))
            )
    return refs[:4] or [PolicyRef(clause_id=h["clause_id"], title=h["title"]) for h in hits[:3]]


def _review(ctx: RunContext, backend, findings: Findings) -> list[ClaimVerdict]:
    ctx.emit("critic", "step", f"reviewing {len(findings.claims)} claims")
    try:
        return review(ctx, backend, findings)
    except BudgetExceeded as e:
        ctx.emit("critic", "limit", f"critic stopped: {e.what}", limit=e.what)
        return [
            ClaimVerdict(claim_id=c.id, accepted=False, stage="llm", reason=f"budget: {e.what}")
            for c in findings.claims
        ]


def case_row(report: CaseReport, ctx: RunContext, deps: Deps, *, forced: bool, source: str) -> dict:
    models = deps.backend.models
    return {
        "case_id": f"CASE_{report.alert_id}_{ctx.run_id[4:]}",
        "alert_id": report.alert_id,
        "ring_id": report.ring_id,
        "alert_status": report.alert_status,
        "run_id": ctx.run_id,
        "created_ts": now_iso(),
        "decision": report.decision,
        "confidence": report.confidence,
        "report": json.dumps({**report.model_dump(), "meta": ctx.meta}),
        "ledger_ids": ctx.ledger.ids()[:2000],
        "models": json.dumps(models),
        "retrieval": RETRIEVAL,
        "graph_engines": ",".join(sorted(ctx.engines)),
        "budget_status": ctx.budget.status,
        "tool_calls": ctx.budget.tool_calls,
        "tokens_total": ctx.budget.total_tokens,
        "latency_ms": ctx.elapsed_ms(),
        "forced": forced,
        "source": source,
    }


def _failed(ctx: RunContext, status: str, message: str) -> dict:
    return {
        "status": status,
        "error": message,
        "cached": False,
        "run_id": ctx.run_id,
        "tokens": ctx.budget.total_tokens,
        "tool_calls": ctx.budget.tool_calls,
        "latency_ms": ctx.elapsed_ms(),
        "engines": ",".join(sorted(ctx.engines)),
        "budget_status": ctx.budget.status,
        "report": None,
    }


def investigate(deps: Deps, alert_id: str, *, force=False, source="live", listeners=()) -> dict:
    """Cached case, or a fresh run. Returns a dict with ``status`` ok | timed_out | error."""
    alert = deps.store.get_alert(alert_id)
    if alert is None:
        raise UnknownAlert(alert_id)
    audit = deps.audit_factory()
    ctx = RunContext(alert=alert, settings=deps.settings, audit=audit, listeners=list(listeners))
    if not force:
        hit = deps.cases.get(alert_id)
        if hit:
            ctx.emit(
                "orchestrator",
                "cache",
                f"served cached case from {hit['created_ts']}",
                cached=True,
                case_created_ts=hit["created_ts"],
                source=hit.get("source"),
            )
            audit.flush()  # asynchronous: the response does not wait for the audit insert
            return {
                "status": "ok",
                "cached": True,
                "run_id": hit["run_id"],
                "case_created_ts": hit["created_ts"],
                "report": json.loads(hit["report"]),
                "tokens": 0,
                "latency_ms": ctx.elapsed_ms(),
                "tool_calls": 0,
                "engines": hit["graph_engines"],
                "budget_status": hit["budget_status"],
            }
    try:
        report = run_investigation(deps, alert, ctx)
    except CaseTimeout as e:
        limit = deps.settings.limits.wall_clock_s
        ctx.emit("orchestrator", "limit", f"timed out after {limit:.0f}s: {e}", limit="timeout")
        audit.flush()
        return _failed(ctx, "timed_out", f"case exceeded {limit:.0f}s")
    except Exception as e:  # noqa: BLE001  record the failure and return a clean status
        ctx.emit("orchestrator", "error", f"{type(e).__name__}: {str(e)[:300]}")
        audit.flush()
        return _failed(ctx, "error", f"{type(e).__name__}: {str(e)[:200]}")
    row = case_row(report, ctx, deps, forced=force, source=source)
    deps.cases.put(row)
    ctx.emit(
        "orchestrator", "step", "case cached in risk_curated.case_reports", case_id=row["case_id"]
    )
    audit.flush()
    return {
        "status": "ok",
        "cached": False,
        "run_id": ctx.run_id,
        "report": json.loads(row["report"]),
        "tokens": ctx.budget.total_tokens,
        "latency_ms": ctx.elapsed_ms(),
        "tool_calls": ctx.budget.tool_calls,
        "engines": row["graph_engines"],
        "cached_graph_calls": ctx.cached_graph_calls,
        "budget_status": row["budget_status"],
    }
