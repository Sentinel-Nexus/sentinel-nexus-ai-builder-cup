"""Labeled manipulation scenarios injected on top of the baseline."""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from services.ingest.synth.baseline import market_price
from services.ingest.synth.common import (
    World,
    hex_id,
    iso,
    parse_iso,
    scenario_rng,
    uniform_time,
)

Scenario = Callable[[World, int, set[str]], dict[str, Any]]


def _pick_market(world: World, rng: random.Random, used: set[str]) -> dict:
    market = rng.choice([m for m in world.markets if m["market_id"] not in used])
    used.add(market["market_id"])
    return market


def _baseline_ids(world: World) -> list[str]:
    return [a["account_id"] for a in world.accounts if not a.get("_scenario")]


def _new_account(world: World, tag: str, **kwargs: Any) -> dict:
    acct = world.new_account(**kwargs)
    acct["_scenario"] = tag
    return acct


def _ip_block(rng: random.Random) -> str:
    return f"{rng.randint(11, 220)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.0/24"


def _yes_price(market: dict, ts: datetime, rng: random.Random) -> float:
    start, end = parse_iso(market["created_at"]), parse_iso(market["resolution_time"])
    return market_price(market, (ts - start) / (end - start), rng.gauss(0, 0.005))


def wash_trading_ring(world: World, seed: int, used: set[str]) -> dict[str, Any]:
    name = "wash_trading_ring"
    rng = scenario_rng(seed, name)
    market = _pick_market(world, rng, used)
    start, end = parse_iso(market["created_at"]), parse_iso(market["resolution_time"])
    ring_size = rng.randint(5, 8)

    funders = [hex_id(rng, "wallet", 6) for _ in range(2)]
    devices = [hex_id(rng, "dev") for _ in range(2)]
    ip_block = _ip_block(rng)
    first = start + (end - start) * 0.3
    ring = [
        _new_account(
            world,
            name,
            device_id=rng.choice(devices),
            funding_source=rng.choice(funders),
            ip_block=ip_block,
            signup=uniform_time(rng, first - timedelta(days=6), first - timedelta(days=1)),
        )
        for _ in range(ring_size)
    ]
    ids = [a["account_id"] for a in ring]
    for acct in ring:
        world.add_transfer(
            name,
            account_id=acct["account_id"],
            direction="in",
            counterparty=acct["funding_source"],
            amount_usd=round(rng.uniform(1800, 2200), 2),
            ts=iso(parse_iso(acct["signup_date"]) + timedelta(hours=rng.uniform(1, 12))),
        )

    # Round-trips: a pair trades one way, then the reverse, at near-constant price and size.
    n_trades = rng.randrange(120, 161, 2)  # even: whole round-trips
    ts = first
    size = rng.uniform(400, 600)
    a, b = ids[0], ids[1]
    for i in range(n_trades):
        ts += timedelta(minutes=rng.uniform(5, 45))
        if i % 2 == 0:
            a, b = rng.sample(ids, 2)
        maker, taker = (a, b) if i % 2 == 0 else (b, a)
        world.add_trade(
            name,
            market_id=market["market_id"],
            ts=iso(ts),
            taker_account_id=taker,
            maker_account_id=maker,
            outcome="YES",
            side="BUY" if i % 2 == 0 else "SELL",
            price=round(_yes_price(market, ts, rng), 2),
            size_usd=round(size * rng.uniform(0.97, 1.03), 2),
        )
    return {
        "name": name,
        "description": "Linked accounts trade the same YES position back and forth in one market.",
        "market_ids": [market["market_id"]],
        "account_ids": ids,
        "expected_detection": {
            "detector": "wash_trading",
            "flag_accounts": ids,
            "flag_market": market["market_id"],
            "signals": [
                "closed_loop_counterparties",
                "near_zero_net_position",
                "shared_device_or_ip_block",
                "shared_funding_source",
            ],
            "min_cluster_size": ring_size,
        },
    }


