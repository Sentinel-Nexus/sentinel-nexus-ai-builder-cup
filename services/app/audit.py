"""Audit sink: every step, tool call and verdict goes to audit.agent_events.

Rows are buffered per run and flushed with streaming inserts (no query, no slots).
"""

from __future__ import annotations

import threading
from typing import Protocol


class AuditSink(Protocol):
    def write(self, event: dict) -> None: ...
    def flush(self) -> None: ...


class MemoryAudit:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)

    def flush(self) -> None:
        pass


class BigQueryAudit:
    TABLE = "audit.agent_events"

    def __init__(self, project: str, client=None):
        self.project, self._client = project, client
        self._buf: list[dict] = []
        self._lock = threading.Lock()
        self.errors: list[str] = []

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project, location="US")
        return self._client

    def write(self, event: dict) -> None:
        with self._lock:
            self._buf.append(event)

    def flush(self) -> None:
        with self._lock:
            rows, self._buf = self._buf, []
        if not rows:
            return
        errs = self.client.insert_rows_json(f"{self.project}.{self.TABLE}", rows)
        if errs:  # never lose the failure silently
            self.errors.append(str(errs)[:300])
            raise RuntimeError(f"audit insert failed: {str(errs)[:300]}")
