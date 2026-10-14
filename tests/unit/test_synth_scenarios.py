"""Canonical generator tests (ADR 0001). Seed 42 must reproduce the live BigQuery counts."""

from __future__ import annotations

import collections
import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.ingest.synth.generator import (
    DEFAULT_NOW,
    generate,
    sample,
    write_dataset,
    write_ground_truth,
)
from services.ingest.synth.load import load

SEED = 42
SCENARIO_NAMES = ["wash_trading_ring", "pre_resolution_coordination", "mule_funding"]


@pytest.fixture(scope="module")
def data():
    return generate(SEED)


def _by_id(rows, key):
    return {r[key]: r for r in rows}


def test_matches_live_counts(data):
    """Live BigQuery (2026-10-14): 2,039 accounts, 35,073 trades, 20 markets, 39 truth rows."""
    assert len(data.accounts) == 2039
    assert len(data.trades) == 35073
    assert len(data.markets) == 20
    assert len(data.ground_truth) == 39
    by_type = collections.Counter(r["scenario_type"] for r in data.ground_truth)
    assert by_type == {"wash_trading": 8, "coordinated_accumulation": 6, "mule_funding": 25}


def test_other_seed_differs(data):
    other = generate(7)
    assert len(other.trades) == 34306  # the count quoted in the original P1 brief
    assert other.trades != data.trades


def test_deterministic_including_timestamps(data):
    again = generate(SEED)
    assert again.trades == data.trades
    assert again.accounts == data.accounts
    assert again.scenarios() == data.scenarios()


def test_now_shifts_timestamps_not_structure(data):
    later = generate(SEED, DEFAULT_NOW + dt.timedelta(days=1))
    assert len(later.trades) == len(data.trades)
    assert later.trades[0]["ts"] != data.trades[0]["ts"]


def test_ids_unique(data):
    for rows, key in (
        (data.trades, "trade_id"),
        (data.accounts, "account_id"),
        (data.markets, "market_id"),
    ):
        ids = [r[key] for r in rows]
        assert len(ids) == len(set(ids))


def test_referential_integrity(data):
    accounts = {a["account_id"] for a in data.accounts}
    markets = {m["market_id"] for m in data.markets}
    assert all(t["account_id"] in accounts and t["market_id"] in markets for t in data.trades)
    assert all(0 < t["price"] < 1 for t in data.trades)
    assert {r["account_id"] for r in data.ground_truth} <= accounts


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_ground_truth_references_exist(data, name):
    gt = data.scenarios()[name]
    accounts = _by_id(data.accounts, "account_id")
    trades = _by_id(data.trades, "trade_id")
    assert gt["seed"] == SEED
    assert set(gt["account_ids"]) <= set(accounts)
    assert set(gt["trade_ids"]) <= set(trades)
    assert set(gt["market_ids"]) <= {m["market_id"] for m in data.markets}
    assert gt["expected_detection"]["flag_accounts"] == gt["account_ids"]
    assert gt["expected_detection"]["min_cluster_size"] == len(gt["account_ids"])


def test_scenarios_do_not_overlap(data):
    sets = [set(gt["account_ids"]) for gt in data.scenarios().values()]
    assert not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])


def test_wash_ring_is_closed_and_matched(data):
    gt = data.scenarios()["wash_trading_ring"]
    ring = set(gt["account_ids"])
    trades = _by_id(data.trades, "trade_id")
    ring_trades = [trades[t] for t in gt["trade_ids"]]
    assert len(ring) == 8 and len(ring_trades) == 400
    assert {t["market_id"] for t in ring_trades} == {"M05"}
    assert all(t["account_id"] in ring for t in ring_trades)
    # each fill is a SELL and a BUY with identical time, price and size
    for sell, buy in zip(ring_trades[::2], ring_trades[1::2], strict=True):
        assert (sell["side"], buy["side"]) == ("SELL", "BUY")
        assert (sell["ts"], sell["price"], sell["size_usd"]) == (
            buy["ts"],
            buy["price"],
            buy["size_usd"],
        )
        assert sell["account_id"] != buy["account_id"]
    accounts = _by_id(data.accounts, "account_id")
    assert {accounts[a]["funding_source_id"] for a in ring} == {"FW1"}


def test_pre_resolution_buys_are_late_and_large(data):
    gt = data.scenarios()["pre_resolution_coordination"]
    group = set(gt["account_ids"])
    market = _by_id(data.markets, "market_id")["M12"]
    resolve = dt.datetime.fromisoformat(market["resolve_ts"])
    trades = _by_id(data.trades, "trade_id")
    group_trades = [trades[t] for t in gt["trade_ids"]]
    assert len(group) == 6 and len(group_trades) >= 2 * len(group)
    for t in group_trades:
        assert t["account_id"] in group and t["side"] == "BUY" and t["outcome"] == "YES"
        assert (
            dt.timedelta(0) < resolve - dt.datetime.fromisoformat(t["ts"]) <= dt.timedelta(hours=3)
        )
        assert t["size_usd"] >= 2000


def test_mule_funding_accounts_are_new_and_small(data):
    gt = data.scenarios()["mule_funding"]
    mules = set(gt["account_ids"])
    accounts = _by_id(data.accounts, "account_id")
    assert len(mules) == 25
    assert {accounts[a]["funding_source_id"] for a in mules} == {"FM1"}
    trades = _by_id(data.trades, "trade_id")
    for t in (trades[i] for i in gt["trade_ids"]):
        assert t["size_usd"] <= 200
        created = dt.datetime.fromisoformat(accounts[t["account_id"]]["created_ts"])
        assert (
            dt.timedelta(0)
            < dt.datetime.fromisoformat(t["ts"]) - created
            <= dt.timedelta(minutes=90)
        )


def test_write_outputs_and_sample(data, tmp_path: Path):
    write_dataset(data, tmp_path / "full")
    write_ground_truth(data, tmp_path / "scenarios")
    lines = (tmp_path / "full" / "trades.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(data.trades)
    assert json.loads(lines[0])["trade_id"] == data.trades[0]["trade_id"]
    for name, gt in data.scenarios().items():
        written = json.loads((tmp_path / "scenarios" / f"{name}.json").read_text("utf-8"))
        assert written == gt

    small = sample(data)
    assert len(small.trades) < 1500
    trade_ids = {t["trade_id"] for t in small.trades}
    accounts = {a["account_id"] for a in small.accounts}
    for gt in data.scenarios().values():
        assert set(gt["trade_ids"]) <= trade_ids
        assert set(gt["account_ids"]) <= accounts
    assert all(t["account_id"] in accounts for t in small.trades)


def test_committed_scenarios_match_generator(data):
    root = Path(__file__).resolve().parents[2]
    for name, gt in data.scenarios().items():
        committed = json.loads((root / "evals" / "scenarios" / f"{name}.json").read_text("utf-8"))
        assert committed == gt, f"{name}.json is stale: rerun python -m services.ingest.synth"


def test_load_is_dry_run_by_default(data):
    client = MagicMock()
    counts = load(data, "p", apply=False, client=client)
    assert counts["risk_raw.trades"] == 35073
    client.load_table_from_json.assert_not_called()
