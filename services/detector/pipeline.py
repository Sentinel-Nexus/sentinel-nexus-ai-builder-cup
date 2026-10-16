"""Render and run the detector SQL (sql/views) against one target. Dry-run unless apply.

A target is either the live tables or one validation seed in ``eval_val``. Every statement
is dry-run first and runs with a maximum-bytes-billed cap; the SQL is set-based (the only
loop is the bounded label-propagation hop inside det_30_components.sql).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scripts.run_sql import LOCATION, render

ROOT = Path(__file__).resolve().parents[2]
VIEWS = ROOT / "sql" / "views"
STEPS = (
    "det_10_fills.sql",
    "det_20_links.sql",
    "det_30_components.sql",
    "det_40_features.sql",
    "baseline_flat_rule.sql",
)
MAX_BYTES_BILLED = 500 * 1024 * 1024
PLACEHOLDERS = ("__TRADES__", "__ACCOUNTS__", "__MARKETS__", "__OUT__", "__PFX__")


@dataclass(frozen=True)
class Target:
    name: str
    split: str  # 'evaluation' (live) or 'validation'
    seed: int
    trades: str
    accounts: str
    markets: str
    out_dataset: str
    prefix: str

    def table(self, name: str) -> str:
        return f"{self.out_dataset}.{self.prefix}{name}"


def live_target(project: str, seed: int = 42) -> Target:
    return Target(
        "live",
        "evaluation",
        seed,
        f"{project}.risk_raw.trades",
        f"{project}.risk_curated.accounts",
        f"{project}.risk_curated.markets",
        f"{project}.risk_curated",
        "",
    )


def validation_target(project: str, seed: int) -> Target:
    ds = f"{project}.eval_val"
    return Target(
        f"s{seed}",
        "validation",
        seed,
        f"{ds}.s{seed}_trades",
        f"{ds}.s{seed}_accounts",
        f"{ds}.s{seed}_markets",
        ds,
        f"s{seed}_",
    )


def parse_target(project: str, text: str) -> Target:
    return live_target(project) if text == "live" else validation_target(project, int(text))


def render_step(sql: str, project: str, target: Target) -> str:
    sql = (
        sql.replace("__TRADES__", target.trades)
        .replace("__ACCOUNTS__", target.accounts)
        .replace("__MARKETS__", target.markets)
        .replace("__OUT__", target.out_dataset)
        .replace("__PFX__", target.prefix)
    )
    sql = render(sql, project)
    if any(p in sql for p in PLACEHOLDERS):
        raise ValueError("unrendered placeholder")
    return sql


def run_pipeline(project: str, target: Target, apply: bool, client=None, steps=STEPS) -> list[dict]:
    from google.api_core.exceptions import BadRequest, NotFound
    from google.cloud import bigquery

    client = client or bigquery.Client(project=project, location=LOCATION)
    report = []
    for name in steps:
        sql = render_step((VIEWS / name).read_text(encoding="utf-8"), project, target)
        dry_cfg = bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False, maximum_bytes_billed=MAX_BYTES_BILLED
        )
        entry = {"step": name, "applied": False}
        try:
            entry["dry_run_bytes"] = client.query(sql, job_config=dry_cfg).total_bytes_processed
        except (NotFound, BadRequest) as e:
            # Depends on a table an earlier step has not created yet (or a table created
            # inside the same script); it is validated by the real run below.
            entry["dry_run_bytes"] = None
            entry["dry_run_note"] = str(e).splitlines()[0][:160]
        if apply:
            cfg = bigquery.QueryJobConfig(
                use_query_cache=False, maximum_bytes_billed=MAX_BYTES_BILLED
            )
            job = client.query(sql, job_config=cfg)
            job.result()
            entry.update(applied=True, job_id=job.job_id, slot_ms=job.slot_millis)
        report.append(entry)
    return report


def fetch_rows(client, query_table: str) -> list[dict]:
    return [dict(r.items()) for r in client.query(f"SELECT * FROM `{query_table}`").result()]
