"""Read-only guard, cache, and the runner's byte cap and timeout (no BigQuery)."""

from __future__ import annotations

import concurrent.futures
import time
from unittest.mock import MagicMock

import pytest

from services.graph.cache import TTLCache
from services.graph.guard import UnsafeQueryError, assert_read_only
from services.graph.runner import MAX_BYTES_BILLED, BigQueryRunner, GraphTimeout

REJECTED = [
    "INSERT INTO t VALUES (1)",
    "insert into t select 1",
    "UPDATE t SET a = 1 WHERE true",
    "DELETE FROM t WHERE true",
    "MERGE t USING s ON t.a = s.a WHEN MATCHED THEN DELETE",
    "DROP TABLE t",
    "CREATE TABLE t AS SELECT 1",
    "CREATE OR REPLACE TABLE t AS SELECT 1",
    "ALTER TABLE t ADD COLUMN c INT64",
    "TRUNCATE TABLE t",
    "GRANT `roles/viewer` ON SCHEMA s TO 'user:a@b.c'",
    "CALL proc()",
    "EXPORT DATA OPTIONS(uri='gs://b/*') AS SELECT 1",
    "DECLARE x INT64",
    "BEGIN SELECT 1; END",
    "SELECT 1; DROP TABLE t",
    "SELECT 1; SELECT 2",
    "WITH x AS (SELECT 1) DELETE FROM t WHERE true",
    "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
    "/* SELECT */ DELETE FROM t WHERE true",
    "-- SELECT\nDROP TABLE t",
    "SELECT 1 /* unterminated",
    "LOAD DATA INTO t FROM FILES (format='CSV', uris=['gs://b/f'])",
    "",
    "   ",
]
ALLOWED = [
    "SELECT 1",
    "select * from `p.ds.t` where a = 1;",
    "-- q: tag\nSELECT account_id FROM `p.risk_graph.uses_device`",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "GRAPH `p.risk_graph.market_graph` MATCH (a:Account)-[:UsesDevice]->(d:Device) "
    "RETURN a.account_id, d.device_id",
    "SELECT 'DROP TABLE t; DELETE FROM t' AS note",
    "SELECT created_ts, update_count, setting FROM `p.ds.insert_log`",
]


@pytest.mark.parametrize("sql", REJECTED)
def test_guard_rejects_everything_but_reads(sql):
    with pytest.raises(UnsafeQueryError):
        assert_read_only(sql)


@pytest.mark.parametrize("sql", ALLOWED)
def test_guard_allows_select_and_graph(sql):
    assert_read_only(sql)


def test_runner_rejects_writes_before_any_job_is_created():
    client = MagicMock()
    runner = BigQueryRunner("p", client=client)
    for sql in ("DROP TABLE t", "UPDATE t SET a = 1 WHERE true", "SELECT 1; DELETE FROM t"):
        with pytest.raises(UnsafeQueryError):
            runner.query(sql)
    client.query.assert_not_called()


def test_runner_sets_byte_cap_params_and_timeout_on_every_job():
    client = MagicMock()
    client.query.return_value.result.return_value = [MagicMock(items=lambda: [("a", 1)])]
    runner = BigQueryRunner("p", client=client)
    rows = runner.query("SELECT @x", {"x": "1", "ids": ["a", "b"]}, deadline=time.monotonic() + 10)
    assert rows == [{"a": 1}]
    cfg = client.query.call_args.kwargs["job_config"]
    assert cfg.maximum_bytes_billed == MAX_BYTES_BILLED == 100 * 1024 * 1024
    assert 0 < int(cfg.job_timeout_ms) <= 10_000
    assert {p.name for p in cfg.query_parameters} == {"x", "ids"}
    assert client.query.call_args.kwargs["location"] == "US"


def test_runner_cancels_the_job_and_raises_on_timeout():
    client = MagicMock()
    client.query.return_value.result.side_effect = concurrent.futures.TimeoutError()
    runner = BigQueryRunner("p", client=client)
    with pytest.raises(GraphTimeout):
        runner.query("SELECT 1", deadline=time.monotonic() + 5)
    client.query.return_value.cancel.assert_called_once()


def test_runner_refuses_an_expired_deadline():
    client = MagicMock()
    with pytest.raises(GraphTimeout):
        BigQueryRunner("p", client=client).query("SELECT 1", deadline=time.monotonic() - 1)
    client.query.assert_not_called()


def test_cache_ttl_size_cap_and_copy_semantics():
    now = [0.0]
    cache = TTLCache(ttl=10, max_items=2, clock=lambda: now[0])
    cache.set("a", {"v": [1]})
    got = cache.get("a")
    got["v"].append(2)  # mutating a returned value must not change the cache
    assert cache.get("a") == {"v": [1]}
    cache.set("b", 1)
    cache.set("c", 1)
    assert cache.get("a") is None and len(cache) == 2  # oldest evicted
    now[0] = 11
    assert cache.get("b") is None  # expired
