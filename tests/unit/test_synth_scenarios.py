from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from services.ingest.synth.common import parse_iso
from services.ingest.synth.generator import generate, sample, write_dataset, write_ground_truth

SEED = 42


@pytest.fixture(scope="module")
def data():
    return generate(SEED)


def _by_id(rows, key):
    return {r[key]: r for r in rows}


def test_baseline_shape(data):
    assert len(data.markets) == 20
    assert 480 <= len(data.accounts) <= 520
    assert 20_000 <= len(data.trades) <= 20_500
    assert set(data.ground_truth) == {
        "wash_trading_ring",
        "pre_resolution_coordination",
        "mule_funding",
    }
    for acct in data.accounts:
        assert {"device_id", "funding_source", "ip_block", "signup_date"} <= set(acct)


def test_deterministic(data):
    again = generate(SEED)
    assert again.trades == data.trades
    assert again.ground_truth == data.ground_truth
    assert generate(SEED + 1).trades != data.trades


def test_ids_unique_and_unlabeled(data):
    for rows, key in (
        (data.trades, "trade_id"),
        (data.accounts, "account_id"),
        (data.transfers, "transfer_id"),
    ):
        ids = [r[key] for r in rows]
        assert len(ids) == len(set(ids))
    assert all(not k.startswith("_") for t in data.trades for k in t)
    assert [t["ts"] for t in data.trades] == sorted(t["ts"] for t in data.trades)


SCENARIO_NAMES = ["wash_trading_ring", "pre_resolution_coordination", "mule_funding"]


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_ground_truth_references_exist(data, name):
    gt = data.ground_truth[name]
    accounts = _by_id(data.accounts, "account_id")
    trades = _by_id(data.trades, "trade_id")
    transfers = _by_id(data.transfers, "transfer_id")
    assert gt["seed"] == SEED
    assert set(gt["account_ids"]) <= set(accounts)
    assert set(gt["trade_ids"]) <= set(trades)
    assert set(gt["transfer_ids"]) <= set(transfers)
    assert set(gt["market_ids"]) <= {m["market_id"] for m in data.markets}
    assert gt["expected_detection"]["flag_accounts"] == gt["account_ids"]
    assert gt["expected_detection"]["min_cluster_size"] == len(gt["account_ids"])


def test_scenarios_do_not_overlap(data):
    sets = [set(gt["account_ids"]) for gt in data.ground_truth.values()]
    assert not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])


def test_wash_trading_ring(data):
    gt = data.ground_truth["wash_trading_ring"]
    ring = set(gt["account_ids"])
    assert 5 <= len(ring) <= 8
    trades = _by_id(data.trades, "trade_id")
    ring_trades = [trades[t] for t in gt["trade_ids"]]
    assert 120 <= len(ring_trades) <= 160
    assert len(gt["market_ids"]) == 1
    assert {t["market_id"] for t in ring_trades} == set(gt["market_ids"])
    # Closed loop: every counterparty is inside the ring.
    assert all({t["taker_account_id"], t["maker_account_id"]} <= ring for t in ring_trades)
    # Back and forth: every trade is followed by its reverse.
    for first, second in zip(ring_trades[::2], ring_trades[1::2], strict=True):
        assert (first["maker_account_id"], first["taker_account_id"]) == (
            second["taker_account_id"],
            second["maker_account_id"],
        )
    accounts = _by_id(data.accounts, "account_id")
    assert len({accounts[a]["ip_block"] for a in ring}) == 1
    assert len({accounts[a]["funding_source"] for a in ring}) <= 2


def test_pre_resolution_coordination(data):
    gt = data.ground_truth["pre_resolution_coordination"]
    group = set(gt["account_ids"])
    assert 5 <= len(group) <= 7
    (market_id,) = gt["market_ids"]
    market = _by_id(data.markets, "market_id")[market_id]
    resolution = parse_iso(market["resolution_time"])
    trades = _by_id(data.trades, "trade_id")
    group_trades = [trades[t] for t in gt["trade_ids"]]
    assert len(group_trades) >= 2 * len(group)
    for t in group_trades:
        assert t["taker_account_id"] in group
        assert t["side"] == "BUY" and t["outcome"] == market["resolved_outcome"]
        assert timedelta(0) < resolution - parse_iso(t["ts"]) <= timedelta(hours=6)
    accounts = _by_id(data.accounts, "account_id")
    funding = {accounts[a]["funding_source"] for a in group}
    assert funding == set(gt["expected_detection"]["shared_funding_sources"])
    assert len(funding) < len(group)


def test_mule_funding(data):
    gt = data.ground_truth["mule_funding"]
    mules = set(gt["account_ids"])
    det = gt["expected_detection"]
    assert 24 <= len(mules) <= 32
    accounts = _by_id(data.accounts, "account_id")
    assert {accounts[a]["funding_source"] for a in mules} == {det["funding_source"]}
    transfers = [x for x in data.transfers if x["account_id"] in mules]
    ins = {x["counterparty"] for x in transfers if x["direction"] == "in"}
    assert ins == {det["funding_source"]}
    outs = [x for x in transfers if x["direction"] == "out"]
    assert len(outs) == len(mules)
    assert {x["counterparty"] for x in outs} == {det["cashout_destination"]}
    trades = _by_id(data.trades, "trade_id")
    for t in (trades[i] for i in gt["trade_ids"]):
        assert t["size_usd"] <= 20
    per_account = {a: 0 for a in mules}
    for t in (trades[i] for i in gt["trade_ids"]):
        per_account[t["taker_account_id"]] += 1
    assert all(1 <= n <= 2 for n in per_account.values())


def test_referential_integrity_and_market_windows(data):
    accounts = {a["account_id"] for a in data.accounts}
    markets = _by_id(data.markets, "market_id")
    for t in data.trades:
        assert t["taker_account_id"] in accounts and t["maker_account_id"] in accounts
        m = markets[t["market_id"]]
        assert parse_iso(m["created_at"]) <= parse_iso(t["ts"]) <= parse_iso(m["resolution_time"])
        assert 0 < t["price"] < 1
    assert all(x["account_id"] in accounts for x in data.transfers)


def test_write_outputs_and_sample(data, tmp_path: Path):
    write_dataset(data, tmp_path / "full")
    write_ground_truth(data, tmp_path / "scenarios")
    lines = (tmp_path / "full" / "trades.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(data.trades)
    assert json.loads(lines[0])["trade_id"] == data.trades[0]["trade_id"]
    for name, gt in data.ground_truth.items():
        assert json.loads((tmp_path / "scenarios" / f"{name}.json").read_text("utf-8")) == gt

    small = sample(data)
    assert len(small.trades) < 1000
    trade_ids = {t["trade_id"] for t in small.trades}
    accounts = {a["account_id"] for a in small.accounts}
    for gt in data.ground_truth.values():
        assert set(gt["trade_ids"]) <= trade_ids
        assert set(gt["account_ids"]) <= accounts
    assert all({t["taker_account_id"], t["maker_account_id"]} <= accounts for t in small.trades)
