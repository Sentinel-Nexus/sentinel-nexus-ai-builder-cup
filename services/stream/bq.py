"""The only place the stream path creates BigQuery jobs.

Every job is dry-run first, runs with a 100 MB billed-bytes cap and a timeout, and passes a guard:

- ``read``: the existing read-only guard (services.graph.guard): one SELECT.
- ``merge``: one MERGE whose target is one of the four stream tables (nothing else is writable).
- ``score``: the frozen detector script (sql/views det_10..det_40) with its tables turned into
  script-local TEMP tables, so no dataset or permanent table is needed.

``StreamStore`` is the interface the consumer and the scorer use; ``MemoryStore`` is the test
double with the same MERGE semantics (insert only when the natural key is new).
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Protocol

from services.graph.guard import assert_read_only
from services.stream import config as C

LOCATION = "US"
_MERGE_TARGET = re.compile(r"^\s*MERGE\s+`[\w\-]+\.(\w+\.\w+)`", re.I)
_BAD_IN_SCRIPT = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|EXPORT|LOAD|EXECUTE|CALL)\b",
    re.I,
)


class UnsafeStatement(ValueError):
    """The statement is not one this path may run."""


def assert_stream_merge(sql: str) -> str:
    m = _MERGE_TARGET.match(sql)
    if not m or m.group(1) not in C.WRITABLE_TABLES:
        raise UnsafeStatement("MERGE target must be one of the four stream tables")
    if sql.count(";") > 0:
        raise UnsafeStatement("one statement only")
    return m.group(1)


def assert_scoring_script(sql: str) -> None:
    """Only TEMP tables are created; the sources are read; nothing is written elsewhere."""
    body = re.sub(r"--[^\n]*", " ", sql)
    if _BAD_IN_SCRIPT.search(body):
        raise UnsafeStatement("scoring script may only create TEMP tables and select")
    for m in re.finditer(r"CREATE\s+(?:OR\s+REPLACE\s+)?(\w+(?:\s+\w+)?)\s+TABLE", body, re.I):
        if "TEMP" not in m.group(1).upper():
            raise UnsafeStatement("scoring script creates a permanent table")
    if re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE", body, re.I):
        raise UnsafeStatement("scoring script creates a permanent table")


class StreamStore(Protocol):
    def merge_accounts(self, rows: list[dict]) -> int: ...
    def merge_trades(self, rows: list[dict]) -> int: ...
    def merge_alerts(self, rows: list[dict]) -> int: ...
    def write_events(self, rows: list[dict]) -> None: ...
    def score_features(self, script: str) -> list[dict]: ...
    def read_accounts(self) -> list[dict]: ...
    def read_trades(self) -> list[dict]: ...
    def latest_alerts(self) -> list[dict]: ...
    def read_events(self, run_id: str, limit: int) -> list[dict]: ...


_ACCOUNT_COLS = ("account_id", "created_ts", "kyc_tier", "device_id", "funding_source_id")
_TRADE_COLS = ("trade_id", "ts", "market_id", "account_id", "side", "outcome", "price", "size_usd")


def merge_accounts_sql(project: str) -> str:
    return f"""MERGE `{project}.{C.T_ACCOUNTS}` T
USING (
  SELECT JSON_VALUE(r, '$.account_id') AS account_id,
         TIMESTAMP(JSON_VALUE(r, '$.created_ts')) AS created_ts,
         CAST(JSON_VALUE(r, '$.kyc_tier') AS INT64) AS kyc_tier,
         JSON_VALUE(r, '$.device_id') AS device_id,
         JSON_VALUE(r, '$.funding_source_id') AS funding_source_id,
         JSON_VALUE(r, '$.run_id') AS run_id
  FROM UNNEST(JSON_EXTRACT_ARRAY(@rows)) AS r
  WHERE TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY JSON_VALUE(r, '$.account_id')
                             ORDER BY JSON_VALUE(r, '$.created_ts')) = 1
) S
ON T.account_id = S.account_id
WHEN NOT MATCHED THEN INSERT
  (account_id, created_ts, kyc_tier, device_id, funding_source_id, run_id, ingested_ts, source)
  VALUES (S.account_id, S.created_ts, S.kyc_tier, S.device_id, S.funding_source_id, S.run_id,
          CURRENT_TIMESTAMP(), 'streamed')"""


def merge_trades_sql(project: str) -> str:
    return f"""MERGE `{project}.{C.T_TRADES}` T
