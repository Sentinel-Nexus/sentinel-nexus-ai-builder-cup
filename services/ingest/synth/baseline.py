"""Normal-trading baseline: accounts, markets, trades and funding flows."""

from __future__ import annotations

import math
import random
from datetime import timedelta

from services.ingest.synth.common import (
    WINDOW_START,
    World,
    hex_id,
    iso,
    parse_iso,
    uniform_time,
)

CATEGORIES = ["politics", "crypto", "sports", "economics", "tech", "culture"]
EXCHANGES = [
    f"cex_{n}"
    for n in (
        "alpha",
        "bravo",
        "coin",
        "delta",
        "echo",
        "foxtrot",
        "gamma",
        "hotel",
        "india",
        "juliet",
        "kilo",
        "lima",
    )
]


def market_price(market: dict, ts_frac: float, noise: float = 0.0) -> float:
    """YES probability at fraction ``ts_frac`` of the market's life."""
    target = 0.97 if market["resolved_outcome"] == "YES" else 0.03
    p = market["_p0"] + (target - market["_p0"]) * ts_frac**3 + noise
    return min(0.98, max(0.02, p))


def make_markets(world: World, rng: random.Random, n: int) -> None:
    for i in range(n):
        created = uniform_time(rng, WINDOW_START, WINDOW_START + timedelta(days=40))
        resolution = created + timedelta(days=rng.uniform(25, 60))
        world.markets.append(
            {
                "market_id": f"mkt_{i + 1:03d}",
                "question": f"Synthetic question #{i + 1}",
                "category": rng.choice(CATEGORIES),
                "created_at": iso(created),
                "resolution_time": iso(resolution),
                "resolved_outcome": rng.choice(["YES", "NO"]),
                "_p0": round(rng.uniform(0.2, 0.8), 3),
            }
        )


def make_accounts(world: World, rng: random.Random, n: int) -> None:
    ip_pool = [
        f"{rng.randint(11, 220)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.0/24"
        for _ in range(150)
    ]
    devices: list[str] = []
    for _ in range(n):
        # ~5% of users share a device with an earlier user (household, internet cafe).
        if devices and rng.random() < 0.05:
            device = rng.choice(devices)
        else:
            device = hex_id(rng, "dev")
            devices.append(device)
        funding = rng.choice(EXCHANGES) if rng.random() < 0.7 else hex_id(rng, "wallet", 6)
        signup = uniform_time(
            rng, WINDOW_START - timedelta(days=30), WINDOW_START + timedelta(days=60)
        )
        world.new_account(
            device_id=device, funding_source=funding, ip_block=rng.choice(ip_pool), signup=signup
        )


def make_funding(world: World, rng: random.Random) -> None:
    for acct in world.accounts:
        signup = parse_iso(acct["signup_date"])
        deposit_ts = signup + timedelta(hours=rng.uniform(0.2, 48))
        amount = round(max(20.0, rng.lognormvariate(math.log(400), 0.9)), 2)
        world.add_transfer(
            None,
            account_id=acct["account_id"],
            direction="in",
            counterparty=acct["funding_source"],
            amount_usd=amount,
            ts=iso(deposit_ts),
        )
        if rng.random() < 0.1:
            world.add_transfer(
                None,
                account_id=acct["account_id"],
                direction="out",
                counterparty=hex_id(rng, "wallet", 6),
                amount_usd=round(amount * rng.uniform(0.3, 1.5), 2),
                ts=iso(deposit_ts + timedelta(days=rng.uniform(5, 40))),
            )


def make_trades(world: World, rng: random.Random, n: int) -> None:
    n_accts = len(world.accounts)
    # Heavy-tailed account activity and market popularity.
    acct_w = [rng.paretovariate(1.5) for _ in range(n_accts)]
    mkt_w = [rng.uniform(0.3, 3.0) for _ in world.markets]
    signups = [parse_iso(a["signup_date"]) for a in world.accounts]
    for _ in range(n):
        market = rng.choices(world.markets, weights=mkt_w)[0]
        start, end = parse_iso(market["created_at"]), parse_iso(market["resolution_time"])
        frac = rng.betavariate(1.0, 1.4)  # activity skews toward market open
        ts = start + (end - start) * min(frac, 0.999)
        while True:
            ti = rng.choices(range(n_accts), weights=acct_w)[0]
            if signups[ti] <= ts:
                break
        mi = rng.randrange(n_accts)
        while mi == ti:
            mi = rng.randrange(n_accts)
        outcome = rng.choice(["YES", "NO"])
        yes_p = market_price(market, (ts - start) / (end - start), rng.gauss(0, 0.02))
        price = yes_p if outcome == "YES" else 1 - yes_p
        world.add_trade(
            None,
            market_id=market["market_id"],
            ts=iso(ts),
            taker_account_id=world.accounts[ti]["account_id"],
            maker_account_id=world.accounts[mi]["account_id"],
            outcome=outcome,
            side=rng.choice(["BUY", "SELL"]),
            price=round(min(0.99, max(0.01, price)), 2),
            size_usd=round(max(1.0, rng.lognormvariate(math.log(40), 1.1)), 2),
        )
