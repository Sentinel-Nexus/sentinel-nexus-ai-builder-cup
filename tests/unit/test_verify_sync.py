from __future__ import annotations

from pathlib import Path

from scripts import verify_sync as v

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "sql" / "schema"


def test_parse_ddl_columns_covers_all_live_tables():
    ddl = {}
    for f in SCHEMA_DIR.glob("*.sql"):
        ddl.update(v.parse_ddl_columns(f.read_text(encoding="utf-8")))
    assert set(ddl) == {
        "risk_raw.trades",
        "risk_curated.accounts",
        "risk_curated.markets",
        "risk_curated.alerts",
        "risk_curated.account_scores",
        "eval.ground_truth",
        "eval.detector_results",
        "eval.decoy_groups",
        "eval.benchmark",
        "eval.benchmark_runs",
        "risk_curated.case_reports",
        "audit.agent_events",
        "risk_curated.case_decisions",
    }
    assert ddl["risk_raw.trades"][0] == ("trade_id", "STRING")
    assert len(ddl["eval.benchmark_runs"]) == 8
    assert [c for c, _ in ddl["audit.agent_events"]][:3] == ["event_id", "run_id", "seq"]
    assert (
        len(ddl["audit.agent_events"]) == 16
        and len(ddl["risk_curated.case_reports"]) == 20
        and len(ddl["risk_curated.case_decisions"]) == 8
    )


def test_compare_columns_normalizes_types():
    assert (
        v.compare_columns([("a", "FLOAT"), ("b", "INTEGER")], [("a", "FLOAT64"), ("b", "INT64")])
        == []
    )
    assert v.compare_columns([("a", "STRING")], [("a", "INT64")])


def test_pubsub_ok_and_drift():
    subs = {
        "trades-sub": {"ack": 60, "dlq": "trades-dlq", "attempts": 5},
        "trades-dlq-sub": {"ack": 10, "dlq": None, "attempts": None},
    }
    assert all(s == v.OK for _, s, _ in v.check_pubsub({"trades", "trades-dlq"}, subs))
    subs["trades-sub"]["attempts"] = 3
    out = v.check_pubsub({"trades"}, subs)
    assert [n for n, s, _ in out if s == v.FAIL] == [
        "pubsub topic trades-dlq",
        "pubsub subscription trades-sub",
    ]


def test_is_public():
    assert v.is_public({"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]})
    assert not v.is_public({"bindings": [{"role": "roles/run.invoker", "members": ["user:x"]}]})
    assert not v.is_public({})


def test_reservation():
    p = "proj"
    res = [
        {
            "name": "projects/proj/locations/US/reservations/gql-poc",
            "edition": "ENTERPRISE",
            "autoscale": {"maxSlots": "50"},
        }
    ]
    asg = [
        {
            "name": "projects/proj/locations/US/reservations/gql-poc/assignments/1",
            "jobType": "QUERY",
            "assignee": "projects/proj",
        }
    ]
    assert all(s == v.OK for _, s, _ in v.check_reservation(res, asg, p))
    assert [s for _, s, _ in v.check_reservation([], [], p)] == [v.FAIL, v.FAIL]
