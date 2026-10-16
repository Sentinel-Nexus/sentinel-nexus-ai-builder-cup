"""Canonical synthetic data generator (ADR 0001).

This is the Cloud Shell generator that produced the live BigQuery data, made deterministic:
the same (seed, now) always gives the same rows. Seed 42 reproduces the live tables
(2,039 accounts, 35,073 trades, 20 markets, 39 ground-truth accounts).

The order of random calls is part of the contract. Do not reorder them; tests compare the
output to the live counts. Decoys and evasions (ADR 0002) draw from a separate RNG, run after
everything else and only append rows, so the earlier rows keep their values and ids.
"""

from __future__ import annotations

import datetime as dt
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Row = dict[str, Any]

DEFAULT_SEED = 42
# Fixed anchor so timestamps are reproducible (the Cloud Shell script used the wall clock).
DEFAULT_NOW = dt.datetime(2026, 10, 12, 16, 0, 0, tzinfo=dt.UTC)
COUNTRIES = ["SG", "HK", "IN", "ID", "MY", "JP", "KR", "AU", "TH", "PH"]
N_NORMAL_ACCOUNTS = 2000
N_MARKETS = 20

# scenario_id (as stored in eval.ground_truth) -> (file name, description)
SCENARIOS = {
    "S1_wash_ring": (
        "wash_trading_ring",
        "Linked accounts sell and buy the same YES position to each other in one open market.",
    ),
    "S2_pre_resolution": (
        "pre_resolution_coordination",
        "Newly created accounts with one funding source buy large YES positions in the "
        "minutes before the market resolves.",
    ),
    "S3_mule_funding": (
        "mule_funding",
        "Many new accounts funded from a single source make small trades right after sign-up.",
    ),
}
SIGNALS = {
    "wash_trading_ring": [
        "matched_buy_sell_fills",
        "shared_device",
        "shared_funding_source",
        "young_accounts",
    ],
    "pre_resolution_coordination": [
        "large_buys_before_resolution",
        "rising_price",
        "shared_funding_source",
        "young_accounts",
    ],
    "mule_funding": ["shared_funding_source", "trade_within_2h_of_signup", "young_accounts"],
}


@dataclass(frozen=True)
class GenParams:
    """Knobs of one generated world. The defaults are the canonical seed-42 world (ADR 0001,
    0002); changing them never reorders random calls, so defaults reproduce it exactly.
    Robustness seeds use other values (``evals/robustness_profiles.json``)."""

    wash_size: int = 8  # wash ring accounts (200 matched fills at 8, 25 per account)
    coord_size: int = 6  # pre-resolution coordination accounts
    mule_size: int = 25  # mule-funded accounts
    evasion: float = 0.4  # ADR 0002: share of each scenario's attackers that stop sharing funding
    household_size: int = 5  # decoy: people on one home device (start A0100)
    exchange_size: int = 8  # decoy: users withdrawing from one exchange (start A0200)
    busy_size: int = 10  # decoy: independent users reacting to news (start A0300)
    bg_trades: tuple[int, int] = (4, 30)  # trades per background account, inclusive
    device_noise: float = 0.08  # share of background accounts on an innocent shared device


# Innocent groups are drawn from the normal population A0001..A2000.
HOUSEHOLD_START, EXCHANGE_START, BUSY_START = 100, 200, 300


def _ids(start: int, n: int) -> list[str]:
    return [f"A{i:04d}" for i in range(start, start + n)]


EXCHANGE_FUNDING_ID = "FEX1"
HOUSEHOLD_DEVICE_ID = "DH1"
EVADED_SHARED_DEVICE = "DC_EV"


