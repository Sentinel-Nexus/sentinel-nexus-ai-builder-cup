"""The Critic: a deterministic ledger check first, then an LLM check that the evidence supports
the claim. A claim whose evidence ids were not returned by a tool in THIS run never reaches
the LLM."""

from __future__ import annotations

import re

from services.app.agents import Backend
from services.app.budget import CaseTimeout
from services.app.models import Claim, ClaimVerdict, Findings
from services.app.runctx import RunContext


def _mentions(ctx: RunContext, ring_id: str, evidence_ids: list[str]) -> bool:
    """True if some evidence id (or the ledger row behind it) concerns an account of the ring."""
    members = ctx.ring_members.get(ring_id, [])
    for eid in evidence_ids:
        found, _ = ctx.ledger.resolve([eid])
        text = f"{eid} {found.get(eid, {}).get('row', '')}"
        if any(
            re.search(rf"(?<![A-Za-z0-9_]){re.escape(m)}(?![A-Za-z0-9_])", text) for m in members
        ):
            return True
    return False


_EDGE = re.compile(r"^(UsesDevice|FundedBy|TradedWith|TradedIn):[^>]+>[^>]+$")
_NUM = re.compile(r"(?<![\w.:>-])(\d+\.\d+|\d{3,})(?![\w:>-])")
_ANY_NUM = re.compile(r"\d+(?:\.\d+)?")


def numbers_in_claim(text: str) -> list[float]:
    """Decimals and numbers of 3+ digits in the claim (small counts are left to the LLM check)."""
    return [float(n) for n in _NUM.findall(text.replace(",", ""))]


def number_check(claim: Claim, rows: dict[str, dict]) -> str | None:
    """Every such number in the claim must appear in the rows it cites."""
    wanted = numbers_in_claim(claim.text)
    if not wanted:
        return None
    seen = {
        round(float(n), 6)
        for eid, r in rows.items()
        for n in _ANY_NUM.findall(f"{eid} {r.get('row', '')}")
    }
    missing = [n for n in wanted if round(n, 6) not in seen]
    return f"numbers not in the cited rows: {missing[:4]}" if missing else None


def ledger_check(ctx: RunContext, claim: Claim, findings: Findings) -> ClaimVerdict | None:
    """Return a rejection, or None when the claim passes the deterministic stage."""
    if not claim.evidence_ids:
        return ClaimVerdict(
            claim_id=claim.id, accepted=False, stage="ledger", reason="claim has no evidence ids"
        )
    _, missing = ctx.ledger.resolve(claim.evidence_ids)
    if missing:
        return ClaimVerdict(
            claim_id=claim.id,
            accepted=False,
            stage="ledger",
            reason=f"evidence ids not in this run's tool-call ledger: {missing[:6]}",
        )
    found, _ = ctx.ledger.resolve(claim.evidence_ids)
    bad_number = number_check(claim, found)
    if bad_number:
        return ClaimVerdict(claim_id=claim.id, accepted=False, stage="ledger", reason=bad_number)
    if claim.kind == "merge_proposal":
        own = ctx.alert["ring_id"]
        others = [r for r in findings.merge_with if r != own]
        if not others:
            return ClaimVerdict(
                claim_id=claim.id,
                accepted=False,
                stage="ledger",
                reason="merge proposal names no other ring",
            )
        for ring in others:
            if ring not in ctx.ring_members:
                return ClaimVerdict(
                    claim_id=claim.id,
                    accepted=False,
                    stage="ledger",
                    reason=f"ring {ring} was never returned by related_rings",
                )
        for ring in [own, *others]:
            if not _mentions(ctx, ring, claim.evidence_ids):
                return ClaimVerdict(
                    claim_id=claim.id,
                    accepted=False,
                    stage="ledger",
                    reason=f"merge evidence has nothing from ring {ring}",
                )
    return None


def review(ctx: RunContext, backend: Backend, findings: Findings) -> list[ClaimVerdict]:
    verdicts: dict[str, ClaimVerdict] = {}
    survivors: list[Claim] = []
    for claim in findings.claims:
        rejection = ledger_check(ctx, claim, findings)
        if rejection:
            verdicts[claim.id] = rejection
        else:
            survivors.append(claim)
    if survivors:
        items = []
        for c in survivors:
            rows, _ = ctx.ledger.resolve(c.evidence_ids)
            # A graph edge id already states the fact (type:source>target), so every edge id is
            # passed as a bare id (the Critic can count them); other rows are passed with content.
            items.append(
                {
                    "claim_id": c.id,
                    "claim": c.text,
                    "edge_ids": [i for i in rows if _EDGE.match(i)],
                    "evidence": [
                        {"id": i, "row": r["row"][:220]}
                        for i, r in rows.items()
                        if not _EDGE.match(i)
                    ][:40],
                }
            )
        try:
            results = {r.get("claim_id"): r for r in backend.critic_check(ctx, items)}
        except CaseTimeout:
            raise
        except Exception as e:  # noqa: BLE001  an unverifiable claim is not accepted
            results = {}
            ctx.emit("critic", "error", f"critic LLM unavailable: {type(e).__name__}")
        for c in survivors:
            r = results.get(c.id)
            if r is None:
                verdicts[c.id] = ClaimVerdict(
                    claim_id=c.id,
                    accepted=False,
                    stage="llm",
                    reason="no verdict from the Critic LLM",
                )
            elif r.get("supported") is True:
                verdicts[c.id] = ClaimVerdict(
                    claim_id=c.id,
                    accepted=True,
                    stage="both_ok",
                    reason=str(r.get("reason", ""))[:300],
                )
            else:
                verdicts[c.id] = ClaimVerdict(
                    claim_id=c.id,
                    accepted=False,
                    stage="llm",
                    reason=str(r.get("reason", "not supported"))[:300],
                )
    ordered = [verdicts[c.id] for c in findings.claims]
    for v in ordered:
        ctx.emit(
            "critic",
            "verdict",
            f"{v.claim_id}: {'accepted' if v.accepted else 'rejected'} ({v.stage}) {v.reason}",
            claim_id=v.claim_id,
            accepted=v.accepted,
            stage=v.stage,
        )
    return ordered
