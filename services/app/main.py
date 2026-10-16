"""FastAPI service: alerts, cached cases, streamed investigations (SSE) and analyst approval.

    uvicorn services.app.main:app --port 8080

The graph service and the table caches warm up at start-up. A repeat request returns the cached
case unless force=true, which is rate limited.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.app.audit import BigQueryAudit, drain_all
from services.app.audit_read import BigQueryAuditReader
from services.app.cases import BigQueryCases
from services.app.config import Settings
from services.app.decisions import (
    BigQueryDecisions,
    decision_row,
    effective_lane,
    is_test_analyst,
    latest_visible,
)
from services.app.deps import Deps
from services.app.models import LANES
from services.app.pipeline import investigate
from services.app.policy import PolicyIndex
from services.app.ratelimit import MESSAGES, ForceLimiter
from services.app.runctx import now_iso
from services.app.webhook import record_decision
from services.app.workbench import client_id, register


def default_deps(settings: Settings | None = None) -> Deps:
    from services.app.agents import AdkBackend
    from services.app.data import DataStore
    from services.graph.service import GraphService

    s = settings or Settings()
    return Deps(
        settings=s,
        store=DataStore(s.project),
        graph=GraphService(s.project),
        policy=PolicyIndex(),
        backend=AdkBackend(s),
        cases=BigQueryCases(s.project),
        audit_factory=lambda: BigQueryAudit(s.project),
        limiter=ForceLimiter(s.force_per_hour, s.force_per_day, per_client_hour=s.client_per_hour),
        decisions=BigQueryDecisions(s.project),
        audit_reader=BigQueryAuditReader(s.project),
    )


class Decision(BaseModel):
    analyst: str
    decision: str  # approve | reject
    note: str = ""


class Override(BaseModel):
    analyst: str
    decision: str  # override_to_escalate | confirm_clear
    note: str = ""


def need_reason(note: str) -> str:
    """Every analyst action carries a written reason (it goes to the audit trail)."""
    note = note.strip()
    if len(note) < 3:
        raise HTTPException(422, "a reason is required (at least 3 characters)")
    return note


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def create_app(deps: Deps | None = None) -> FastAPI:
    state: dict = {"deps": deps, "ready": False, "warm_error": None}
    slots = threading.BoundedSemaphore(2)  # investigations running at once; the rest wait

    def warm() -> None:
        t0 = time.monotonic()
        try:
            d = state["deps"]
            d.graph.warm()  # BigQuery client up before the first GQL call
            d.store.warm()
            d.cases.latest()
            d.decisions.latest()
            if hasattr(d.backend, "warm"):
                d.backend.warm()  # model clients up before the first investigation
            state["ready"] = True
            print(f"warm: ready after {time.monotonic() - t0:.1f}s", flush=True)
            state["prefetch"]()  # graph, trades and timelines of the cached cases
            print(f"warm: views prefetched after {time.monotonic() - t0:.1f}s", flush=True)
        except Exception as e:  # noqa: BLE001  reported on /health, requests still work
            state["warm_error"] = f"{type(e).__name__}: {str(e)[:200]}"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if state["deps"] is None:
            state["deps"] = default_deps()
        slots_n = state["deps"].settings.run_concurrency
        state["slots"] = threading.BoundedSemaphore(slots_n)
        threading.Thread(target=warm, daemon=True).start()
        yield
        drain_all()  # queued audit inserts

    app = FastAPI(title="MarketSentinel agents", lifespan=lifespan)

    def d() -> Deps:
        return state["deps"]

    def _report(row: dict) -> dict:
        """The stored report; the lane is derived from the decision for rows written before it."""
        report = json.loads(row["report"])
        report.setdefault("lane", LANES.get(report["decision"], "needs_analyst"))
        return report

    register(app, d, state)

    def _invalidate() -> None:
        if d().audit_reader is not None:
            d().audit_reader.invalidate()

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "service": "marketsentinel-agents",
            "ready": state["ready"],
            "warm_error": state["warm_error"],
            "models": d().backend.models,
        }

    @app.get("/alerts")
    def alerts():
        have = d().cases.latest()
        return [
            {
                k: a[k]
                for k in ("alert_id", "ring_id", "status", "scenario_hint", "score", "n_members")
            }
            | {"has_case": a["alert_id"] in have}
            for a in d().store.alerts()
        ]

    @app.get("/cases/{alert_id}")
    def get_case(alert_id: str):
        row = d().cases.get(alert_id)
        if row is None:
            raise HTTPException(404, "no cached case; POST /cases/{alert_id}/investigate")
        return {
            "cached": True,
            "case_created_ts": row["created_ts"],
            "run_id": row["run_id"],
            "report": json.loads(row["report"]),
        }

    @app.post("/cases/{alert_id}/investigate")
    async def run(alert_id: str, request: Request, force: bool = False):
        if d().store.get_alert(alert_id) is None:
            raise HTTPException(404, f"unknown alert {alert_id}")
        # a run that is not served from the cache spends Gemini tokens: it is limited
        live = force or d().cases.get(alert_id) is None
        if live:
            ok, retry, scope = d().limiter.check(client_id(request))
            if not ok:
                raise HTTPException(
                    429,
                    f"{MESSAGES.get(scope, 'Rate limited.')} The cached cases are real recorded "
                    "agent runs and are still available to browse. "
                    f"Try again in about {max(1, retry // 60)} min.",
                    headers={"Retry-After": str(retry)},
                )
        q: queue.Queue = queue.Queue()

        def work() -> None:
            # a cache hit answers at once; a real run takes one of the few investigation slots
            fresh = force or d().cases.get(alert_id) is None
            sl = state.get("slots", slots)
            try:
                if fresh and not sl.acquire(blocking=False):
                    q.put(
                        {
                            "event_type": "queued",
                            "agent": "orchestrator",
                            "alert_id": alert_id,
                            "summary": "waiting for a free investigation slot",
                        }
                    )
                    sl.acquire()
                try:
                    # a forced (live) result answers this caller; it does not replace the cache
                    keep = not force or d().settings.persist_forced
                    res = investigate(d(), alert_id, force=force, listeners=[q.put], persist=keep)
                    q.put(("result", res))
                finally:
                    if fresh:
                        sl.release()
            except Exception as e:  # noqa: BLE001
                q.put(("error", {"error": type(e).__name__, "message": str(e)[:300]}))
            finally:
                q.put(None)

        threading.Thread(target=work, daemon=True).start()

        async def stream():
            loop = asyncio.get_running_loop()
            while (item := await loop.run_in_executor(None, q.get)) is not None:
                if isinstance(item, tuple):
                    yield sse(*item)
                else:
                    yield sse("step", item)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/cases/{alert_id}/approve")
    def approve(alert_id: str, body: Decision, request: Request):
        row = d().cases.get(alert_id)
        if row is None:
            raise HTTPException(404, "no case to approve; investigate first")
        case = {"run_id": row["run_id"], "report": json.loads(row["report"])}
        note = need_reason(body.note)
        try:
            out = record_decision(
                d().audit_factory(),
                case,
                body.analyst,
                body.decision,
                note,
                override=latest_visible(d().decisions, is_test_analyst(body.analyst)).get(alert_id),
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        state["decision_log"][out["decision_id"]] = {
            "kind": "approval",
            "alert_id": alert_id,
            "decision": body.decision,
            "analyst": body.analyst,
            "ts": now_iso(),
        }
        _invalidate()
        return out

    @app.post("/cases/{alert_id}/override")
    def override(alert_id: str, body: Override):
        """The analyst disagrees with an agent clear (or confirms it). Audited and stored; an
        override moves the case to the escalated lane but still needs approval to act."""
        row = d().cases.get(alert_id)
        if row is None:
            raise HTTPException(404, "no case; investigate first")
        case = {"run_id": row["run_id"], "report": _report(row)}
        note = need_reason(body.note)
        try:
            rec = decision_row(case, body.analyst, body.decision, note)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        d().decisions.put(rec)
        audit = d().audit_factory()
        audit.write(
            {
                "event_id": f"{rec['decision_id']}:1",
                "run_id": rec["decision_id"],
                "seq": 1,
                "ts": rec["ts"],
                "alert_id": alert_id,
                "agent": "analyst",
                "event_type": "override",
                "tool": None,
                "args": None,
                "summary": f"{body.decision} by {rec['analyst']} (was {rec['previous_lane']})",
                "evidence_ids": [],
                "engine": None,
                "cached": None,
                "latency_ms": None,
                "tokens": None,
                "detail": json.dumps({"note": rec["note"], "case_run": case["run_id"]}),
            }
        )
        audit.flush()
        if body.decision == "confirm_clear":  # the analyst has dealt with it
            state["decision_log"][rec["decision_id"]] = {
                "kind": "approval",
                "alert_id": alert_id,
                "decision": body.decision,
                "analyst": body.analyst,
                "ts": rec["ts"],
            }
        _invalidate()
        lane, _ = effective_lane(case["report"], rec)
        return {"recorded": True, "decision": rec["decision"], "lane": lane}

    @app.get("/lanes")
    def lanes(include_test: bool = False):
        """Cached cases grouped by lane. Agent-cleared cases keep their own lane with the clear
        reason and evidence, and an analyst override moves a case to `escalated`."""
        out: dict[str, list] = {"escalated": [], "agent_cleared": [], "needs_analyst": []}
        decisions = latest_visible(d().decisions, include_test)
        for alert_id, row in sorted(d().cases.latest().items()):
            report = _report(row)
            dec = decisions.get(alert_id)
            lane, changed = effective_lane(report, dec)
            out[lane].append(
                {
                    "alert_id": alert_id,
                    "injected": bool((d().store.get_alert(alert_id) or {}).get("injected")),
                    "ring_id": report["ring_id"],
                    "alert_status": report["alert_status"],
                    "agent_decision": report["decision"],
                    "agent_lane": report.get("lane"),
                    "confidence": report["confidence"],
                    "summary": report["summary"],
                    "clear_reason": report.get("clear_reason", ""),
                    "clear_evidence_ids": report.get("clear_evidence_ids", []),
                    "analyst_decision": dec["decision"] if dec else None,
                    "analyst": dec["analyst"] if dec else None,
                    "analyst_changed": changed,
                }
            )
        return out

    return app


app = create_app()
