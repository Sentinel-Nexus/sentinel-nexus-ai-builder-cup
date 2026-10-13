"""Shared helpers and the mutable world the generators write into."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

WINDOW_START = datetime(2026, 1, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 4, 1, tzinfo=UTC)

Row = dict[str, Any]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def hex_id(rng: random.Random, prefix: str, nbytes: int = 4) -> str:
    return f"{prefix}_{rng.getrandbits(nbytes * 8):0{nbytes * 2}x}"


def uniform_time(rng: random.Random, start: datetime, end: datetime) -> datetime:
    span = (end - start).total_seconds()
    return start + timedelta(seconds=rng.uniform(0, span))


def scenario_rng(seed: int, name: str) -> random.Random:
    return random.Random(f"{seed}:{name}")


@dataclass
class World:
    """Rows accumulated across the baseline and scenario generators.

    Trades and transfers carry a ``_tag`` (scenario name or None) until ids are
    assigned after the global time sort; ids therefore never leak labels.
    """

    accounts: list[Row] = field(default_factory=list)
    markets: list[Row] = field(default_factory=list)
    trades: list[Row] = field(default_factory=list)
    transfers: list[Row] = field(default_factory=list)

    def new_account(
        self,
        *,
        device_id: str,
        funding_source: str,
        ip_block: str,
        signup: datetime,
    ) -> Row:
        row = {
            "account_id": f"acct_{len(self.accounts) + 1:04d}",
            "device_id": device_id,
            "funding_source": funding_source,
            "ip_block": ip_block,
            "signup_date": iso(signup),
        }
        self.accounts.append(row)
        return row

    def add_trade(self, tag: str | None, **fields: Any) -> Row:
        row = {"trade_id": None, **fields, "_tag": tag}
        self.trades.append(row)
        return row

    def add_transfer(self, tag: str | None, **fields: Any) -> Row:
        row = {"transfer_id": None, **fields, "_tag": tag}
        self.transfers.append(row)
        return row

    def market(self, market_id: str) -> Row:
        return next(m for m in self.markets if m["market_id"] == market_id)