@dataclass
class SyntheticData:
    seed: int
    now: dt.datetime
    accounts: list[Row] = field(default_factory=list)
    markets: list[Row] = field(default_factory=list)
    trades: list[Row] = field(default_factory=list)
    ground_truth: list[Row] = field(default_factory=list)  # rows of eval.ground_truth
    decoy_groups: list[Row] = field(default_factory=list)  # rows of eval.decoy_groups
    scenario_trade_ids: dict[str, list[str]] = field(default_factory=dict)

    def scenarios(self) -> dict[str, Row]:
        """Per-scenario ground-truth documents, keyed by file name."""
        out: dict[str, Row] = {}
        for sid, (name, description) in SCENARIOS.items():
            rows = [r for r in self.ground_truth if r["scenario_id"] == sid]
            accounts = sorted(r["account_id"] for r in rows)
            trade_ids = self.scenario_trade_ids.get(sid, [])
            trade_markets = {t["market_id"] for t in self.trades if t["trade_id"] in set(trade_ids)}
            out[name] = {
                "name": name,
                "scenario_id": sid,
                "scenario_type": rows[0]["scenario_type"],
                "description": description,
                "seed": self.seed,
                "account_ids": accounts,
                "market_ids": sorted({r["market_id"] for r in rows} | trade_markets),
                "trade_ids": trade_ids,
                "expected_detection": {
                    "flag_accounts": accounts,
                    "min_cluster_size": len(accounts),
                    "signals": SIGNALS[name],
                },
            }
        return out


