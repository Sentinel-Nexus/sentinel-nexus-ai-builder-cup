"""Case report cache: risk_curated.case_reports (append only, latest created_ts per alert wins).

Reads use list_rows (free, includes the streaming buffer); writes are streaming inserts.
"""

from __future__ import annotations

import threading
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
        self._loaded = False
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project, location="US")
        return self._client

    def _load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self.rows = [
                {
                    k: (v.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if k == "created_ts" else v)
                    for k, v in dict(r.items()).items()
                }
                for r in self.client.list_rows(f"{self.project}.{self.TABLE}")
            ]
            self._loaded = True

    def latest(self) -> dict[str, dict]:
        self._load()
        return super().latest()

    def put(self, row: dict) -> None:
        self._load()
        errs = self.client.insert_rows_json(f"{self.project}.{self.TABLE}", [row])
        if errs:
            raise RuntimeError(f"case_reports insert failed: {str(errs)[:300]}")
        super().put(row)