USING (
  SELECT JSON_VALUE(r, '$.trade_id') AS trade_id,
         TIMESTAMP(JSON_VALUE(r, '$.ts')) AS ts,
         JSON_VALUE(r, '$.market_id') AS market_id,
         JSON_VALUE(r, '$.account_id') AS account_id,
         JSON_VALUE(r, '$.side') AS side,
         JSON_VALUE(r, '$.outcome') AS outcome,
         CAST(JSON_VALUE(r, '$.price') AS FLOAT64) AS price,
         CAST(JSON_VALUE(r, '$.size_usd') AS FLOAT64) AS size_usd,
         JSON_VALUE(r, '$.run_id') AS run_id
  FROM UNNEST(JSON_EXTRACT_ARRAY(@rows)) AS r
  WHERE TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY JSON_VALUE(r, '$.trade_id')
                             ORDER BY JSON_VALUE(r, '$.ts')) = 1
) S
ON T.trade_id = S.trade_id
WHEN NOT MATCHED THEN INSERT
  (trade_id, ts, market_id, account_id, side, outcome, price, size_usd, run_id, ingested_ts, source)
  VALUES (S.trade_id, S.ts, S.market_id, S.account_id, S.side, S.outcome, S.price, S.size_usd,
          S.run_id, CURRENT_TIMESTAMP(), 'streamed')"""


def merge_alerts_sql(project: str) -> str:
    return f"""MERGE `{project}.{C.T_ALERTS}` T
USING (
  SELECT JSON_VALUE(r, '$.alert_id') AS alert_id,
         JSON_VALUE(r, '$.ring_id') AS ring_id,
         JSON_VALUE(r, '$.scenario_hint') AS scenario_hint,
         ARRAY(SELECT JSON_VALUE(x) FROM UNNEST(JSON_EXTRACT_ARRAY(r, '$.member_accounts')) x)
           AS member_accounts,
         CAST(JSON_VALUE(r, '$.n_members') AS INT64) AS n_members,
         CAST(JSON_VALUE(r, '$.score') AS FLOAT64) AS score,
         JSON_VALUE(r, '$.top_features') AS top_features,
         ARRAY(SELECT JSON_VALUE(x) FROM UNNEST(JSON_EXTRACT_ARRAY(r, '$.evidence_trade_ids')) x)
           AS evidence_trade_ids,
         ARRAY(SELECT JSON_VALUE(x) FROM UNNEST(JSON_EXTRACT_ARRAY(r, '$.evidence_edge_ids')) x)
           AS evidence_edge_ids,
         CAST(JSON_VALUE(r, '$.threshold') AS FLOAT64) AS threshold,
         JSON_VALUE(r, '$.status') AS status,
         CAST(JSON_VALUE(r, '$.n_flagged') AS INT64) AS n_flagged,
         JSON_VALUE(r, '$.run_id') AS run_id,
         JSON_VALUE(r, '$.member_scores') AS member_scores
  FROM UNNEST(JSON_EXTRACT_ARRAY(@rows)) AS r
) S
ON T.alert_id = S.alert_id
WHEN MATCHED THEN UPDATE SET
  ring_id = S.ring_id, scenario_hint = S.scenario_hint, member_accounts = S.member_accounts,
  n_members = S.n_members, score = S.score, top_features = S.top_features,
  evidence_trade_ids = S.evidence_trade_ids, evidence_edge_ids = S.evidence_edge_ids,
  threshold = S.threshold, status = S.status, n_flagged = S.n_flagged, source = 'streamed',
  run_id = S.run_id, created_ts = CURRENT_TIMESTAMP(), member_scores = S.member_scores
