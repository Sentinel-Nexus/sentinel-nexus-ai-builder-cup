"""Orchestrates baseline + scenarios and writes JSONL / ground-truth files."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.ingest.synth import baseline
from services.ingest.synth.common import Row, World
from services.ingest.synth.scenarios import SCENARIOS

TABLES = ("accounts", "markets", "trades", "transfers")


@dataclass
class SyntheticData:
    seed: int
    accounts: list[Row]
    markets: list[Row]
    trades: list[Row]
    transfers: list[Row]
    ground_truth: dict[str, dict[str, Any]]

    def table(self, name: str) -> list[Row]:
        return getattr(self, name)


def _public(row: Row) -> Row:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def generate(
    seed: int = 42, n_accounts: int = 460, n_markets: int = 20, n_trades: int = 20_000
) -> SyntheticData:
    rng = random.Random(seed)
    world = World()
    baseline.make_markets(world, rng, n_markets)
    baseline.make_accounts(world, rng, n_accounts)
    baseline.make_funding(world, rng)
    baseline.make_trades(world, rng, n_trades)

    used_markets: set[str] = set()
    truths = [scenario(world, seed, used_markets) for scenario in SCENARIOS]

    # Assign ids after the global time sort so ids reveal neither scenario nor tag.
    by_tag: dict[str, dict[str, list[str]]] = {}
    for table, prefix, key in (("trades", "t", "trade_id"), ("transfers", "x", "transfer_id")):
        rows = sorted(getattr(world, table), key=lambda r: (r["ts"], r["_tag"] or ""))
        for i, row in enumerate(rows, 1):
            row[key] = f"{prefix}_{i:06d}"
            if row["_tag"]:
                tagged = by_tag.setdefault(row["_tag"], {"trades": [], "transfers": []})
                tagged[table].append(row[key])
        setattr(world, table, rows)

    ground_truth = {
        truth["name"]: {
            "seed": seed,
            **truth,
            "trade_ids": by_tag[truth["name"]]["trades"],
            "transfer_ids": by_tag[truth["name"]]["transfers"],
        }
        for truth in truths
    }
    return SyntheticData(
        seed=seed,
        accounts=[_public(a) for a in world.accounts],
        markets=[_public(m) for m in world.markets],
        trades=[_public(t) for t in world.trades],
        transfers=[_public(t) for t in world.transfers],
        ground_truth=ground_truth,
    )


def _write_jsonl(path: Path, rows: list[Row]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def write_dataset(data: SyntheticData, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        _write_jsonl(out_dir / f"{table}.jsonl", data.table(table))


def write_ground_truth(data: SyntheticData, scenarios_dir: Path) -> None:
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    for name, truth in data.ground_truth.items():
        (scenarios_dir / f"{name}.json").write_text(
            json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )


def sample(data: SyntheticData, baseline_trades: int = 300) -> SyntheticData:
    """Small committed sample: every scenario row plus the first baseline trades."""
    gts = data.ground_truth.values()
    scenario_trades = {t for gt in gts for t in gt["trade_ids"]}
    scenario_accounts = {a for gt in gts for a in gt["account_ids"]}
    trades, extra = [], 0
    for t in data.trades:
        if t["trade_id"] in scenario_trades:
            trades.append(t)
        elif extra < baseline_trades:
            trades.append(t)
            extra += 1
    used = scenario_accounts | {
        a for t in trades for a in (t["taker_account_id"], t["maker_account_id"])
    }
    return SyntheticData(
        seed=data.seed,
        accounts=[a for a in data.accounts if a["account_id"] in used],
        markets=data.markets,
        trades=trades,
        transfers=[x for x in data.transfers if x["account_id"] in used],
        ground_truth=data.ground_truth,
    )
