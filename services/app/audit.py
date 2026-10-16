"""Audit sink: every step, tool call and verdict goes to audit.agent_events.

Rows are buffered per run and flushed with streaming inserts (no query, no slots). ``flush()`` hands
the rows to a single background worker so a response never waits for the insert; ``drain_all()``
waits for the worker (call it before a script exits and at service shutdown).
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
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


_worker = ThreadPoolExecutor(1, thread_name_prefix="audit-flush")  # one worker keeps order
_pending: list[Future] = []
_pending_lock = threading.Lock()
failures: list[str] = []


def drain_all(timeout: float = 60.0) -> None:
    """Wait for every queued audit insert."""
    with _pending_lock:
        futures, _pending[:] = list(_pending), []
    for f in futures:
        f.result(timeout=timeout)


class BigQueryAudit:
    TABLE = "audit.agent_events"

    def __init__(self, project: str, client=None):
        self.project, self._client = project, client
        self._buf: list[dict] = []
        self._lock = threading.Lock()

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
        if rows:
            fut = _worker.submit(self._insert, rows)
            with _pending_lock:
                _pending.append(fut)

    def _insert(self, rows: list[dict]) -> None:
        try:
            errs = self.client.insert_rows_json(f"{self.project}.{self.TABLE}", rows)
            if errs:
                raise RuntimeError(str(errs)[:300])
        except Exception as e:  # noqa: BLE001  never lose the failure silently
            failures.append(f"{type(e).__name__}: {str(e)[:200]}")
            print(f"audit insert failed: {failures[-1]}", file=sys.stderr)