def generate(
    seed: int = DEFAULT_SEED, now: dt.datetime = DEFAULT_NOW, params: GenParams | None = None
) -> SyntheticData:
    params = params or GenParams()
    rng = random.Random(seed)
    now = now.astimezone(dt.UTC).replace(microsecond=0)
    t0 = now - dt.timedelta(days=14)
    data = SyntheticData(seed=seed, now=now)
    tid = 0

    def trade(ts, m, a, side, outcome, price, size, sid=None):
        nonlocal tid
        tid += 1
        trade_id = f"T{tid:07d}"
        data.trades.append(
            dict(
                trade_id=trade_id,
                ts=ts.isoformat(),
                market_id=m,
                account_id=a,
                side=side,
                outcome=outcome,
                price=round(min(max(price, 0.01), 0.99), 3),
                size_usd=round(size, 2),
                source="synthetic",
            )
        )
        if sid:
            data.scenario_trade_ids.setdefault(sid, []).append(trade_id)

    def account(a, created, kyc, country, device, fund):
        data.accounts.append(
            dict(
                account_id=a,
                created_ts=created.isoformat(),
                kyc_tier=kyc,
                country=country,
                device_id=device,
                funding_source_id=fund,
                source="synthetic",
            )
        )

    def truth(sid, stype, a, m):
        data.ground_truth.append(
            dict(scenario_id=sid, scenario_type=stype, account_id=a, market_id=m)
        )

    # ---- markets: 20 of them; M05 stays open, M12 resolved yesterday ----
    resolve, base_price = {}, {}
    for i in range(1, N_MARKETS + 1):
        m = f"M{i:02d}"
        resolve[m] = t0 + dt.timedelta(days=rng.randint(4, 13))
        base_price[m] = rng.uniform(0.15, 0.85)
    resolve["M05"] = now + dt.timedelta(days=3)
    resolve["M12"] = now - dt.timedelta(days=1)
    for m, r in resolve.items():
        data.markets.append(
            dict(
                market_id=m,
                title=f"Synthetic market {m}",
                resolve_ts=r.isoformat(),
                source="synthetic",
            )
        )

    # ---- background: 2,000 normal accounts ----
    for i in range(1, N_NORMAL_ACCOUNTS + 1):
        a = f"A{i:04d}"
        created = t0 - dt.timedelta(days=rng.randint(5, 400))
        # 8% innocent shared devices and 3% innocent shared funding are noise for detectors.
        device = (
            f"D{i:04d}" if rng.random() > params.device_noise else f"D{rng.randint(1, 200):04d}"
        )
        fund = f"F{i:04d}" if rng.random() > 0.03 else f"F{rng.randint(1, 2000):04d}"
        account(a, created, rng.choice([1, 2, 2, 3]), rng.choice(COUNTRIES), device, fund)
        for _ in range(rng.randint(*params.bg_trades)):
            m = f"M{rng.randint(1, 20):02d}"
            end = min(resolve[m], now)
            ts = t0 + (end - t0) * rng.random()
            trade(
                ts,
                m,
                a,
                rng.choice(["BUY", "SELL"]),
                rng.choice(["YES", "NO"]),
                base_price[m] + rng.uniform(-0.08, 0.08),
                min(rng.lognormvariate(4.0, 0.8), 800),
            )

    # ---- Scenario 1: wash-trading ring (8 linked accounts trade with each other) ----
    sid = "S1_wash_ring"
    ring = [f"W{i:03d}" for i in range(1, params.wash_size + 1)]
    for i, a in enumerate(ring):
        account(a, now - dt.timedelta(days=rng.randint(3, 6)), 1, "SG", f"DW{i % 2 + 1}", "FW1")
        truth(sid, "wash_trading", a, "M05")
    for _ in range(params.wash_size * 25):
        a, b = rng.sample(ring, 2)
        ts = now - dt.timedelta(days=2) + dt.timedelta(minutes=rng.randint(0, 2 * 24 * 60 - 1))
        p, size = 0.50 + rng.uniform(-0.01, 0.01), rng.uniform(300, 900)
        trade(ts, "M05", a, "SELL", "YES", p, size, sid)
        trade(ts, "M05", b, "BUY", "YES", p, size, sid)

    # ---- Scenario 2: coordinated buying just before resolution (insider pattern) ----
    sid = "S2_pre_resolution"
    group = [f"C{i:03d}" for i in range(1, params.coord_size + 1)]
    rt = resolve["M12"]
    for a in group:
        account(a, rt - dt.timedelta(days=rng.randint(2, 3)), 2, "HK", f"DC_{a}", "FC1")
        truth(sid, "coordinated_accumulation", a, "M12")
        for _ in range(rng.randint(5, 8)):
            ts = rt - dt.timedelta(minutes=rng.randint(20, 180))
            p = 0.55 + 0.37 * (1 - (rt - ts).total_seconds() / (180 * 60))  # climbs to resolution
            trade(
                ts,
                "M12",
                a,
                "BUY",
                "YES",
                p + rng.uniform(-0.02, 0.02),
                rng.uniform(2000, 5000),
                sid,
            )

    # ---- Scenario 3: mule funding (25 new accounts, one funding source) ----
    sid = "S3_mule_funding"
    for i in range(1, params.mule_size + 1):
        a = f"M{i:03d}"
        created = now - dt.timedelta(hours=rng.randint(6, 48))
        account(a, created, 1, "ID", f"DM{i % 3 + 1}", "FM1")
        truth(sid, "mule_funding", a, "M03")
        for _ in range(rng.randint(3, 6)):
            ts = created + dt.timedelta(minutes=rng.randint(1, 90))
            trade(
                ts,
                rng.choice(["M03", "M08"]),
                a,
                rng.choice(["BUY", "SELL"]),
                rng.choice(["YES", "NO"]),
                rng.uniform(0.4, 0.6),
                rng.choice([50, 100, 100, 200]),
                sid,
            )

    _plant_decoys_and_evasions(data, now, resolve, base_price, trade, params)
    return data


