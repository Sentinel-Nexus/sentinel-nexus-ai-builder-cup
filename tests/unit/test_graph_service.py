"""GraphService against a fake BigQuery built from the seed-42 data.

The fake answers each fixed query template (``-- q: <tag>``) from the generated data, so the
GQL engine and the SQL + NetworkX engine run their real code over the same edges.
"""

from __future__ import annotations

import re
from collections import defaultdict

import networkx as nx
import pytest

from services.graph import links
from services.graph.engines import FallbackEngine, GqlEngine
from services.graph.guard import assert_read_only
from services.graph.runner import GraphTimeout
from services.graph.service import GraphInputError, GraphService
from services.ingest.synth.generator import generate

PROJECT = "test-project-123"
VOLATILE = ("engine", "cached", "fallback_reason")


class FakeBigQuery:
    def __init__(self, data, fail_gql: Exception | None = None):
        self.calls: list[str] = []
        self.fail_gql = fail_gql
        acc = {a["account_id"]: a for a in data.accounts}
        self.device = {k: v["device_id"] for k, v in acc.items()}
        self.funding = {k: v["funding_source_id"] for k, v in acc.items()}
        self.trades = data.trades
        sides: dict[tuple, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for t in data.trades:
            key = (t["market_id"], t["outcome"], t["ts"], t["price"], t["size_usd"])
            sides[key][t["side"]].append(t["account_id"])
        self.fills = sorted(
            {(s, b) for v in sides.values() for s in v["SELL"] for b in v["BUY"] if s != b}
        )

    def _at(self, table, node):
        return [a for a, n in table.items() if n == node]

    def query(self, sql, params=None, deadline=None):
        assert_read_only(sql)  # the real runner does this for every job
        tag = re.search(r"-- q: (\w+)", sql).group(1)
        self.calls.append(tag)
        params = params or {}
        if tag.startswith("gql_") and self.fail_gql:
            raise self.fail_gql
        if tag in ("gql_UsesDevice", "gql_FundedBy"):
            table = self.device if tag == "gql_UsesDevice" else self.funding
            return [
                {"a": a, "node": table[a], "b": b}
                for a in params["frontier"]
                if a in table
                for b in self._at(table, table[a])
            ]
        if tag == "gql_TradedWith":
            f = set(params["frontier"])
            return [{"seller": s, "buyer": b} for s, b in self.fills if s in f or b in f]
        if tag == "sql_uses_device":
            return [{"account_id": a, "node": n} for a, n in self.device.items()]
        if tag == "sql_funded_by":
            return [{"account_id": a, "node": n} for a, n in self.funding.items()]
        if tag == "sql_traded_with":
            return [{"seller": s, "buyer": b} for s, b in self.fills]
        if tag in ("gql_traded_in", "sql_traded_in"):
            agg: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
            for t in self.trades:
                if t["market_id"] == params["market"] and t["account_id"] in params["accounts"]:
                    agg[t["account_id"]][0] += 1
                    agg[t["account_id"]][1] += t["size_usd"]
            return [{"account_id": a, "n_trades": n, "total_usd": u} for a, (n, u) in agg.items()]
        if tag == "sql_trades":
            rows = [
                t
                for t in self.trades
                if t["market_id"] == params["market"] and t["account_id"] in params["accounts"]
            ]
            rows.sort(key=lambda t: (t["ts"], t["trade_id"]))
            return rows[: links.MAX_TRADES + 1]
        raise AssertionError(f"unexpected query {tag}")


@pytest.fixture(scope="module")
def data():
    return generate(42)


def make(data, **kw):
    fake = FakeBigQuery(data, kw.pop("fail_gql", None))
    return GraphService(PROJECT, runner=fake, **kw), fake


def strip(resp):
    return {k: v for k, v in resp.items() if k not in VOLATILE}


def reference_rings(data):
    """Independent connected components under the P2 link rules (hub limit 25)."""
    fake = FakeBigQuery(data)
    graph = nx.Graph()
    for table in (fake.device, fake.funding):
        groups = defaultdict(list)
        for a, n in table.items():
            groups[n].append(a)
        for members in groups.values():
            if 2 <= len(members) <= links.HUB_LIMIT:
                graph.add_edges_from((members[0], m) for m in members[1:])
    graph.add_edges_from(fake.fills)
    return {min(c): sorted(c) for c in nx.connected_components(graph)}


# ---- engine field, forced fallback, automatic fallback ----


def test_every_response_has_an_engine_field(data):
    svc, _ = make(data)
    for resp in (
        svc.neighbors("C002", 1),
        svc.ring_subgraph("R_C002"),
        svc.trades_for(["W001"], "M05"),
    ):
        assert resp["engine"] == "gql"


def test_forced_fallback_never_touches_gql(data):
    svc, fake = make(data, force_fallback=True)
    ring = svc.ring_subgraph("R_W001")
    assert ring["engine"] == "fallback" and ring["fallback_reason"] == "forced"
    assert svc.neighbors("W001", 1)["engine"] == "fallback"
    assert svc.trades_for(["W001"], "M05")["engine"] == "fallback"
    assert not any(c.startswith("gql_") for c in fake.calls)


def test_env_variable_forces_fallback(data, monkeypatch):
    monkeypatch.setenv("GRAPH_FORCE_FALLBACK", "1")
    svc, _ = make(data)
    assert svc.neighbors("C002", 1)["engine"] == "fallback"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("reservation gql-poc is unavailable"),
        GraphTimeout("query exceeded 15s"),
        ValueError("Syntax error in GQL"),
    ],
)
def test_any_gql_error_falls_back_with_the_same_answer(data, error):
    good, _ = make(data)
    svc, fake = make(data, fail_gql=error)
    resp = svc.ring_subgraph("R_M001")
    assert resp["engine"] == "fallback" and type(error).__name__ in resp["fallback_reason"]
    assert strip(resp) == strip(good.ring_subgraph("R_M001"))


