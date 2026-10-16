"""Read endpoints and static files for the analyst workbench.

Everything here is read-only except what main.py already writes (approve, override, investigate).
Case graphs, trades and timelines are cached in memory after the first read, so browsing cached
cases does not run queries.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from services.app.audit_read import DECISION_TYPES, MemoryAuditReader
from services.app.decisions import effective_lane
from services.app.deps import Deps
from services.app.models import LANES

STATIC = Path(__file__).resolve().parent / "static"
TRADE_ROWS = 300  # rows returned for one account
SEVERITY = ((60.0, "critical"), (50.0, "high"))  # score cut-offs; below is medium


def severity(alert: dict) -> str:
    """UI rule, not a detector output: a review-band alert is `review`, else by score."""
    if alert["status"] == "needs_review":
        return "review"
    return next((name for cut, name in SEVERITY if float(alert["score"]) >= cut), "medium")


def client_id(request: Request) -> str:
    """A hash of the caller's address. Cloud Run appends the real address to X-Forwarded-For, so
    the last entry is the one a caller cannot choose. Never stored or logged in clear."""
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "")
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def register(app: FastAPI, d: Callable[[], Deps], state: dict) -> None:
    graph_cache: dict[str, dict] = {}
    trade_cache: dict[str, dict] = {}
    lock = threading.Lock()
    log: dict[str, dict] = {}  # analyst decisions made on this instance: run_id -> row

    def reader():
        return d().audit_reader or MemoryAuditReader(d().audit_factory)

    state["decision_log"] = log
    state["reader"] = reader

    def alert_or_404(alert_id: str) -> dict:
        a = d().store.get_alert(alert_id)
        if a is None:
            raise HTTPException(404, f"unknown alert {alert_id}")
        return a

    def report_of(alert_id: str) -> dict:
        row = d().cases.get(alert_id)
        if row is None:
            raise HTTPException(404, "no cached case for this alert")
        report = json.loads(row["report"])
        report.setdefault("lane", LANES.get(report["decision"], "needs_analyst"))
        return report

    def decided() -> dict[str, dict]:
        """Latest analyst approval or rejection per alert (audit trail plus this instance)."""
        rows: dict[str, dict] = {}
        try:
            events = reader().decisions()
        except Exception:  # noqa: BLE001  the queue still renders if the audit read fails
            events = []
        for e in events:
            word = e["summary"].split(" ")[0]
            if e["event_type"] == "approval" or word == "confirm_clear":
                rows[e["run_id"]] = {"alert_id": e["alert_id"], "decision": word, "ts": e["ts"]}
        for run_id, r in log.items():
            if r["kind"] == "approval":
                rows[run_id] = r
        latest: dict[str, dict] = {}
        for r in sorted(rows.values(), key=lambda r: r["ts"]):
            latest[r["alert_id"]] = r
        return latest

    # ---- views ----

    @app.get("/summary")
    def summary():
        """Counts for the KPI strip. Lanes use the analyst override; `open` means no analyst
        approve or reject is on record."""
        decisions = d().decisions.latest()
        done = decided()
        alerts, lanes = d().store.alerts(), {"escalated": 0, "agent_cleared": 0, "needs_analyst": 0}
        no_case = 0
        have = d().cases.latest()
        for a in alerts:
            row = have.get(a["alert_id"])
            if row is None:
                no_case += 1
                continue
            lane, _ = effective_lane(_lane_report(row), decisions.get(a["alert_id"]))
            lanes[lane] += 1
        bench = _benchmark()
        return {
            "alerts": len(alerts),
            "open": sum(1 for a in alerts if a["alert_id"] not in done),
            "decided": len(done),
            "no_case": no_case,
            "lanes": lanes,
            "detector": bench["kpi"]["detector"] if bench else None,
            "baseline": bench["kpi"]["baseline"] if bench else None,
            "synthetic": True,
        }

    def _lane_report(row: dict) -> dict:
        report = json.loads(row["report"])
        report.setdefault("lane", LANES.get(report["decision"], "needs_analyst"))
        return report

    @app.get("/queue")
    def queue():
        """The alert queue with severity, lane and any analyst decision."""
        have, decisions, done = d().cases.latest(), d().decisions.latest(), decided()
        out = []
        for a in d().store.alerts():
            row = have.get(a["alert_id"])
            lane = (
                effective_lane(_lane_report(row), decisions.get(a["alert_id"]))[0] if row else None
            )
            out.append(
                {
                    **{
                        k: a[k]
                        for k in ("alert_id", "ring_id", "status", "scenario_hint", "n_members")
                    },
                    "score": round(float(a["score"]), 2),
                    "threshold": a.get("threshold"),
                    "severity": severity(a),
                    "has_case": row is not None,
                    "lane": lane,
                    "agent_decision": row["decision"] if row else None,
                    "analyst_decision": done.get(a["alert_id"], {}).get("decision"),
                }
            )
        order = {"critical": 0, "high": 1, "medium": 2, "review": 3}
        return sorted(out, key=lambda r: (order[r["severity"]], -r["score"]))

    @app.get("/cases/{alert_id}/detail")
    def case_detail(alert_id: str):
        """The cached case with its run facts, the alert and the analyst's decisions."""
        alert = alert_or_404(alert_id)
        row = d().cases.get(alert_id)
        if row is None:
            return {"cached": False, "alert": _alert_view(alert), "report": None}
        dec = d().decisions.latest().get(alert_id)
        report = _lane_report(row)
        lane, changed = effective_lane(report, dec)
        return {
            "cached": True,
            "alert": _alert_view(alert),
            "case_created_ts": row["created_ts"],
            "run_id": row["run_id"],
            "source": row.get("source"),
            "models": json.loads(row["models"]) if row.get("models") else {},
            "retrieval": row.get("retrieval"),
            "engines": row.get("graph_engines"),
            "budget_status": row.get("budget_status"),
            "tool_calls": row.get("tool_calls"),
            "tokens": row.get("tokens_total"),
            "latency_ms": row.get("latency_ms"),
            "report": report,
            "effective_lane": lane,
            "analyst_changed": changed,
            "analyst_override": dec,
        }

    def _alert_view(a: dict) -> dict:
        view = {
            k: a[k]
            for k in (
                "alert_id",
                "ring_id",
                "status",
                "scenario_hint",
                "n_members",
                "member_accounts",
                "evidence_trade_ids",
                "evidence_edge_ids",
                "threshold",
            )
            if k in a
        }
        view["score"] = round(float(a["score"]), 2)
        view["severity"] = severity(a)
        try:
            view["top_features"] = json.loads(a["top_features"]) if a.get("top_features") else {}
        except (TypeError, ValueError):
            view["top_features"] = {}
        view["evidence_trade_ids"] = view.get("evidence_trade_ids", [])[:200]
        return view

    @app.get("/cases/{alert_id}/timeline")
    def timeline(alert_id: str, run_id: str | None = None):
        """Audit events of the case's run. `run_id` shows another run (a live one)."""
        alert_or_404(alert_id)
        if run_id is None:
            run_id = _run_id(alert_id)
        try:
            events = reader().run_events(run_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"audit read failed: {type(e).__name__}") from e
        events = [e for e in events if e["alert_id"] == alert_id]
        return {"run_id": run_id, "events": events, "summary": _timeline_summary(events)}

    def _run_id(alert_id: str) -> str:
        row = d().cases.get(alert_id)
        if row is None:
            raise HTTPException(404, "no cached case for this alert")
        return row["run_id"]

    @app.get("/cases/{alert_id}/graph")
    def case_graph(alert_id: str):
        """The ring subgraph for the case, with the alert's evidence edges marked."""
        alert = alert_or_404(alert_id)
        with lock:
            hit = graph_cache.get(alert_id)
        if hit is None:
            try:
                g = d().graph.ring_subgraph(alert["ring_id"])
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"graph read failed: {type(e).__name__}") from e
            hit = {
                "ring_id": alert["ring_id"],
                "nodes": g["nodes"],
                "edges": g["edges"],
                "truncated": g.get("truncated", False),
                "engine": g["engine"],
                "fallback_reason": g.get("fallback_reason"),
                "evidence_edge_ids": list(alert.get("evidence_edge_ids", [])),
                "members_alert": list(alert["member_accounts"]),
            }
            with lock:
                graph_cache[alert_id] = hit
        trade_accounts: dict[str, str] = {}
        # lets an evidence chip with a trade id light up the account that made it
        with contextlib.suppress(HTTPException):
            trade_accounts = {t["id"]: t["account_id"] for t in _trades(alert_id)["trades"]}
        return {**hit, "cached": True, "trade_accounts": trade_accounts}

    def _trades(alert_id: str) -> dict:
        alert = alert_or_404(alert_id)
        with lock:
            hit = trade_cache.get(alert_id)
        if hit is None:
            members = list(alert["member_accounts"])[:200]
            market = d().store.dominant_market(members)
            if market is None:
                hit = {"market_id": None, "trades": [], "edges": [], "engine": "table"}
            else:
                try:
                    g = d().graph.trades_for(members, market)
                except Exception as e:  # noqa: BLE001
                    raise HTTPException(502, f"trade read failed: {type(e).__name__}") from e
                hit = {
                    "market_id": market,
                    "trades": g["trades"],
                    "edges": g["edges"],
                    "truncated": g.get("truncated", False),
                    "engine": g["engine"],
                }
            with lock:
                trade_cache[alert_id] = hit
        return hit

    @app.get("/cases/{alert_id}/trades")
    def case_trades(alert_id: str, account: str = Query(min_length=1, max_length=64)):
        """Trades of one account in the ring's main market (the click-a-node panel)."""
        alert = alert_or_404(alert_id)
        members = set(alert["member_accounts"])
        report = None
        row = d().cases.get(alert_id)
        if row:
            report = json.loads(row["report"])
            members |= set(report.get("members_linked", []))
        if account not in members:
            raise HTTPException(404, "account is not a member of this case")
        t = _trades(alert_id)
        rows = [r for r in t["trades"] if r.get("account_id") == account]
        edge = next((e for e in t["edges"] if e.get("account_id") == account), None)
        markets = d().store.markets_of(account)[:5]
        return {
            "account_id": account,
            "market_id": t["market_id"],
            "summary": edge,
            "other_markets": [
                {
                    "market_id": m["market_id"],
                    "n_trades": m["n_trades"],
                    "total_usd": m["total_usd"],
                }
                for m in markets
            ],
            "trades": rows[:TRADE_ROWS],
            "n_trades": len(rows),
            "truncated": t.get("truncated", False) or len(rows) > TRADE_ROWS,
            "engine": t["engine"],
            "score": d().store.score_of(account),
        }

    @app.get("/cases/{alert_id}/export")
    def export_case(alert_id: str):
        """One JSON file with the alert, case, timeline and analyst decisions."""
        alert = alert_or_404(alert_id)
        detail = case_detail(alert_id)
        run_id = detail.get("run_id")
        try:
            events = reader().run_events(run_id) if run_id else []
            trail = reader().recent(200, alert_id=alert_id)
        except Exception:  # noqa: BLE001
            events, trail = [], []
        body = {
            "export": "marketsentinel.case.v1",
            "synthetic_data": True,
            "alert": _alert_view(alert),
            "case": detail,
            "timeline": events,
            "analyst_events": [e for e in trail if e["event_type"] in DECISION_TYPES],
        }
        return Response(
            json.dumps(body, indent=1, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="case_{alert_id}.json"'},
        )

    @app.get("/audit")
    def audit(
        alert_id: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=150, ge=1, le=500),
        event_type: str | None = Query(default=None, max_length=32),
    ):
        try:
            events = reader().recent(limit, alert_id=alert_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"audit read failed: {type(e).__name__}") from e
        if event_type:
            events = [e for e in events if e["event_type"] == event_type]
        return {"events": events, "n": len(events)}

    @app.get("/benchmark")
    def benchmark():
        data = _benchmark()
        if data is None:
            raise HTTPException(404, "benchmark data not built: run scripts/build_ui_data.py")
        return data

    @app.get("/governance")
    def governance(request: Request):
        s, lim = d().settings, d().settings.limits
        return {
            "synthetic": True,
            "limits": {
                "max_tool_calls": lim.max_tool_calls,
                "total_tokens": lim.total_tokens,
                "agent_tokens": lim.agent_tokens,
                "max_output_tokens": lim.out_tokens,
                "wall_clock_s": lim.wall_clock_s,
                "graph_budget_s": lim.graph_budget_s,
                "llm_concurrency": s.llm_concurrency,
                "run_concurrency": s.run_concurrency,
            },
            "live": {
                **d().limiter.status(client_id(request)),
                "persist_live_results": s.persist_forced,
                "note": "Limits are counted per service instance, so with N instances the "
                "service-wide ceiling is N times the figure shown.",
            },
            "models": d().backend.models,
            "retrieval": "keyword",
            "guards": _guards(),
        }

    def _guards() -> list[dict]:
        return [
            {
                "name": "Evidence ledger",
                "status": "active",
                "detail": "A claim stays in a report only if every evidence id it cites was "
                "returned by a tool call in the same run.",
            },
            {
                "name": "Number check",
                "status": "active",
                "detail": "Decimals and 3+ digit numbers in a claim must appear in the cited rows.",
            },
            {
                "name": "LLM Critic",
                "status": "active",
                "detail": "A second model checks that the cited rows support each claim; one "
                "send-back, then unsupported claims are listed as removed.",
            },
            {
                "name": "Escalation guard",
                "status": "active",
                "detail": "An escalation needs named accounts and two supported claims; "
                "otherwise it is downgraded to uncertain.",
            },
            {
                "name": "Analyst approval",
                "status": "active",
                "detail": "Agents never act on accounts. Approve, reject and escalate need an "
                "analyst and a written reason, and are written to the audit trail.",
            },
            {
                "name": "Outbound webhook",
                "status": "mock",
                "detail": "Fires only after an approved escalation; it never leaves the process.",
            },
            {
                "name": "Model Armor prompt guard",
                "status": "not deployed",
                "detail": "Planned for P6 (streaming and governance).",
            },
            {
                "name": "PII redaction",
                "status": "not deployed",
                "detail": "Planned for P6. The data is synthetic and holds no personal data.",
            },
        ]

    # ---- the single page ----

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def prefetch() -> None:
        """Fill the in-memory view caches for the cached cases (a few small reads)."""
        for alert_id in sorted(d().cases.latest()):
            for fn in (
                lambda a=alert_id: case_graph(a),
                lambda a=alert_id: _trades(a),
                lambda a=alert_id: timeline(a),
            ):
                with contextlib.suppress(Exception):  # best effort; endpoints retry on demand
                    fn()

    state["prefetch"] = prefetch


def _benchmark() -> dict | None:
    path = STATIC / "data" / "benchmark.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _timeline_summary(events: list[dict]) -> dict:
    tools = [e for e in events if e["event_type"] == "tool_call"]
    verdicts = [e for e in events if e["event_type"] == "verdict"]
    return {
        "n_events": len(events),
        "tool_calls": len(tools),
        "engines": sorted({e["engine"] for e in tools if e.get("engine")}),
        "cached_tool_calls": sum(1 for e in tools if e.get("cached")),
        "verdicts": len(verdicts),
        "llm_calls": sum(1 for e in events if e["event_type"] == "llm_call"),
        "tokens": sum(int(e.get("tokens") or 0) for e in events if e["event_type"] == "llm_call"),
    }
