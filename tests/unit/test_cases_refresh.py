"""BigQuery-backed caches re-read the table, so instances converge and early misses heal."""

from __future__ import annotations

import datetime as dt

from services.app import cases as cases_mod
from services.app import decisions as dec_mod


class Row(dict):
    pass


class Client:
    def __init__(self):
        self.tables: list[list[dict]] = [[]]
        self.reads = 0

    def list_rows(self, _name):
        self.reads += 1
        return [Row(r) for r in self.tables[-1]]


def case(alert, cid, ts="2026-10-16T10:00:00"):
    return {"case_id": cid, "alert_id": alert, "created_ts": dt.datetime.fromisoformat(ts)}


def test_a_miss_after_an_early_read_is_retried_and_finds_the_case(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cases_mod.time, "monotonic", lambda: clock[0])
    client = Client()
    store = cases_mod.BigQueryCases("p", client=client)
    assert store.get("AL_1") is None  # first read: table not settled yet
    client.tables.append([case("AL_1", "C1")])
    assert store.get("AL_1") is None  # a retry within 5 s is not allowed (no read storm)
    clock[0] += 6
    assert store.get("AL_1")["case_id"] == "C1"


def test_ttl_refresh_keeps_local_rows_not_yet_visible_in_the_table(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(cases_mod.time, "monotonic", lambda: clock[0])
    client = Client()
    client.tables = [[case("AL_1", "C1")]]
    store = cases_mod.BigQueryCases("p", client=client)
    store.latest()
    store.rows.append({"case_id": "C2", "alert_id": "AL_2", "created_ts": "2026-10-16T11:00:00Z"})
    clock[0] += 31
    latest = store.latest()
    assert set(latest) == {"AL_1", "AL_2"} and client.reads == 2


def test_decisions_made_elsewhere_appear_after_the_ttl(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(dec_mod.time, "monotonic", lambda: clock[0])
    client = Client()
    store = dec_mod.BigQueryDecisions("p", client=client)
    assert store.latest() == {}
    client.tables.append(
        [
            {
                "decision_id": "D1",
                "alert_id": "AL_1",
                "ts": dt.datetime(2026, 10, 16, 12),
                "decision": "x",
            }
        ]
    )
    assert store.latest() == {}  # still inside the TTL
    clock[0] += 31
    assert set(store.latest()) == {"AL_1"}
