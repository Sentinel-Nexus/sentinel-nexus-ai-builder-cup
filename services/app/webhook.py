"""Analyst approval and the mocked outbound webhook.

Agents never act on accounts. A recommended action only becomes an outbound call after an
analyst approves it, and in this build that call is a mock: the payload is audited and nothing
leaves the process.
"""

from __future__ import annotations

import json
import uuid

from services.app.audit import AuditSink
from services.app.runctx import now_iso

ALLOWED_DECISIONS = ("approve", "reject")


def record_decision(
    audit: AuditSink, case: dict, analyst: str, decision: str, note: str = ""
) -> dict:
    """Audit the analyst's decision; on approval of an escalation, audit the mocked webhook."""
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"decision must be one of {ALLOWED_DECISIONS}")
    if not analyst.strip():
        raise ValueError("analyst is required")
    report = case["report"]
    run_id = f"APR_{uuid.uuid4().hex[:10]}"
    base = {
        "run_id": run_id,
        "alert_id": report["alert_id"],
        "agent": "analyst",
        "tool": None,
        "args": None,
        "evidence_ids": [],
        "engine": None,
        "cached": None,
        "latency_ms": None,
        "tokens": None,
    }
    audit.write(
        {
            **base,
            "event_id": f"{run_id}:1",
            "seq": 1,
            "ts": now_iso(),
            "event_type": "approval",
            "summary": f"{decision} by {analyst}",
            "detail": json.dumps(
                {"analyst": analyst, "note": note[:500], "case_run": case["run_id"]}
            ),
        }
    )
    fired = decision == "approve" and report["decision"] == "escalate"
    if fired:
        payload = {
            "alert_id": report["alert_id"],
            "action": report["recommended_action"],
            "accounts": report["escalate_accounts"],
            "approved_by": analyst,
            "mock": True,
        }
        audit.write(
            {
                **base,
                "event_id": f"{run_id}:2",
                "seq": 2,
                "ts": now_iso(),
                "event_type": "webhook_mock",
                "summary": "mock webhook fired after analyst approval (no network call)",
                "detail": json.dumps(payload),
            }
        )
    audit.flush()
    return {"recorded": True, "decision": decision, "webhook_fired": fired, "mock": True}