def test_breaker_skips_gql_after_a_failure_then_retries(data):
    now = [0.0]
    svc, fake = make(
        data,
        fail_gql=RuntimeError("down"),
        breaker_seconds=60,
        clock=lambda: now[0],
        cache=None,
    )
    svc.neighbors("C002", 1)
    gql_calls = sum(c.startswith("gql_") for c in fake.calls)
    assert gql_calls > 0
    svc.cache.clear()
    resp = svc.neighbors("C004", 1)  # breaker open: no new GQL attempt
    assert resp["engine"] == "fallback" and "breaker" in resp["fallback_reason"]
    assert sum(c.startswith("gql_") for c in fake.calls) == gql_calls
    now[0] = 61.0
    fake.fail_gql = None
    svc.cache.clear()
    assert svc.neighbors("C002", 1)["engine"] == "gql"


def test_a_slow_gql_attempt_past_its_deadline_falls_back(data):
    now = [0.0]

    class Slow(FakeBigQuery):
        def query(self, sql, params=None, deadline=None):
            now[0] += 20  # every query "takes" 20 s on the injected clock
            return super().query(sql, params, deadline)

    fake = Slow(data)
    svc = GraphService(PROJECT, runner=fake, clock=lambda: now[0])
    svc.gql = GqlEngine(PROJECT, fake)
    # the engine checks the real monotonic clock, so a pre-expired deadline is the lever here
    with pytest.raises(GraphTimeout):
        svc.gql.expand(["W001"], 3, deadline=0.0)


def test_bad_input_is_rejected_not_retried(data):
    svc, fake = make(data)
    for call in (
        lambda: svc.neighbors("A0001'; DROP TABLE t;--", 1),
        lambda: svc.neighbors("A0001", 0),
        lambda: svc.neighbors("A0001", 9),
        lambda: svc.neighbors("A0001", True),
        lambda: svc.ring_subgraph("W001"),
        lambda: svc.ring_subgraph("R_"),
        lambda: svc.trades_for([], "M05"),
        lambda: svc.trades_for(["A0001"], "M 05"),
        lambda: svc.trades_for([f"A{i:04d}" for i in range(201)], "M05"),
    ):
        with pytest.raises(GraphInputError):
            call()
    assert fake.calls == []


# ---- GQL and the fallback return the same ring ----


@pytest.fixture(scope="module")
def rings(data):
    return reference_rings(data)


def scenario_rings(data, rings):
    out = defaultdict(set)
    by_account = {a: min_id for min_id, members in rings.items() for a in members}
    for r in data.ground_truth:
        out[r["scenario_type"]].add(f"R_{by_account[r['account_id']]}")
    return {k: sorted(v) for k, v in out.items()}


def test_gql_and_fallback_return_the_same_ring_for_each_scenario(data, rings):
    gql, _ = make(data)
    fb, _ = make(data, force_fallback=True)
    per_scenario = scenario_rings(data, rings)
    assert set(per_scenario) == {"wash_trading", "coordinated_accumulation", "mule_funding"}
    truth = defaultdict(set)
    for r in data.ground_truth:
        truth[r["scenario_type"]].add(r["account_id"])
    for scenario, ring_ids in per_scenario.items():
        union: set[str] = set()
        for ring_id in ring_ids:
            a, b = gql.ring_subgraph(ring_id), fb.ring_subgraph(ring_id)
            assert (a["engine"], b["engine"]) == ("gql", "fallback")
            assert strip(a) == strip(b), f"{scenario} {ring_id}: engines disagree"
            assert a["members"] == rings[ring_id[2:]], "ring differs from the reference"
            assert a["truncated"] is False
            union |= set(a["members"])
        assert truth[scenario] <= union, f"{scenario}: attackers missing from their rings"
    # the shared-funding-and-device rings are exactly the attackers; coordination splits in two
    assert len(per_scenario["wash_trading"]) == 1
    assert len(per_scenario["mule_funding"]) == 1
    assert len(per_scenario["coordinated_accumulation"]) == 2


