"""Run state, counters and the event log of the stream path.

One ``Run`` per replay or poison message. Counters are kept in memory (one instance) and every
event also goes to ``audit.stream_events`` through a background flusher, so a restart loses the
live counters but not the log.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

EVENT_TYPES = (
    "published", "ingested", "duplicate_dropped", "malformed", "retry",
    "dead_letter_candidate", "scored", "alert_raised",
)  # fmt: skip


def new_run_id() -> str:
    return "r-" + dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]


@dataclass
class Run:
    run_id: str
    kind: str  # replay | poison | external
    expected: int = 0  # messages the publisher will send
    state: str = "publishing"  # publishing | ingesting | scoring | finished | error | stalled
    published: int = 0
    processed: int = 0  # valid messages acked (new or duplicate)
    ingested: int = 0  # rows new to BigQuery
    duplicates_dropped: int = 0
    retried: int = 0
    malformed: int = 0
    dead_letter: int = 0
    alerts_raised: int = 0
    scoring_queries: int = 0
    scoring_active: bool = False
    error: str | None = None
    started_wall: float = field(default_factory=time.time)
    t_start: float = field(default_factory=time.monotonic)
    t_first_publish: float | None = None
    t_last_publish: float | None = None
    t_alert: float | None = None
    t_end: float | None = None
    t_activity: float = field(default_factory=time.monotonic)
    alert_ids: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.state in ("publishing", "ingesting", "scoring")

    def progress(self) -> float:
        if self.state == "finished":
            return 1.0
        if self.expected <= 0:
            return 0.0
        done = self.processed + (self.dead_letter if self.kind == "poison" else 0)
        # publishing and ingesting are 90 % of the bar; scoring is the last 10 %
        return min(0.9, 0.9 * done / self.expected) + (0.05 if self.state == "scoring" else 0.0)

    def timings(self) -> dict:
        def gap(a, b):
            return None if a is None or b is None else round(b - a, 2)

        return {
            "publish_s": gap(self.t_first_publish, self.t_last_publish),
            "last_publish_to_alert_s": gap(self.t_last_publish, self.t_alert),
            "total_s": gap(self.t_start, self.t_end),
            "elapsed_s": round(time.monotonic() - self.t_start, 2),
        }


class StreamRuntime:
    def __init__(self, store, overlay=None, clock=time.monotonic):
        self.store, self.overlay, self._clock = store, overlay, clock
        self.runs: dict[str, Run] = {}
        self.order: list[str] = []
        self.latest: str | None = None
        self.lock = threading.RLock()
        self._events: deque[dict] = deque(maxlen=400)  # recent, for the status call
        self._pending: list[dict] = []
        self.event_failures: list[str] = []

    # ---- runs ----

    def start_run(self, kind: str, expected: int, run_id: str | None = None) -> Run:
        with self.lock:
            run = Run(run_id or new_run_id(), kind, expected=expected)
            self.runs[run.run_id] = run
            self.order.append(run.run_id)
            for old in self.order[:-20]:  # keep the last 20 runs
                self.runs.pop(old, None)
            self.order = self.order[-20:]
            if kind == "replay":
                self.latest = run.run_id
                if self.overlay is not None:
                    self.overlay.clear_alerts()  # streamed alerts are replaced by a newer run
            return run

    def get(self, run_id: str) -> Run | None:
        with self.lock:
            return self.runs.get(run_id)

    def run_for_message(self, run_id: str) -> Run:
        """The run a message belongs to; an unknown id (another instance, a restart) is tracked
        as an ``external`` run so its events are still counted."""
        with self.lock:
            run = self.runs.get(run_id)
            if run is None:
                run = self.start_run("external", 0, run_id)
            return run

    def active_run(self) -> Run | None:
        with self.lock:
            return next((r for r in self.runs.values() if r.active and r.kind != "external"), None)

    # ---- events ----

    def record(
        self, run_id: str, event_type: str, message_id: str = "", attempt: int | None = None,
        detail: str = "",
    ) -> None:  # fmt: skip
        assert event_type in EVENT_TYPES, event_type
        row = {
            "run_id": run_id,
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "event_type": event_type,
            "message_id": message_id or None,
            "delivery_attempt": attempt,
            "detail": detail[:500] or None,
        }
        with self.lock:
            self._events.append(row)
            self._pending.append(row)
            r = self.runs.get(run_id)
            if r:
                r.t_activity = self._clock()

    def flush_events(self) -> int:
        with self.lock:
            rows, self._pending = self._pending, []
        if not rows:
            return 0
        try:
            self.store.write_events(rows)
        except Exception as e:  # noqa: BLE001  the log must not take the pipeline down
            self.event_failures.append(f"{type(e).__name__}: {str(e)[:160]}")
            print(f"stream: event log write failed: {self.event_failures[-1]}", flush=True)
        return len(rows)

    def recent_events(self, run_id: str, n: int = 15) -> list[dict]:
        with self.lock:
            return [e for e in self._events if e["run_id"] == run_id][-n:]

    # ---- status ----

    def status(self, run_id: str | None = None) -> dict | None:
        with self.lock:
            run = self.runs.get(run_id or self.latest or "")
            if run is None:
                return None
            return {
                "run_id": run.run_id,
                "kind": run.kind,
                "state": run.state,
                "active": run.active,
                "expected": run.expected,
                "progress": round(run.progress(), 3),
                "counters": {
                    "published": run.published,
                    "ingested": run.ingested,
                    "duplicates_dropped": run.duplicates_dropped,
                    "retried": run.retried,
                    "malformed": run.malformed,
                    "dead_letter": run.dead_letter,
                    "alerts_raised": run.alerts_raised,
                },
                "scoring_queries": run.scoring_queries,
                "timings": run.timings(),
                "alert_ids": list(run.alert_ids),
                "error": run.error,
                "events": self.recent_events(run.run_id),
            }
