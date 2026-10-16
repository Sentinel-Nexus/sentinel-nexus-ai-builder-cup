"""One investigation run: budget, ledger, event stream and the audited tool-call wrapper."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from services.app.audit import AuditSink
from services.app.budget import BudgetExceeded, RunBudget
from services.app.config import Settings
from services.app.ledger import Ledger


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class RunContext:
    alert: dict
    settings: Settings
    audit: AuditSink
    run_id: str = field(default_factory=lambda: "RUN_" + uuid.uuid4().hex[:12])
    listeners: list[Callable[[dict], None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ledger = Ledger()
        self.budget = RunBudget(self.settings.limits)
        self.engines: set[str] = set()
        self.cached_graph_calls = 0
        self.ring_members: dict[str, list[str]] = {}
        self.started = time.monotonic()
        self.deadline = time.time() + self.settings.limits.wall_clock_s  # wall clock, not monotonic
        self._seq = 0
        self._lock = threading.Lock()
        self.events: list[dict] = []
        self.meta: dict = {}

    # ---- events ----

    def emit(self, agent: str, event_type: str, summary: str = "", **fields) -> dict:
        with self._lock:
            self._seq += 1
            seq = self._seq
        args = fields.pop("args", None)
        event = {
            "event_id": f"{self.run_id}:{seq}",
            "run_id": self.run_id,
            "seq": seq,
            "ts": now_iso(),
            "alert_id": self.alert["alert_id"],
            "agent": agent,
            "event_type": event_type,
            "tool": fields.pop("tool", None),
            "args": None if args is None else json.dumps(args, default=str),
            "summary": summary[:1000],
            "evidence_ids": fields.pop("evidence_ids", None) or [],
            "engine": fields.pop("engine", None),
            "cached": fields.pop("cached", None),
            "latency_ms": fields.pop("latency_ms", None),
            "tokens": fields.pop("tokens", None),
            "detail": json.dumps(fields, default=str)[:8000] if fields else None,
        }
        self.events.append(event)
        self.audit.write(event)
        for listener in self.listeners:
            listener(event)
        return event

    # ---- tools ----

    def call_tool(self, agent: str, name: str, args: dict, fn: Callable[[], tuple[dict, dict]]):
        """Run a tool under the call cap. ``fn`` returns (full result, compact result for the
        model). The full result goes to the ledger, the compact one back to the model."""
        try:
            n = self.budget.charge_tool()
        except BudgetExceeded:
            self.emit(
                agent,
                "limit",
                f"tool call refused: cap of {self.budget.limits.max_tool_calls}",
                tool=name,
                args=args,
                limit="tool_calls",
            )
            return {
                "error": "tool_call_limit_reached",
                "message": "No tool calls left. Answer now from the evidence already gathered.",
            }
        t0 = time.monotonic()
        try:
            full, compact = fn()
        except Exception as e:  # noqa: BLE001  a bad tool call is reported to the model, not raised
            ms = int((time.monotonic() - t0) * 1000)
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            self.emit(
                agent,
                "tool_call",
                f"error: {msg}",
                tool=name,
                args=args,
                latency_ms=ms,
                error=msg,
                tool_call_no=n,
            )
            return {"error": msg}
        ms = int((time.monotonic() - t0) * 1000)
        engine, cached = full.get("engine", "table"), bool(full.get("cached", False))
        ids = self.ledger.record(n, name, full)
        if name in ("graph", "trades_for"):
            self.engines.add(engine)
            self.cached_graph_calls += int(cached)
        self.emit(
            agent,
            "tool_call",
            f"{name} ok ({len(ids)} evidence ids)",
            tool=name,
            args=args,
            evidence_ids=ids[:60],
            engine=engine,
            cached=cached,
            latency_ms=ms,
            tool_call_no=n,
            fallback_reason=full.get("fallback_reason"),
        )
        return compact

    def remaining_s(self) -> float:
        return self.deadline - time.time()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)
