"""Analyst decisions on agent cases: override a clear, or confirm it.

Append only (risk_curated.case_decisions); the latest decision per alert wins. An override moves
a case from the agent-cleared lane to the escalated lane, but it still needs the normal analyst
approval before anything is sent (the mocked webhook).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Protocol

from services.app.runctx import now_iso

DECISIONS = ("override_to_escalate", "confirm_clear")


class DecisionStore(Protocol):
    def latest(self) -> dict[str, dict]: ...
    def put(self, row: dict) -> None: ...


class MemoryDecisions:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def latest(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self.rows:
            if r["alert_id"] not in out or r["ts"] >= out[r["alert_id"]]["ts"]:
                out[r["alert_id"]] = r
        return out

    def put(self, row: dict) -> None:
        self.rows.append(row)


class BigQueryDecisions(MemoryDecisions):
    TABLE = "risk_curated.case_decisions"

    def __init__(self, project: str, client=None):
        super().__init__()
        self.project, self._client, self._loaded_at = project, client, None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project, location="US")
        return self._client

    TTL_S = 30.0  # an override made on another instance shows up within this time

    def _load(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._loaded_at is not None and now - self._loaded_at < self.TTL_S:
                return
            fresh = [
                {
                    k: (v.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if k == "ts" else v)
                    for k, v in dict(r.items()).items()
                }
                for r in self.client.list_rows(f"{self.project}.{self.TABLE}")
            ]
            seen = {r["decision_id"] for r in fresh}
            self.rows = fresh + [r for r in self.rows if r["decision_id"] not in seen]
            self._loaded_at = now

    def latest(self) -> dict[str, dict]:
        self._load()
        return super().latest()

    def put(self, row: dict) -> None:
        self._load()
        errs = self.client.insert_rows_json(f"{self.project}.{self.TABLE}", [row])
        if errs:
            raise RuntimeError(f"case_decisions insert failed: {str(errs)[:300]}")
        super().put(row)


def decision_row(case: dict, analyst: str, decision: str, note: str) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if not analyst.strip():
        raise ValueError("analyst is required")
    report = case["report"]
    if report.get("lane", "needs_analyst") == "escalated":
        raise ValueError("the case is already escalated; use approve or reject")
    return {
        "decision_id": f"DEC_{uuid.uuid4().hex[:10]}",
        "alert_id": report["alert_id"],
        "case_run_id": case["run_id"],
        "ts": now_iso(),
        "analyst": analyst.strip(),
        "decision": decision,
        "note": note[:500],
        "previous_lane": report.get("lane", ""),
    }


def effective_lane(report: dict, decision: dict | None) -> tuple[str, bool]:
    """(lane, analyst_changed_it): an override to escalate moves the case to `escalated`."""
    lane = report.get("lane", "needs_analyst")
    if decision and decision["decision"] == "override_to_escalate":
        return "escalated", True
    return lane, bool(decision)