def _plant_decoys_and_evasions(data, now, resolve, base_price, trade, params) -> None:
    """ADR 0002. Deterministic (own RNG), applied at generation time, never by SQL UPDATE.

    Evasion: a share of each scenario's attackers stop sharing a funding source and stay
    linked only through devices or matched fills. Decoys: three innocent groups that each look
    suspicious on one signal. They go to ``decoy_groups``, used only for false-positive reports.
    """
    aux = random.Random(f"{data.seed}:decoys")
    accounts = {a["account_id"]: a for a in data.accounts}

    household = _ids(HOUSEHOLD_START, params.household_size)
    exchange = _ids(EXCHANGE_START, params.exchange_size)
    busy = _ids(BUSY_START, params.busy_size)

    for sid in ("S1_wash_ring", "S2_pre_resolution", "S3_mule_funding"):
        fraction = params.evasion
        members = sorted(r["account_id"] for r in data.ground_truth if r["scenario_id"] == sid)
        for a in sorted(aux.sample(members, round(len(members) * fraction))):
            accounts[a]["funding_source_id"] = f"FE_{a}"
            if sid == "S2_pre_resolution":
                accounts[a]["device_id"] = EVADED_SHARED_DEVICE

    def group(group_id, group_type, members, market_id=None):
        for a in members:
            data.decoy_groups.append(
                dict(group_id=group_id, group_type=group_type, account_id=a, market_id=market_id)
            )

    for a in household:
        accounts[a]["device_id"] = HOUSEHOLD_DEVICE_ID
    group("DG1_household", "shared_device_household", household)

    for a in exchange:
        accounts[a]["funding_source_id"] = EXCHANGE_FUNDING_ID
    group("DG2_exchange_cluster", "shared_exchange_funding", exchange)

    # Busy but honest: aged, unlinked accounts piling into one outcome in the last hours of a
    # market after public news. Same direction and timing as an insider ring, but no links.
    attack_markets = {"M03", "M05", "M08", "M12"}
    market = aux.choice(
        sorted(m for m, r in resolve.items() if r < now and m not in attack_markets)
    )
    rt = resolve[market]
    favoured = aux.choice(["YES", "NO"])
    other = "NO" if favoured == "YES" else "YES"
    for a in busy:
        for _ in range(aux.randint(3, 6)):
            hours = aux.uniform(0.5, 5.0)
            outcome = favoured if aux.random() < 0.85 else other
            drift = 0.15 * (1 - hours / 5.0)
            yes_p = base_price[market] + drift * (1 if favoured == "YES" else -1)
            price = yes_p if outcome == "YES" else 1 - yes_p
            size = min(aux.lognormvariate(6.2, 0.6), 3000)
            trade(rt - dt.timedelta(hours=hours), market, a, "BUY", outcome, price, size)
    group("DG3_busy_honest", "busy_but_honest", busy, market)


# ---- writers ----

TABLES = ("accounts", "markets", "trades")


def _write_jsonl(path: Path, rows: list[Row]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def write_dataset(data: SyntheticData, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        _write_jsonl(out_dir / f"{table}.jsonl", getattr(data, table))
    _write_jsonl(out_dir / "ground_truth.jsonl", data.ground_truth)
    _write_jsonl(out_dir / "decoy_groups.jsonl", data.decoy_groups)


def write_ground_truth(data: SyntheticData, scenarios_dir: Path) -> None:
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    for name, doc in data.scenarios().items():
        (scenarios_dir / f"{name}.json").write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )


def sample(data: SyntheticData, baseline_trades: int = 300) -> SyntheticData:
    """Small committed sample: every scenario trade and account plus some baseline trades."""
    scenario_trades = {t for ids in data.scenario_trade_ids.values() for t in ids}
    scenario_accounts = {r["account_id"] for r in data.ground_truth}
    decoy_accounts = {r["account_id"] for r in data.decoy_groups}
    trades, extra = [], 0
    for t in data.trades:
        if t["trade_id"] in scenario_trades or t["account_id"] in decoy_accounts:
            trades.append(t)
        elif extra < baseline_trades:
            trades.append(t)
            extra += 1
    used = scenario_accounts | decoy_accounts | {t["account_id"] for t in trades}
    return SyntheticData(
        seed=data.seed,
        now=data.now,
        accounts=[a for a in data.accounts if a["account_id"] in used],
        markets=data.markets,
        trades=trades,
        ground_truth=data.ground_truth,
        decoy_groups=data.decoy_groups,
        scenario_trade_ids=data.scenario_trade_ids,
    )