def pre_resolution_coordination(world: World, seed: int, used: set[str]) -> dict[str, Any]:
    name = "pre_resolution_coordination"
    rng = scenario_rng(seed, name)
    market = _pick_market(world, rng, used)
    resolution = parse_iso(market["resolution_time"])
    winner = market["resolved_outcome"]
    n = rng.randint(5, 7)
    funders = [hex_id(rng, "wallet", 6) for _ in range(2)]
    baseline = _baseline_ids(world)

    accts = [
        _new_account(
            world,
            name,
            device_id=hex_id(rng, "dev"),
            funding_source=funders[i % 2],
            ip_block=_ip_block(rng),
            signup=resolution - timedelta(days=rng.uniform(4, 12)),
        )
        for i in range(n)
    ]
    ids = [a["account_id"] for a in accts]
    for acct in accts:
        world.add_transfer(
            name,
            account_id=acct["account_id"],
            direction="in",
            counterparty=acct["funding_source"],
            amount_usd=round(rng.uniform(4000, 9000), 2),
            ts=iso(resolution - timedelta(hours=rng.uniform(12, 48))),
        )
        for _ in range(rng.randint(2, 4)):
            ts = resolution - timedelta(hours=rng.uniform(0.5, 6))
            yes_p = _yes_price(market, ts, rng)
            # Buy the winning side while it is still priced as uncertain.
            price = min(0.85, max(0.55, yes_p if winner == "YES" else 1 - yes_p))
            world.add_trade(
                name,
                market_id=market["market_id"],
                ts=iso(ts),
                taker_account_id=acct["account_id"],
                maker_account_id=rng.choice(baseline),
                outcome=winner,
                side="BUY",
                price=round(price, 2),
                size_usd=round(rng.uniform(800, 3000), 2),
            )
    return {
        "name": name,
        "description": "Funding-linked accounts buy the winning outcome in the final hours.",
        "market_ids": [market["market_id"]],
        "account_ids": ids,
        "expected_detection": {
            "detector": "pre_resolution_coordination",
            "flag_accounts": ids,
            "flag_market": market["market_id"],
            "signals": [
                "concentrated_buying_final_6h",
                "same_outcome_direction",
                "shared_funding_source",
                "recent_signup",
            ],
            "shared_funding_sources": sorted(funders),
            "min_cluster_size": n,
        },
    }


def mule_funding(world: World, seed: int, used: set[str]) -> dict[str, Any]:
    name = "mule_funding"
    rng = scenario_rng(seed, name)
    t0 = parse_iso("2026-02-20T00:00:00Z") + timedelta(hours=rng.uniform(0, 72))
    source, destination = hex_id(rng, "wallet", 6), hex_id(rng, "wallet", 6)
    devices = [hex_id(rng, "dev") for _ in range(4)]
    ip_blocks = [_ip_block(rng) for _ in range(2)]
    baseline = _baseline_ids(world)
    open_markets = [
        m
        for m in world.markets
        if parse_iso(m["created_at"]) < t0
        and parse_iso(m["resolution_time"]) > t0 + timedelta(days=6)
    ]
    n = rng.randint(24, 32)
    accts = []
    for _ in range(n):
        signup = t0 + timedelta(hours=rng.uniform(0, 48))
        acct = _new_account(
            world,
            name,
            device_id=rng.choice(devices),
            funding_source=source,
            ip_block=rng.choice(ip_blocks),
            signup=signup,
        )
        accts.append(acct)
        deposit = round(rng.uniform(180, 260), 2)
        last = signup + timedelta(minutes=rng.uniform(2, 60))
        world.add_transfer(
            name,
            account_id=acct["account_id"],
            direction="in",
            counterparty=source,
            amount_usd=deposit,
            ts=iso(last),
        )
        for _ in range(rng.randint(1, 2)):  # token trading to look active
            last += timedelta(hours=rng.uniform(1, 12))
            market = rng.choice(open_markets)
            outcome = rng.choice(["YES", "NO"])
            yes_p = _yes_price(market, last, rng)
            world.add_trade(
                name,
                market_id=market["market_id"],
                ts=iso(last),
                taker_account_id=acct["account_id"],
                maker_account_id=rng.choice(baseline),
                outcome=outcome,
                side="BUY",
                price=round(yes_p if outcome == "YES" else 1 - yes_p, 2),
                size_usd=round(rng.uniform(5, 20), 2),
            )
        world.add_transfer(
            name,
            account_id=acct["account_id"],
            direction="out",
            counterparty=destination,
            amount_usd=round(deposit * 0.97, 2),
            ts=iso(last + timedelta(hours=rng.uniform(6, 48))),
        )
    ids = [a["account_id"] for a in accts]
    return {
        "name": name,
        "description": "Fresh accounts funded from one source, barely trading, one cashout place.",
        "market_ids": sorted({t["market_id"] for t in world.trades if t["_tag"] == name}),
        "account_ids": ids,
        "expected_detection": {
            "detector": "mule_funding",
            "flag_accounts": ids,
            "signals": [
                "single_funding_source_fan_out",
                "single_cashout_destination_fan_in",
                "minimal_trading_volume",
                "fresh_accounts",
            ],
            "funding_source": source,
            "cashout_destination": destination,
            "min_cluster_size": n,
        },
    }


SCENARIOS: tuple[Scenario, ...] = (wash_trading_ring, pre_resolution_coordination, mule_funding)
