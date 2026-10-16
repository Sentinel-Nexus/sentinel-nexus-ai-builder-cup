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
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.app.audit import BigQueryAudit, drain_all
from services.app.cases import BigQueryCases
from services.app.config import Settings
from services.app.deps import Deps
from services.app.pipeline import investigate
from services.app.policy import PolicyIndex
from services.app.ratelimit import ForceLimiter
from services.app.webhook import record_decision


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
        limiter=ForceLimiter(s.force_per_hour, s.force_per_day),
    )


class Decision(BaseModel):
    analyst: str
    decision: str  # approve | reject
    note: str = ""


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def create_app(deps: Deps | None = None) -> FastAPI:
    state: dict = {"deps": deps, "ready": False, "warm_error": None}
    slots = threading.BoundedSemaphore(2)  # investigations running at once; the rest wait

    def warm() -> None:
        try:
            d = state["deps"]
            d.graph.warm()  # BigQuery client up before the first GQL call
            d.store.warm()
            d.cases.latest()
            if hasattr(d.backend, "warm"):
                d.backend.warm()  # model clients up before the first investigation
            state["ready"] = True
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
    async def run(alert_id: str, force: bool = False):
        if d().store.get_alert(alert_id) is None:
            raise HTTPException(404, f"unknown alert {alert_id}")
        if force:
            ok, retry = d().limiter.allow()
            if not ok:
                raise HTTPException(
                    429, "force=true is rate limited", headers={"Retry-After": str(retry)}
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
                    q.put(("result", investigate(d(), alert_id, force=force, listeners=[q.put])))
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
        try:
            return record_decision(
                d().audit_factory(), case, body.analyst, body.decision, body.note
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    return app


app = create_app()
