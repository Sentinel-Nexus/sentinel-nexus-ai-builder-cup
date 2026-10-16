"""Live check against BigQuery market_graph (reservation gql-poc). Opt in with GRAPH_LIVE_TESTS=1.

GRAPH_LIVE_TESTS=1 PROJECT_ID=<project> pytest tests/integration/test_graph_live.py
"""

from __future__ import annotations

import os

import pytest

from services.graph.guard import UnsafeQueryError
from services.graph.service import GraphService

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPH_LIVE_TESTS") != "1", reason="set GRAPH_LIVE_TESTS=1 to query BigQuery"
)
PROJECT = os.environ.get("PROJECT_ID", "project-f8544066-3df8-4f99-8ed")
VOLATILE = ("engine", "cached", "fallback_reason")
# one ring per scenario, taken from risk_curated.account_scores (ring_id of an attacker)
RINGS = {"wash_trading": "R_W001", "coordinated_accumulation": "R_C001", "mule_funding": "R_M001"}


def strip(resp):
    return {k: v for k, v in resp.items() if k not in VOLATILE}


@pytest.fixture(scope="module")
def services():
    gql = GraphService(PROJECT, gql_timeout=30.0)
    gql.warm()
    return gql, GraphService(PROJECT, force_fallback=True)


@pytest.mark.parametrize(("scenario", "ring_id"), RINGS.items())
def test_live_gql_and_fallback_return_the_same_ring(services, scenario, ring_id):
    gql, fallback = services
    a, b = gql.ring_subgraph(ring_id), fallback.ring_subgraph(ring_id)
    assert a["engine"] == "gql", a.get("fallback_reason")
    assert b["engine"] == "fallback"
    assert strip(a) == strip(b)
    assert len(a["members"]) >= 2 and a["truncated"] is False


def test_live_ring_matches_the_detector_scores(services):
    gql, _ = services
    rows = gql.runner.query(
        f"SELECT account_id FROM `{PROJECT}.risk_curated.account_scores` WHERE ring_id = @r",
        {"r": "R_M001"},
    )
    assert sorted(r["account_id"] for r in rows) == gql.ring_subgraph("R_M001")["members"]


def test_live_runner_refuses_writes(services):
    gql, _ = services
    with pytest.raises(UnsafeQueryError):
        gql.runner.query(f"DELETE FROM `{PROJECT}.risk_curated.account_scores` WHERE TRUE")
