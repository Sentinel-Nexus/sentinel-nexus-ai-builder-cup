"""Stream path with fakes: schema, consumer, retries and DLQ, idempotency, limits, flag, scoring."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.app import workbench
from services.app.main import create_app
from services.stream import bq as B
from services.stream import scoring as S
from services.stream import service as svc
from services.stream.limits import StreamLimiter
from services.stream.schema import Malformed, parse_message
from tests.unit.agent_fakes import FakeBackend, good_findings, make_deps
from tests.unit.stream_fakes import (
    account,
    features,
    make_controller,
    msg,
    trade,
)

ROOT = Path(__file__).resolve().parents[2]


def wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------- schema ----------


def test_valid_messages_parse():
    ev = parse_message(*_raw(trade()))
    assert ev.ident == ("trade", "ST000001") and ev.row["price"] == 0.5
    ev = parse_message(*_raw(account()))
    assert ev.ident == ("account_registered", "SW01") and ev.row["kyc_tier"] == 1


def _raw(body, run_id="r-1"):
    import json

    return json.dumps(body).encode(), {"event_type": body["event_type"], "run_id": run_id}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(price="x"),
        lambda d: d.update(price=0),
        lambda d: d.update(price=1.5),
        lambda d: d.update(side="HOLD"),
        lambda d: d.update(size_usd=-1),
        lambda d: d.update(ts="yesterday"),
        lambda d: d.update(ts="2026-10-01T10:00:00"),  # no timezone
        lambda d: d.update(extra=1),
        lambda d: d.pop("market_id"),
        lambda d: d.update(account_id="bad id!"),
        lambda d: d.update(size_usd=float("inf")),
        lambda d: d.update(price=True),
    ],
)
def test_malformed_trades_are_rejected(mutate):
    d = trade()
    mutate(d)
    import json

    with pytest.raises(Malformed):
        parse_message(json.dumps(d).encode(), {"event_type": "trade", "run_id": "r-1"})


def test_attributes_and_body_must_agree():
    data, _ = _raw(trade())
    for attrs in ({}, {"event_type": "trade"}, {"event_type": "account_registered", "run_id": "r"}):
        with pytest.raises(Malformed):
            parse_message(data, attrs)
    for bad in (b"", b"not json", b"[1]", b"{}", b"x" * 5000):
        with pytest.raises(Malformed):
            parse_message(bad, {"event_type": "trade", "run_id": "r-1"})


# ---------- consumer ----------


def consume(ctl, *msgs):
    for m in msgs:
        ctl.consumer.on_message(m)
    batch = ctl.consumer.collect(0.1)
    ctl.consumer.process_batch(batch)
    return batch


def test_valid_batch_is_merged_acked_and_counted():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("replay", 3)
    ms = [msg(account(), run.run_id, "a"), msg(trade("T1"), run.run_id, "b"),
          msg(trade("T2"), run.run_id, "c")]  # fmt: skip
    consume(ctl, *ms)
    assert [m.acked for m in ms] == [1, 1, 1] and not any(m.nacked for m in ms)
    assert (run.ingested, run.duplicates_dropped, run.processed) == (3, 0, 3)
    assert set(store.trades) == {"T1", "T2"} and set(store.accounts) == {"SW01"}
    assert store.merge_calls == 2  # one MERGE per table


def test_duplicate_in_memory_and_in_the_same_batch():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("replay", 99)
    consume(ctl, msg(trade("T1"), run.run_id, "a"))
    d1, d2 = msg(trade("T1"), run.run_id, "b"), msg(trade("T1"), run.run_id, "c")
    consume(ctl, d1, d2)
    assert run.duplicates_dropped == 2 and run.ingested == 1
    assert d1.acked == d2.acked == 1
    types = [e["event_type"] for e in ctl.runtime.recent_events(run.run_id, 50)]
    assert types.count("duplicate_dropped") == 2


def test_duplicate_after_restart_is_caught_by_the_merge():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("replay", 99)
    consume(ctl, msg(trade("T1"), run.run_id, "a"), msg(trade("T2"), run.run_id, "b"))
    ctl.consumer._seen.clear()  # a new instance has an empty set; BigQuery already has the rows
    consume(ctl, msg(trade("T1"), run.run_id, "c"), msg(trade("T3"), run.run_id, "d"))
    assert len(store.trades) == 3
    assert run.ingested == 3 and run.duplicates_dropped == 1


def test_out_of_order_events_are_ingested_all_the_same():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("replay", 3)
    consume(ctl, msg(trade("T2", ts="2026-10-02T10:00:00Z"), run.run_id, "b"),
            msg(trade("T1"), run.run_id, "c"), msg(account(), run.run_id, "a"))  # fmt: skip
    assert set(store.trades) == {"T1", "T2"} and "SW01" in store.accounts and run.ingested == 3


def test_malformed_message_is_nacked_recorded_and_never_written():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("poison", 1)
    bad = msg(b'{"event_type":"trade","price":"x"}', run.run_id, "p1", event_type="trade")
    ctl.consumer.on_message(bad)
    assert bad.nacked == 1 and bad.acked == 0 and not store.trades
    assert (run.malformed, run.retried, run.dead_letter) == (1, 1, 0)


def test_retry_exhaustion_ends_as_a_dead_letter_candidate():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("poison", 1)
    for attempt in range(1, 6):
        ctl.consumer.on_message(
            msg(b"garbage", run.run_id, "p1", attempt=attempt, event_type="trade")
        )
    types = [e["event_type"] for e in ctl.runtime.recent_events(run.run_id, 50)]
    assert types.count("malformed") == 1 and types.count("retry") == 4
    assert types.count("dead_letter_candidate") == 1
    assert run.malformed == 1 and run.retried == 4 and run.dead_letter == 1
    assert run.state == "finished" and not store.trades


def test_a_bigquery_failure_nacks_and_a_redelivery_succeeds():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("replay", 99)
    store.fail_next_merge = 1
    m = msg(trade("T1"), run.run_id, "a")
    consume(ctl, m)
    assert m.nacked == 1 and m.acked == 0 and not store.trades and run.retried == 1
    m2 = msg(trade("T1"), run.run_id, "a", attempt=2)
    consume(ctl, m2)
    assert m2.acked == 1 and set(store.trades) == {"T1"}


def test_scoring_waits_for_the_whole_burst_then_runs_once():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("replay", 3)
    consume(ctl, msg(account("SW01"), run.run_id, "a"), msg(trade("T1"), run.run_id, "b"))
    assert store.score_calls == 0
    consume(ctl, msg(account("SW02"), run.run_id, "c"))
    assert wait(lambda: run.state == "finished")
    assert store.score_calls == 1 and run.scoring_queries == 1


def test_idempotent_scoring_gives_one_alert_across_replays():
    ctl, store, _ = make_controller()
    ids = []
    for n in range(2):
        run = ctl.runtime.start_run("replay", 7)
        ms = [msg(account(f"SW0{i}"), run.run_id, f"a{n}{i}") for i in range(1, 7)]
        ms.append(msg(account("SB01", device_id="SDB01"), run.run_id, f"b{n}"))
        consume(ctl, *ms)
        assert wait(lambda r=run: r.state == "finished")
        ids.append(run.alert_ids)
    assert ids[0] == ids[1] and len(ids[0]) == 1  # same ring, same alert id
    assert len(store.alerts) == 1 and len(store.accounts) == 7
    a = next(iter(store.alerts.values()))
    assert a["source"] == "streamed" and a["run_id"] == run.run_id
    assert set(a["member_accounts"]) == {f"SW0{i}" for i in range(1, 7)}  # SB01 is not flagged
    assert run.duplicates_dropped == 7 and run.ingested == 0  # the second replay added no rows


def test_at_most_six_scoring_queries_per_run():
    ctl, store, _ = make_controller()
    run = ctl.runtime.start_run("replay", 1)
    consume(ctl, msg(account(), run.run_id, "a"))
    for _ in range(10):
        wait(lambda: not run.scoring_active, 3)
        ctl.consumer.request_scoring(run.run_id)
        wait(lambda: not run.scoring_active, 3)
    assert run.scoring_queries == 6 and store.score_calls == 6


# ---------- rate limits ----------


def test_limits_per_client_hour_and_day():
    t = [1000.0]
    lim = StreamLimiter(1, 3, 4, clock=lambda: t[0])
    assert lim.check("a")[0]
    ok, retry, scope = lim.check("a")
    assert not ok and scope == "client" and 0 < retry <= 601
    t[0] += 601
    assert lim.check("a")[0]
    t[0] += 601
    assert lim.check("a")[0]
    ok, _, scope = lim.check("b")  # fourth hit this hour: the service-wide hourly limit
    assert not ok and scope == "hour"
    t[0] += 3601
    assert lim.check("b")[0]
    ok, _, scope = lim.check("c")  # fifth hit inside a day
    assert not ok and scope == "day"
    assert StreamLimiter(0, 1, 1).check("x")[2] == "disabled"


# ---------- fresh bursts and the same-burst replay ----------


def run_and_wait(c, path="/stream/replay"):
    r = c.post(path)
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    assert wait(
        lambda: c.get("/stream/status", params={"run_id": rid}).json()["state"] == "finished"
    )
    return c.get("/stream/status", params={"run_id": rid}).json()


def test_namespace_suffixes_every_id_and_nothing_else():
    msgs = svc.load_burst()
    a, b = svc.namespace(msgs, "aaaaaaaa"), svc.namespace(msgs, "bbbbbbbb")
    assert a == svc.namespace(msgs, "aaaaaaaa")  # deterministic: the same suffix, the same bodies
    keys = svc.NAMESPACED
    ids = lambda burst: {m[k] for m in burst for k in keys if k in m}  # noqa: E731
    assert ids(a).isdisjoint(ids(b)) and len(ids(a)) == len(ids(msgs))
    assert all(m[k].endswith("-aaaaaaaa") for m in a for k in keys if k in m)
    for m0, m1 in zip(msgs, a, strict=True):  # market, side, price, size and time are untouched
        assert {k: v for k, v in m0.items() if k not in keys} == {
            k: v for k, v in m1.items() if k not in keys
        }
    for m in a:
        parse_message(
            __import__("json").dumps(m).encode(), {"event_type": m["event_type"], "run_id": "r"}
        )


def test_two_fresh_runs_each_ingest_everything_and_do_not_collide(monkeypatch):
    c, holder, _ = app_client(monkeypatch, True, per_client_10min=5, per_hour=50, per_day=50)
    with c:
        first = run_and_wait(c)
        second = run_and_wait(c)
        for st in (first, second):
            k = st["counters"]
            assert st["mode"] == "fresh" and k["ingested"] == 17 and k["duplicates_dropped"] == 0
            assert len(st["alert_ids"]) == 1
        assert first["alert_ids"] != second["alert_ids"]
        store = holder["store"]
        assert len(store.accounts) == 14 and len(store.trades) == 20  # 7 + 7 accounts, 10 + 10
        # the newest run's alert replaces the previous one in the queue
        streamed = [a for a in c.get("/queue").json() if a.get("streamed")]
        assert [a["alert_id"] for a in streamed] == second["alert_ids"]
        # no device or funding source is shared across runs
        by_run = {}
        for a in store.accounts.values():
            by_run.setdefault(a["run_id"], set()).update((a["device_id"], a["funding_source_id"]))
        sets = list(by_run.values())
        assert len(sets) == 2 and sets[0].isdisjoint(sets[1])
        # and each run was scored over its own accounts only
        assert holder["store"].score_calls == 2


def test_same_burst_adds_no_rows_and_raises_the_same_alert(monkeypatch):
    c, holder, _ = app_client(monkeypatch, True, per_client_10min=5, per_hour=50, per_day=50)
    with c:
        assert c.get("/stream/config").json()["can_replay_same"] is False
        assert c.post("/stream/replay", params={"same": "true"}).status_code == 409
        fresh = run_and_wait(c)
        assert c.get("/stream/config").json()["can_replay_same"] is True
        store = holder["store"]
        rows = (len(store.accounts), len(store.trades), len(store.alerts))
        same = run_and_wait(c, "/stream/replay?same=true")
        k = same["counters"]
        assert same["mode"] == "same" and k["ingested"] == 0 and k["duplicates_dropped"] == 17
        assert (len(store.accounts), len(store.trades), len(store.alerts)) == rows
        assert same["alert_ids"] == fresh["alert_ids"]
        q = [a["alert_id"] for a in c.get("/queue").json() if a.get("streamed")]
        assert q == fresh["alert_ids"]


def test_same_burst_has_its_own_rate_limit_counter(monkeypatch):
    c, _, _ = app_client(monkeypatch, True)  # 1 per client per 10 minutes
    with c:
        run_and_wait(c)
        assert c.post("/stream/replay").status_code == 429  # a second fresh burst: limited
        assert run_and_wait(c, "/stream/replay?same=true")["mode"] == "same"
        r = c.post("/stream/replay", params={"same": "true"})
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_the_scoring_script_is_scoped_to_the_run():
    script = S.build_script("proj-123456", "0123abcd")
    B.assert_scoring_script(script)
    assert "ENDS_WITH(account_id, '-0123abcd')" in script
    assert "`run_trades`" in script and "`run_accounts`" in script
    assert "`proj-123456.risk_raw.stream_trades`" not in script.split("det_features")[-1]
    with pytest.raises(ValueError):
        S.build_script("proj-123456", "x'); DROP TABLE t; --")
    assert S.suffix_of("SW01-0123abcd") == "0123abcd" and S.suffix_of("SW01") is None
    a, _ = S.alerts_from_features(
        features([account("SW01-0123abcd")], []), "r-1", suffix="0123abcd"
    )
    b, _ = S.alerts_from_features(
        features([account("SW01-4567abcd")], []), "r-2", suffix="4567abcd"
    )
    assert a == [] or a[0]["alert_id"] != b[0]["alert_id"]


# ---------- app: flag off, flag on ----------


def app_client(monkeypatch, enabled: bool, **limits):
    monkeypatch.setenv("STREAMING_ENABLED", "1" if enabled else "")
    ctl_holder = {}

    def factory(settings):
        ctl, store, pub = make_controller(enabled=True, burst=_burst(), **limits)
        ctl_holder.update(ctl=ctl, store=store, pub=pub)
        return ctl

    monkeypatch.setattr(svc, "FACTORY", factory)
    deps, _ = make_deps(FakeBackend(good_findings(), second=good_findings()))
    return TestClient(create_app(deps)), ctl_holder, deps


def _burst():
    rows = [account(f"SW0{i}") for i in range(1, 7)]
    rows += [account("SB01", device_id="SDB01", funding_source_id="SFB01")]
    rows += [trade(f"T{i}", f"SW0{i % 6 + 1}") for i in range(10)]
    return rows


def test_flag_off_has_no_stream_routes_and_the_queue_is_unchanged(monkeypatch):
    c, holder, _ = app_client(monkeypatch, False)
    with c:
        assert c.get("/stream/config").json()["enabled"] is False
        assert c.post("/stream/replay").status_code == 404
        assert c.post("/stream/poison").status_code == 404
        assert c.get("/stream/status").status_code == 404
        q = c.get("/queue").json()
        assert not any("streamed" in a for a in q) and {a["alert_id"] for a in q} == {
            "AL_W",
            "AL_C",
        }
        names = {g["name"] for g in c.get("/governance").json()["guards"]}
        assert not any(n.startswith("Stream") for n in names)
        assert "ctl" not in holder


def test_replay_end_to_end_with_loopback(monkeypatch):
    c, holder, deps = app_client(monkeypatch, True)
    with c:
        before = c.get("/summary").json()
        r = c.post("/stream/replay")
        assert r.status_code == 200 and r.json()["total"] == 17
        run_id = r.json()["run_id"]
        assert wait(
            lambda: c.get("/stream/status", params={"run_id": run_id}).json()["state"] == "finished"
        )
        s = c.get("/stream/status", params={"run_id": run_id}).json()
        k = s["counters"]
        assert k["published"] == 17 and k["ingested"] == 17 and k["alerts_raised"] == 1
        assert s["progress"] == 1.0 and s["timings"]["last_publish_to_alert_s"] is not None
        q = c.get("/queue").json()
        streamed = [a for a in q if a.get("streamed")]
        assert len(streamed) == 1 and streamed[0]["n_members"] == 6
        assert all("streamed" not in a for a in q if not a.get("streamed"))
        # detector counts and KPIs do not move
        after = c.get("/summary").json()
        assert after["alerts"] == before["alerts"] and after["detector"] == before["detector"]
        # case detail and graph work through the existing endpoints
        aid = streamed[0]["alert_id"]
        d = c.get(f"/cases/{aid}/detail").json()
        assert d["alert"]["streamed"] is True and d["cached"] is False
        g = c.get(f"/cases/{aid}/graph")
        assert g.status_code == 200 and g.json()["engine"] == "fallback"
        sfx = holder["ctl"].last_suffix
        assert {n["key"] for n in g.json()["nodes"] if n["type"] == "Account"} >= {
            f"SW01-{sfx}",
            f"SW06-{sfx}",
        }
        t = c.get(f"/cases/{aid}/trades", params={"account": f"SW01-{sfx}"})
        assert t.status_code == 200 and t.json()["score"]["flagged"] is True
        # a newer run replaces the streamed alerts while it is running
        assert any(
            n.startswith("Stream")
            for n in (g["name"] for g in c.get("/governance").json()["guards"])
        )


def test_replay_rate_limit_answers_429_with_a_clear_message(monkeypatch):
    c, _, _ = app_client(monkeypatch, True)
    with c:
        assert c.post("/stream/replay").status_code == 200
        wait(lambda: not holder_active(c))
        r = c.post("/stream/replay")
        assert r.status_code == 429 and "10 minutes" in r.json()["detail"]
        assert int(r.headers["Retry-After"]) > 0


def holder_active(c) -> bool:
    return c.get("/stream/status").json()["active"]


def test_poison_message_ends_as_a_dead_letter_candidate(monkeypatch):
    c, holder, _ = app_client(monkeypatch, True)
    with c:
        run_id = c.post("/stream/poison").json()["run_id"]
        pub = holder["pub"]
        assert wait(lambda: len(pub.sent) == 1)
        m = pub.sent[0]
        for attempt in range(2, 6):  # Pub/Sub redelivers a nacked message with a higher attempt
            m.delivery_attempt = attempt
            holder["ctl"].consumer.on_message(m)
        s = c.get("/stream/status", params={"run_id": run_id}).json()
        assert s["counters"]["malformed"] == 1 and s["counters"]["retried"] == 4
        assert s["counters"]["dead_letter"] == 1 and s["state"] == "finished"
        assert holder["store"].trades == {}


def test_a_second_run_cannot_start_while_one_is_active(monkeypatch):
    c, holder, _ = app_client(monkeypatch, True, per_client_10min=5, per_hour=50, per_day=50)
    with c:
        holder["ctl"].runtime.start_run("replay", 5)  # active: nothing has been processed
        assert c.post("/stream/replay").status_code == 409


# ---------- scoring SQL ----------


def test_the_script_only_rewrites_output_tables_to_temp_tables():
    script = S.build_script("proj-123456")
    assert "CREATE OR REPLACE TABLE" not in script and "_stream_scope" not in script
    assert script.count("CREATE OR REPLACE TEMP TABLE") >= 8
    B.assert_scoring_script(script)
    assert "proj-123456.risk_raw.stream_trades" in script
    assert "proj-123456.risk_raw.stream_accounts" in script
    assert "proj-123456.risk_curated.markets" in script
    # undoing the two mechanical edits gives the frozen SQL back, file by file
    for name in S.STEPS:
        raw = (S.VIEWS / name).read_text(encoding="utf-8")
        frozen = S.render_step(raw, "proj-123456", S.target("proj-123456"))
        assert S.temp_table_script(frozen).replace("TEMP TABLE", "TABLE").replace(
            "`det_", f"`{S.SCOPE}.det_"
        ) == frozen.replace("`_stream_scope.det_", f"`{S.SCOPE}.det_")


def test_scoring_uses_the_frozen_threshold_and_weights():
    cfg = S.frozen()
    assert cfg["threshold"] == 43.0 and cfg["iteration"] == 1
    from services.detector import scoring as D

    assert sum(f.weight for f in D.FEATURES) == pytest.approx(1.0)
    accounts = [account(f"SW0{i}") for i in range(1, 7)] + [account("SB01")]
    alerts, scores = S.alerts_from_features(features(accounts, []), "r-9")
    assert len(alerts) == 1 and alerts[0]["status"] == "flagged"
    assert scores["SW01"]["flagged"] and not scores["SB01"]["flagged"]
    again, _ = S.alerts_from_features(features(accounts, []), "r-10")
    assert alerts[0]["alert_id"] == again[0]["alert_id"]  # stable across replays


@pytest.mark.parametrize(
    "sql",
    [
        "MERGE `p.risk_raw.trades` T USING x ON TRUE WHEN MATCHED THEN DELETE",
        "MERGE `p.risk_curated.alerts` T USING x ON TRUE WHEN MATCHED THEN DELETE",
        "MERGE `p.risk_raw.stream_trades` T USING x ON TRUE WHEN MATCHED THEN DELETE; DROP TABLE x",
    ],
)
def test_merge_guard_allows_only_the_four_stream_tables(sql):
    with pytest.raises(B.UnsafeStatement):
        B.assert_stream_merge(sql)
    B.assert_stream_merge(B.merge_trades_sql("proj-123456"))
    B.assert_stream_merge(B.merge_accounts_sql("proj-123456"))
    B.assert_stream_merge(B.merge_alerts_sql("proj-123456"))


def test_scoring_guard_rejects_writes_and_permanent_tables():
    for bad in (
        "CREATE TABLE `p.d.t` AS SELECT 1",
        "CREATE OR REPLACE TABLE `p.d.t` AS SELECT 1",
        "CREATE TEMP TABLE a AS SELECT 1; DROP TABLE `p.d.t`",
        "INSERT INTO `p.d.t` VALUES (1)",
    ):
        with pytest.raises(B.UnsafeStatement):
            B.assert_scoring_script(bad)


# ---------- the detector never reads labels ----------


def test_stream_code_never_reads_the_ground_truth_file():
    pat = re.compile(r"_truth|ground_truth|decoy_groups|do_not_flag|innocent_household", re.I)
    for folder in ("services/stream", "services/detector", "sql/views"):
        for p in (ROOT / folder).rglob("*"):
            if p.suffix in (".py", ".sql") and p.name != "evaluate.py":
                assert not pat.search(p.read_text(encoding="utf-8")), p
    burst = (ROOT / "data" / "stream" / "burst_01.jsonl").read_text(encoding="utf-8")
    assert not pat.search(burst) and "wash" not in burst.lower()


def test_burst_file_shape_and_validity():
    msgs = svc.load_burst()
    assert len(msgs) == 310 and [m["ts"] for m in msgs] == sorted(m["ts"] for m in msgs)
    kinds = {m["event_type"] for m in msgs}
    assert kinds == {"account_registered", "trade"}
    for m in msgs:
        parse_message(
            __import__("json").dumps(m).encode(), {"event_type": m["event_type"], "run_id": "r"}
        )
    ids = [m.get("trade_id") or m["account_id"] for m in msgs]
    assert len(ids) == len(set(ids))
    assert workbench.client_id  # imported for the app tests above


def test_config_answers_before_anything_was_measured(monkeypatch):
    """stream.json holds null until stream_eval measured a run: /stream/config must still be 200."""
    c, _, _ = app_client(monkeypatch, True)
    with c:
        body = c.get("/stream/config")
        assert body.status_code == 200 and body.json()["enabled"] is True
        assert svc._measured_seconds() is None or svc._measured_seconds() > 0