def test_ring_subgraph_shape_and_stable_evidence_ids(data):
    svc, _ = make(data, force_fallback=True)
    ring = svc.ring_subgraph("R_W001")
    assert ring["ring_id"] == "R_W001" and ring["members"][0] == "W001"
    ids = [e["id"] for e in ring["edges"]]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    kinds = {e["type"] for e in ring["edges"]}
    assert kinds == {"UsesDevice", "FundedBy", "TradedWith"}
    sample = next(e for e in ring["edges"] if e["type"] == "UsesDevice")
    assert re.fullmatch(r"UsesDevice:W\d{3}>DW\d", sample["id"])  # detector's evidence format
    assert sample["source"].startswith("Account:") and sample["target"].startswith("Device:")
    node_ids = {n["id"] for n in ring["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in ring["edges"])
    assert {m for m in ring["members"]} == {
        n["key"] for n in ring["nodes"] if n["type"] == "Account"
    }


def test_isolated_account_is_a_singleton_ring(data):
    svc, _ = make(data)
    ring = svc.ring_subgraph("R_A2000")
    assert ring["members"] == ["A2000"] and ring["edges"] == []


# ---- neighbors and trades ----


@pytest.mark.parametrize(("account", "hops"), [("C002", 1), ("W001", 1), ("M001", 2), ("A0100", 3)])
def test_neighbors_agree_across_engines(data, account, hops):
    gql, _ = make(data)
    fb, _ = make(data, force_fallback=True)
    a, b = gql.neighbors(account, hops), fb.neighbors(account, hops)
    assert strip(a) == strip(b)
    assert all(1 <= n["distance"] <= hops for n in a["neighbors"])


def test_neighbors_distances_are_link_hops(data):
    svc, _ = make(data)
    one = {n["account_id"] for n in svc.neighbors("A0100", 1)["neighbors"]}
    assert one == {"A0101", "A0102", "A0103", "A0104"}  # the household shares one device
    assert svc.neighbors("C002", 1)["neighbors"] == [{"account_id": "C004", "distance": 1}]


def test_trades_for_agree_across_engines_and_carry_trade_ids(data):
    gql, _ = make(data)
    fb, _ = make(data, force_fallback=True)
    args = (["W001", "W002", "A0100"], "M05")
    a, b = gql.trades_for(*args), fb.trades_for(*args)
    assert strip(a) == strip(b)
    assert a["trades"] and all(t["id"].startswith("T") for t in a["trades"])
    assert {e["account_id"] for e in a["edges"]} == {"W001", "W002"}  # A0100 never traded in M05
    assert a["edges"][0]["id"] == "TradedIn:W001>M05"
    assert [t["ts"] for t in a["trades"]] == sorted(t["ts"] for t in a["trades"])


def test_trades_for_caps_rows_and_flags_truncation(data, monkeypatch):
    monkeypatch.setattr(links, "MAX_TRADES", 100)
    svc, _ = make(data)
    wash = [r["account_id"] for r in data.ground_truth if r["scenario_type"] == "wash_trading"]
    resp = svc.trades_for(wash, "M05")  # 400 wash trades in M05
    assert len(resp["trades"]) == 100 and resp["truncated"] is True


# ---- cache ----


def test_cache_serves_repeat_calls_without_new_queries(data):
    svc, fake = make(data)
    first = svc.ring_subgraph("R_M001")
    n = len(fake.calls)
    again = svc.ring_subgraph("R_M001")
    assert again["cached"] is True and first["cached"] is False
    assert len(fake.calls) == n and strip(again) == strip(first)
    assert again["engine"] == "gql"


def test_forced_and_auto_answers_are_cached_separately(data):
    svc, _ = make(data)
    svc.neighbors("C002", 1)
    svc.force_fallback = True
    assert svc.neighbors("C002", 1)["engine"] == "fallback"


# ---- hub rule ----


def test_hub_nodes_create_no_links():
    rows = [("UsesDevice", f"A{i}", "HUB") for i in range(links.HUB_LIMIT + 1)]
    rows += [("UsesDevice", "A0", "SOLO")] + [("FundedBy", f"B{i}", "F") for i in range(3)]
    edges, reached = links.hop_edges(rows, [])
    assert reached == {"B0", "B1", "B2"}
    assert {e["type"] for e in edges.values()} == {"FundedBy"}


def test_engines_report_their_names(data):
    fake = FakeBigQuery(data)
    assert GqlEngine(PROJECT, fake).name == "gql"
    assert FallbackEngine(PROJECT, fake).name == "fallback"
