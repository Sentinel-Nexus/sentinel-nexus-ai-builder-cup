"""Read side of the audit trail: a run's timeline, recent events, analyst decisions.

BigQuery reads are small parameterised queries (50 MB billed cap, 20 s timeout). A finished run is
immutable, so its timeline is cached for the life of the process; the recent list is cached for a
few seconds. ``MemoryAuditReader`` serves the tests from a MemoryAudit sink.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from typing import Protocol

DECISION_TYPES = ("approval", "override")
MAX_BYTES = 50_000_000
QUERY_TIMEOUT_S = 20
RECENT_TTL_S = 8.0
COLUMNS = (
    "event_id, run_id, seq, ts, alert_id, agent, event_type, tool, summary, evidence_ids, "
    "engine, cached, latency_ms, tokens, detail"
)


class AuditReader(Protocol):
    def run_events(self, run_id: str) -> list[dict]: ...
    def recent(self, limit: int = 200, alert_id: str | None = None) -> list[dict]: ...
    def decisions(self) -> list[dict]: ...
    def invalidate(self) -> None: ...


def _clean(row: dict) -> dict:
    out = dict(row)
    ts = out.get("ts")
    if isinstance(ts, dt.datetime):
        out["ts"] = ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    out["evidence_ids"] = list(out.get("evidence_ids") or [])
    return out


class MemoryAuditReader:
    def __init__(self, factory: Callable[[], object]):
        self._factory = factory

    def _events(self) -> list[dict]:
        return [_clean(e) for e in getattr(self._factory(), "events", [])]

    def run_events(self, run_id: str) -> list[dict]:
        return sorted((e for e in self._events() if e["run_id"] == run_id), key=lambda e: e["seq"])

    def recent(self, limit: int = 200, alert_id: str | None = None) -> list[dict]:
        rows = [e for e in self._events() if alert_id in (None, e["alert_id"])]
        return sorted(rows, key=lambda e: (e["ts"], e["seq"]), reverse=True)[:limit]

    def decisions(self) -> list[dict]:
        return [e for e in self.recent(1000) if e["event_type"] in DECISION_TYPES]

    def invalidate(self) -> None:
        pass


class BigQueryAuditReader:
    TABLE = "audit.agent_events"

    def __init__(self, project: str, client=None, clock=time.monotonic):
        self.project, self._client, self._clock = project, client, clock
        self._runs: dict[str, list[dict]] = {}
        self._recent: dict[tuple, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project, location="US")
        return self._client

    def _query(self, sql: str, params: list) -> list[dict]:
        from google.cloud import bigquery

        cfg = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES, query_parameters=params)
        job = self.client.query(sql, job_config=cfg)
        return [_clean(dict(r.items())) for r in job.result(timeout=QUERY_TIMEOUT_S)]

    def run_events(self, run_id: str) -> list[dict]:
        from google.cloud import bigquery

        with self._lock:
            if run_id in self._runs:
                return self._runs[run_id]
        rows = self._query(
            f"SELECT {COLUMNS} FROM `{self.project}.{self.TABLE}` "
            "WHERE run_id = @run_id ORDER BY seq LIMIT 500",
            [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)],
        )
        if rows:  # an empty answer may just be the streaming buffer: do not remember it
            with self._lock:
                self._runs[run_id] = rows
        return rows

    def recent(self, limit: int = 200, alert_id: str | None = None) -> list[dict]:
        from google.cloud import bigquery

        key = (limit, alert_id)
        with self._lock:
            hit = self._recent.get(key)
            if hit and self._clock() - hit[0] < RECENT_TTL_S:
                return hit[1]
        params = [bigquery.ScalarQueryParameter("n", "INT64", limit)]
        where = ""
        if alert_id:
            where = "WHERE alert_id = @alert_id "
            params.append(bigquery.ScalarQueryParameter("alert_id", "STRING", alert_id))
        rows = self._query(
            f"SELECT {COLUMNS} FROM `{self.project}.{self.TABLE}` {where}"
            "ORDER BY ts DESC, seq DESC LIMIT @n",
            params,
        )
        with self._lock:
            self._recent[key] = (self._clock(), rows)
        return rows

    def decisions(self) -> list[dict]:
        from google.cloud import bigquery

        key = ("decisions",)
        with self._lock:
            hit = self._recent.get(key)
            if hit and self._clock() - hit[0] < RECENT_TTL_S:
                return hit[1]
        rows = self._query(
            f"SELECT {COLUMNS} FROM `{self.project}.{self.TABLE}` "
            "WHERE event_type IN UNNEST(@types) ORDER BY ts DESC LIMIT 500",
            [bigquery.ArrayQueryParameter("types", "STRING", list(DECISION_TYPES))],
        )
        with self._lock:
            self._recent[key] = (self._clock(), rows)
        return rows

    def invalidate(self) -> None:
        with self._lock:
            self._recent.clear()
