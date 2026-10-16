"""The only place a BigQuery job is created: read-only guard, byte cap and timeout on each."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any

from services.graph.guard import assert_read_only

LOCATION = "US"
MAX_BYTES_BILLED = 100 * 1024 * 1024
DEFAULT_TIMEOUT_S = 15.0


class GraphTimeout(TimeoutError):
    """A query did not finish inside the deadline."""


def _param(name: str, value: Any):
    from google.cloud import bigquery

    if isinstance(value, list | tuple):
        return bigquery.ArrayQueryParameter(name, "STRING", list(value))
    return bigquery.ScalarQueryParameter(name, "STRING", value)


class BigQueryRunner:
    """``query(sql, params, deadline)`` -> list of row dicts."""

    def __init__(self, project: str, client=None, max_bytes: int = MAX_BYTES_BILLED):
        self.project, self.max_bytes = project, max_bytes
        self._client = client
        self._lock = threading.Lock()

    @property
    def client(self):
        with self._lock:  # built once even when several threads ask at the same time
            if self._client is None:
                from google.cloud import bigquery

                self._client = bigquery.Client(project=self.project, location=LOCATION)
            return self._client

    def query(
        self, sql: str, params: dict[str, Any] | None = None, deadline: float | None = None
    ) -> list[dict]:
        """``deadline`` is a time.monotonic() value; the job is cancelled if it is passed."""
        assert_read_only(sql)
        from google.cloud import bigquery

        client = self.client  # create before the clock is read, so setup time is not hidden
        remaining = DEFAULT_TIMEOUT_S if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise GraphTimeout("deadline already passed")
        cfg = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.max_bytes,
            query_parameters=[_param(k, v) for k, v in (params or {}).items()],
            job_timeout_ms=int(remaining * 1000),
        )
        job = client.query(sql, job_config=cfg, location=LOCATION)
        try:
            return [dict(r.items()) for r in job.result(timeout=remaining)]
        except concurrent.futures.TimeoutError as e:
            job.cancel()
            raise GraphTimeout(f"query exceeded {remaining:.0f}s") from e
