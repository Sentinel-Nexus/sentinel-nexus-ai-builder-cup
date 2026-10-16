"""Canonical generator tests (ADR 0001, 0002): live counts, scenarios, decoys and evasions."""

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


def test_matches_expected_counts(data):
    """2,039 accounts, 20 markets, 39 truth rows; 35,073 ADR 0001 trades plus 43 decoy trades."""
    assert len(data.accounts) == 2039
    assert len(data.trades) == 35073 + 43
    assert len(data.markets) == 20
    assert len(data.ground_truth) == 39
    by_type = collections.Counter(r["scenario_type"] for r in data.ground_truth)
    assert by_type == {"wash_trading": 8, "coordinated_accumulation": 6, "mule_funding": 25}


def test_other_seed_differs(data):
    other = generate(7)
    assert len(other.trades) != len(data.trades)
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
    assert {accounts[a]["funding_source_id"] for a in ring} <= {"FW1"} | {f"FE_{a}" for a in ring}


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
    assert {accounts[a]["funding_source_id"] for a in mules} <= {"FM1"} | {f"FE_{a}" for a in mules}
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
    assert counts["risk_raw.trades"] == 35073 + 43
    assert counts["eval.decoy_groups"] == 23
    client.load_table_from_json.assert_not_called()


def _funding_counts(data):
    return collections.Counter(a["funding_source_id"] for a in data.accounts)


def test_evasion_removes_funding_links_from_part_of_each_scenario(data):
    accounts = _by_id(data.accounts, "account_id")
    for name, evaded in zip(SCENARIO_NAMES, (3, 2, 10), strict=True):
        ids = data.scenarios()[name]["account_ids"]
        unique = [a for a in ids if accounts[a]["funding_source_id"] == f"FE_{a}"]
        assert len(unique) == evaded
    # evaded coordination accounts keep a link: a shared device
    gt = data.scenarios()["pre_resolution_coordination"]["account_ids"]
    assert sum(accounts[a]["device_id"] == "DC_EV" for a in gt) == 2


def test_decoy_groups_are_innocent_and_labelled(data):
    attackers = {r["account_id"] for r in data.ground_truth}
    decoys = collections.defaultdict(list)
    for r in data.decoy_groups:
        decoys[r["group_id"]].append(r["account_id"])
    assert {k: len(v) for k, v in decoys.items()} == {
        "DG1_household": 5,
        "DG2_exchange_cluster": 8,
        "DG3_busy_honest": 10,
    }
    assert not attackers & {a for v in decoys.values() for a in v}
    accounts = _by_id(data.accounts, "account_id")
    assert {accounts[a]["device_id"] for a in decoys["DG1_household"]} == {"DH1"}
    assert {accounts[a]["funding_source_id"] for a in decoys["DG2_exchange_cluster"]} == {"FEX1"}


def test_busy_honest_trades_are_late_but_not_linked(data):
    accounts = _by_id(data.accounts, "account_id")
    busy = [r for r in data.decoy_groups if r["group_id"] == "DG3_busy_honest"]
    market = busy[0]["market_id"]
    resolve = dt.datetime.fromisoformat(_by_id(data.markets, "market_id")[market]["resolve_ts"])
    ids = {r["account_id"] for r in busy}
    late = [t for t in data.trades if t["account_id"] in ids and t["market_id"] == market]
    assert len(late) >= 30
    assert all(
        dt.timedelta(0) < resolve - dt.datetime.fromisoformat(t["ts"]) <= dt.timedelta(hours=5)
        for t in late
        if t["trade_id"] > "T0035073"
    )
    assert len({accounts[a]["device_id"] for a in ids}) == len(ids)
    assert len({accounts[a]["funding_source_id"] for a in ids}) == len(ids)


def test_flat_funding_rule_is_no_longer_near_perfect(data):
    """The shared-funding >= 5 rule: misses evaders and flags the exchange cluster."""
    counts = _funding_counts(data)
    accounts = _by_id(data.accounts, "account_id")
    flagged = {a for a, r in accounts.items() if counts[r["funding_source_id"]] >= 5}
    truth = {r["account_id"] for r in data.ground_truth}
    tp = len(flagged & truth)
    assert tp / len(truth) < 0.6
    assert tp / len(flagged) < 0.8
    assert len(flagged - truth) == 8


def test_pre_adr0002_trades_are_unchanged(data):
    ids = [t["trade_id"] for t in data.trades]
    assert ids[:35073] == [f"T{i:07d}" for i in range(1, 35074)]


def test_default_params_are_the_canonical_world():
    from services.ingest.synth.generator import GenParams

    assert generate(SEED, params=GenParams()).trades == generate(SEED).trades


def test_params_change_sizes_and_keep_the_pipeline_valid():
    from services.ingest.synth.generator import GenParams

    p = GenParams(
        wash_size=12,
        coord_size=4,
        mule_size=40,
        evasion=0.6,
        household_size=3,
        exchange_size=12,
        busy_size=14,
        bg_trades=(2, 15),
        device_noise=0.15,
    )
    d = generate(101, params=p)
    kinds = collections.Counter(r["scenario_type"] for r in d.ground_truth)
    assert kinds == {"wash_trading": 12, "coordinated_accumulation": 4, "mule_funding": 40}
    groups = collections.Counter(r["group_id"] for r in d.decoy_groups)
    assert groups == {"DG1_household": 3, "DG2_exchange_cluster": 12, "DG3_busy_honest": 14}
    accounts = _by_id(d.accounts, "account_id")
    evaded = [a for a in accounts.values() if a["funding_source_id"].startswith("FE_")]
    assert len(evaded) == round(12 * 0.6) + round(4 * 0.6) + round(40 * 0.6)
    assert len({t["trade_id"] for t in d.trades}) == len(d.trades)


def test_robustness_profiles_cover_the_requested_ranges():
    from services.ingest.synth.profiles import load_profiles

    profiles = load_profiles()
    assert len(profiles) >= 6 and not set(profiles) & {7, 11, 23, 42}
    evasion = [p.evasion for p in profiles.values()]
    assert min(evasion) >= 0.2 and max(evasion) <= 0.6 and max(evasion) - min(evasion) >= 0.3
    for field in ("wash_size", "coord_size", "mule_size", "busy_size", "device_noise"):
        assert len({getattr(p, field) for p in profiles.values()}) >= 5, field
