"""Case report cache: risk_curated.case_reports (append only, latest created_ts per alert wins).

Reads use list_rows (free, includes the streaming buffer); writes are streaming inserts.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol


class CaseStore(Protocol):
    def get(self, alert_id: str) -> dict | None: ...
    def put(self, row: dict) -> None: ...
    def latest(self) -> dict[str, dict]: ...


class MemoryCases:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def get(self, alert_id: str) -> dict | None:
        return self.latest().get(alert_id)

    def put(self, row: dict) -> None:
        self.rows.append(row)

    def latest(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self.rows:
            if r["alert_id"] not in out or r["created_ts"] >= out[r["alert_id"]]["created_ts"]:
                out[r["alert_id"]] = r
        return out


class BigQueryCases(MemoryCases):
    TABLE = "risk_curated.case_reports"

    def __init__(self, project: str, client=None):
        super().__init__()
        self.project, self._client = project, client
        self._loaded_at: float | None = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project, location="US")
        return self._client

    TTL_S = 30.0  # other instances write too, and the streaming buffer settles late

    def _load(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._loaded_at is not None and now - self._loaded_at < self.TTL_S:
                return
            fresh = [
                {
                    k: (v.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if k == "created_ts" else v)
                    for k, v in dict(r.items()).items()
                }
                for r in self.client.list_rows(f"{self.project}.{self.TABLE}")
            ]
            # keep rows this instance inserted that the read does not show yet
            seen = {r["case_id"] for r in fresh}
            self.rows = fresh + [r for r in self.rows if r["case_id"] not in seen]
            self._loaded_at = now

    def latest(self) -> dict[str, dict]:
        self._load()
        return super().latest()

    def get(self, alert_id: str) -> dict | None:
        hit = super().get(alert_id)
        if hit is None and self._loaded_at is not None and time.monotonic() - self._loaded_at > 5:
            # a miss may only mean this instance read the table too early: look again
            with self._lock:
                self._loaded_at = None
            hit = super().get(alert_id)
        return hit

    def put(self, row: dict) -> None:
        self._load()
        errs = self.client.insert_rows_json(f"{self.project}.{self.TABLE}", [row])
        if errs:
            raise RuntimeError(f"case_reports insert failed: {str(errs)[:300]}")
        super().put(row)
