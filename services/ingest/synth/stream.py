"""Held-out stream scenario for the near-real-time micro-batch path (P6).

An extension of the canonical generator (ADR 0001): same idioms, a NEW seed that shares nothing
with the seed-42 world (account ids ``S***``, trade ids ``ST******``, devices ``SD**``, funding
sources ``SF**``), so adding it changes no existing row, id or count. It describes one small
burst as time-ordered events:

- ``account_registered``: account_id, device_id, funding_source_id, kyc_tier, created_ts, ts
- ``trade``: trade_id, market_id, account_id, side, outcome, price, size_usd, ts

The burst holds one wash-trading ring of 6 new accounts (two shared devices, funding shared by
only 4 of the 6, reciprocal matched fills), one innocent household of 4 aged accounts (one shared
device, ordinary trading) and background accounts with ordinary trading. Markets are existing
markets of the live ``risk_curated.markets`` table (the detector joins trades to it).

The events file carries no labels. Ground truth is written to a separate file that only
evaluation scripts read (``scripts/stream_eval.py``, tests); the service and the detector never
open it.

    python -m services.ingest.synth.stream            # writes data/stream/burst_01.jsonl
"""

from __future__ import annotations

import datetime as dt
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Row = dict[str, Any]

STREAM_SEED = (
    6001  # not used by the live world (42), validation (7, 11, 23) or robustness (101-108)
)
BURST_ID = "burst_01"
T0 = dt.datetime(2026, 10, 1, 0, 0, 0, tzinfo=dt.UTC)  # burst window start (event time)
ROOT = Path(__file__).resolve().parents[3]
BURST_FILE = ROOT / "data" / "stream" / f"{BURST_ID}.jsonl"
TRUTH_FILE = ROOT / "evals" / "stream" / f"{BURST_ID}_truth.json"

RING_MARKET = "M05"  # open at the time of the burst
BG_MARKETS = ["M01", "M02", "M03", "M06", "M13", "M14"]
ORDER = {"account_registered": 0, "trade": 1}


@dataclass
class StreamBurst:
    seed: int
    events: list[Row] = field(default_factory=list)  # time-ordered
    ring: list[str] = field(default_factory=list)
    household: list[str] = field(default_factory=list)
    background: list[str] = field(default_factory=list)

    def truth(self) -> Row:
        """Labels for evaluation scripts only."""
        return {
            "burst_id": BURST_ID,
            "seed": self.seed,
            "wash_ring": sorted(self.ring),
            "innocent_household": sorted(self.household),
            "background": sorted(self.background),
            "expected": {"flag": sorted(self.ring), "do_not_flag": sorted(self.household)},
            "counts": {
                "events": len(self.events),
                "account_registered": sum(
                    e["event_type"] == "account_registered" for e in self.events
                ),
                "trade": sum(e["event_type"] == "trade" for e in self.events),
            },
        }


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_stream(seed: int = STREAM_SEED) -> StreamBurst:
    rng = random.Random(f"stream:{seed}")
    burst = StreamBurst(seed=seed)
    n_trade = 0

    def account(a, created, kyc, device, fund):
        burst.events.append(
            dict(
                event_type="account_registered",
                account_id=a,
                device_id=device,
                funding_source_id=fund,
                kyc_tier=kyc,
                created_ts=_iso(created),
                ts=_iso(created),
            )
        )

    def trade(ts, market, a, side, outcome, price, size):
        nonlocal n_trade
        n_trade += 1
        burst.events.append(
            dict(
                event_type="trade",
                trade_id=f"ST{n_trade:06d}",
                market_id=market,
                account_id=a,
                side=side,
                outcome=outcome,
                price=round(min(max(price, 0.01), 0.99), 3),
                size_usd=round(size, 2),
                ts=_iso(ts),
            )
        )

    def ordinary_trades(a, n):
        for _ in range(n):
            m = rng.choice(BG_MARKETS)
            ts = T0 + dt.timedelta(minutes=rng.randint(0, 2 * 24 * 60 - 1))
            trade(
                ts,
                m,
                a,
                rng.choice(["BUY", "SELL"]),
                rng.choice(["YES", "NO"]),
                rng.uniform(0.25, 0.75),
                min(rng.lognormvariate(4.0, 0.8), 800),
            )

    # ---- background: 30 ordinary accounts, one innocent shared device between two of them ----
    for i in range(1, 31):
        a = f"SB{i:02d}"
        burst.background.append(a)
        device = f"SDB{i:02d}" if i != 8 else "SDB07"
        account(
            a,
            T0 - dt.timedelta(days=rng.randint(5, 400)),
            rng.choice([1, 2, 2, 3]),
            device,
            f"SFB{i:02d}",
        )
        ordinary_trades(a, rng.randint(3, 8))

    # ---- innocent household: 4 aged accounts on one home device, ordinary trading ----
    for i in range(1, 5):
        a = f"SH{i:02d}"
        burst.household.append(a)
        account(a, T0 - dt.timedelta(days=rng.randint(40, 300)), 2, "SDH1", f"SFH{i:02d}")
        ordinary_trades(a, rng.randint(5, 8))

    # ---- wash ring: 6 new accounts, shared devices, funding shared by 4 of 6, matched fills ----
    for i in range(1, 7):
        a = f"SW{i:02d}"
        burst.ring.append(a)
        account(
            a,
            T0 - dt.timedelta(days=rng.randint(3, 6)),
            1,
            f"SDW{(i - 1) // 3 + 1}",
            "SFW1" if i <= 4 else f"SFE_{a}",
        )
    for _ in range(40):
        s, b = rng.sample(burst.ring, 2)
        ts = T0 + dt.timedelta(minutes=rng.randint(0, 2 * 24 * 60 - 1))
        p, size = 0.50 + rng.uniform(-0.01, 0.01), rng.uniform(300, 900)
        trade(ts, RING_MARKET, s, "SELL", "YES", p, size)
        trade(ts, RING_MARKET, b, "BUY", "YES", p, size)

    burst.events.sort(
        key=lambda e: (e["ts"], ORDER[e["event_type"]], e.get("trade_id", e["account_id"]))
    )
    return burst


def write_burst(burst: StreamBurst, path: Path = BURST_FILE, truth_path: Path = TRUTH_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for e in burst.events:
            fh.write(json.dumps(e, sort_keys=True) + "\n")
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(
        json.dumps(burst.truth(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    burst = generate_stream()
    write_burst(burst)
    t = burst.truth()["counts"]
    print(f"seed={burst.seed} {t} -> {BURST_FILE} (labels: {TRUTH_FILE})")


if __name__ == "__main__":
    main()