WHEN NOT MATCHED THEN INSERT
  (alert_id, ring_id, scenario_hint, member_accounts, n_members, score, top_features,
   evidence_trade_ids, evidence_edge_ids, threshold, status, n_flagged, source, run_id,
   created_ts, member_scores)
  VALUES (S.alert_id, S.ring_id, S.scenario_hint, S.member_accounts, S.n_members, S.score,
          S.top_features, S.evidence_trade_ids, S.evidence_edge_ids, S.threshold, S.status,
          S.n_flagged, 'streamed', S.run_id, CURRENT_TIMESTAMP(), S.member_scores)"""


class BigQueryStreamStore:
    """BigQuery implementation. ``jobs`` counts every query job (dry runs excluded)."""

    def __init__(self, project: str, client=None):
        self.project, self._client = project, client
        self._lock = threading.Lock()
        self.jobs = 0
        self.bytes_billed = 0

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                from google.cloud import bigquery

                self._client = bigquery.Client(project=self.project, location=LOCATION)
            return self._client

    # ---- jobs ----

    def _run(self, sql: str, kind: str, params: dict | None = None):
        """Guard, dry run, then the capped job. Returns the finished query job."""
        from google.cloud import bigquery

        if kind == "read":
            assert_read_only(sql)
        elif kind == "merge":
            assert_stream_merge(sql)
        elif kind == "score":
            assert_scoring_script(sql)
        else:
            raise UnsafeStatement(f"unknown kind {kind}")
        cap = C.SCORE_MAX_BYTES_BILLED if kind == "score" else C.MAX_BYTES_BILLED
        qp = [bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in (params or {}).items()]
        dry_cfg = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            query_parameters=qp,
            maximum_bytes_billed=cap,
        )
        try:
            est = self.client.query(sql, job_config=dry_cfg, location=LOCATION)
            if est.total_bytes_processed and est.total_bytes_processed > cap:
                raise UnsafeStatement(
                    f"dry run says {est.total_bytes_processed} bytes, over the cap"
                )
        except UnsafeStatement:
            raise
        except Exception as e:  # noqa: BLE001
            # A script whose later statements read its own TEMP tables cannot be validated by a
            # dry run; the real run below still has the byte cap.
            if kind != "score":
                raise
            print(
                f"stream: scoring dry run not validated ({type(e).__name__}); capped run",
                flush=True,
            )
        cfg = bigquery.QueryJobConfig(
            query_parameters=qp,
            maximum_bytes_billed=cap,
            job_timeout_ms=int(C.JOB_TIMEOUT_S * 1000),
        )
        job = self.client.query(sql, job_config=cfg, location=LOCATION)
        job.result(timeout=C.JOB_TIMEOUT_S)
        self.jobs += 1
        self.bytes_billed += int(job.total_bytes_billed or 0)
        return job

    def _merge(self, sql: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        job = self._run(sql, "merge", {"rows": json.dumps(rows)})
        return int(job.num_dml_affected_rows or 0)

    # ---- writes ----

    def merge_accounts(self, rows: list[dict]) -> int:
        return self._merge(merge_accounts_sql(self.project), rows)

    def merge_trades(self, rows: list[dict]) -> int:
        return self._merge(merge_trades_sql(self.project), rows)

    def merge_alerts(self, rows: list[dict]) -> int:
        return self._merge(merge_alerts_sql(self.project), rows)

    def write_events(self, rows: list[dict]) -> None:
        """Append-only streaming insert (no query, no slots) into audit.stream_events."""
        for i in range(0, len(rows), 400):
            errs = self.client.insert_rows_json(f"{self.project}.{C.T_EVENTS}", rows[i : i + 400])
            if errs:
                raise RuntimeError(f"stream_events insert failed: {str(errs)[:200]}")

    # ---- reads (list_rows: no query, no slots) ----

    def _list(self, table: str) -> list[dict]:
        return [dict(r.items()) for r in self.client.list_rows(f"{self.project}.{table}")]

    def read_accounts(self) -> list[dict]:
        return self._list(C.T_ACCOUNTS)

    def read_trades(self) -> list[dict]:
        return self._list(C.T_TRADES)

    def latest_alerts(self) -> list[dict]:
        rows = self._list(C.T_ALERTS)
        if not rows:
            return []
        newest = max(str(r["created_ts"]) for r in rows)
        run = next(r["run_id"] for r in rows if str(r["created_ts"]) == newest)
        return [r for r in rows if r["run_id"] == run]

    def read_events(self, run_id: str, limit: int = 100) -> list[dict]:
        rows = [r for r in self._list(C.T_EVENTS) if r["run_id"] == run_id]
        return sorted(rows, key=lambda r: str(r["ts"]))[-limit:]

    # ---- scoring ----

    def score_features(self, script: str) -> list[dict]:
        job = self._run(script, "score")
        return [dict(r.items()) for r in job.result(timeout=C.JOB_TIMEOUT_S)]


class MemoryStore:
    """Same semantics in memory: a MERGE inserts a row only when its natural key is new."""

    def __init__(self, scorer=None):
        self.accounts: dict[str, dict] = {}
        self.trades: dict[str, dict] = {}
        self.alerts: dict[str, dict] = {}
        self.events: list[dict] = []
        self.merge_calls = 0
        self.score_calls = 0
        self.scorer = scorer  # callable(accounts, trades) -> feature rows
        self.fail_next_merge = 0

    def _merge(self, table: dict, key: str, rows: list[dict]) -> int:
        self.merge_calls += 1
        if self.fail_next_merge:
            self.fail_next_merge -= 1
            raise RuntimeError("simulated BigQuery failure")
        n = 0
        for r in rows:
            if r[key] not in table:
                table[r[key]] = dict(r)
                n += 1
        return n

    def merge_accounts(self, rows):
        return self._merge(self.accounts, "account_id", rows)

    def merge_trades(self, rows):
        return self._merge(self.trades, "trade_id", rows)

    def merge_alerts(self, rows):
        for r in rows:
            self.alerts[r["alert_id"]] = dict(r)
        return len(rows)

    def write_events(self, rows):
        self.events.extend(rows)

    def read_accounts(self):
        return list(self.accounts.values())

    def read_trades(self):
        return list(self.trades.values())

    def latest_alerts(self):
        return list(self.alerts.values())

    def read_events(self, run_id, limit=100):
        return [e for e in self.events if e["run_id"] == run_id][-limit:]

    def score_features(self, script: str) -> list[dict]:
        self.score_calls += 1
        if self.scorer is None:
            return []
        accounts, trades = list(self.accounts.values()), list(self.trades.values())
        m = re.search(r"ENDS_WITH\(account_id, '-([0-9a-f]{8})'\)", script)
        if m:  # the run-scoped script reads only that run's accounts and trades
            accounts, trades = (
                [r for r in rows if r["account_id"].endswith(f"-{m.group(1)}")]
                for rows in (accounts, trades)
            )
        return self.scorer(accounts, trades)


def now_s() -> float:
    return time.monotonic()
