"""Load generated data into BigQuery (batch loads are free). Dry-run unless apply=True."""

from __future__ import annotations

from services.ingest.synth.generator import SyntheticData

LOCATION = "US"

# destination table -> (SyntheticData attribute, [(column, BigQuery type)])
TABLE_SPECS = {
    "risk_raw.trades": (
        "trades",
        [
            ("trade_id", "STRING"),
            ("ts", "TIMESTAMP"),
            ("market_id", "STRING"),
            ("account_id", "STRING"),
            ("side", "STRING"),
            ("outcome", "STRING"),
            ("price", "FLOAT"),
            ("size_usd", "FLOAT"),
            ("source", "STRING"),
        ],
    ),
    "risk_curated.accounts": (
        "accounts",
        [
            ("account_id", "STRING"),
            ("created_ts", "TIMESTAMP"),
            ("kyc_tier", "INTEGER"),
            ("country", "STRING"),
            ("device_id", "STRING"),
            ("funding_source_id", "STRING"),
            ("source", "STRING"),
        ],
    ),
    "risk_curated.markets": (
        "markets",
        [
            ("market_id", "STRING"),
            ("title", "STRING"),
            ("resolve_ts", "TIMESTAMP"),
            ("source", "STRING"),
        ],
    ),
    "eval.ground_truth": (
        "ground_truth",
        [
            ("scenario_id", "STRING"),
            ("scenario_type", "STRING"),
            ("account_id", "STRING"),
            ("market_id", "STRING"),
        ],
    ),
    "eval.decoy_groups": (
        "decoy_groups",
        [
            ("group_id", "STRING"),
            ("group_type", "STRING"),
            ("account_id", "STRING"),
            ("market_id", "STRING"),
        ],
    ),
}

VALIDATION_DATASET = "eval_val"


def validation_destinations(seed: int) -> dict[str, str]:
    """Live table -> validation table for one seed, e.g. risk_raw.trades -> eval_val.s7_trades."""
    return {t: f"{VALIDATION_DATASET}.s{seed}_{t.split('.')[1]}" for t in TABLE_SPECS}


def load(
    data: SyntheticData,
    project: str,
    apply: bool = False,
    client=None,
    destinations: dict[str, str] | None = None,
) -> dict[str, int]:
    """WRITE_TRUNCATE each table. Returns {table: row count}. Nothing is written unless apply.

    ``destinations`` maps each TABLE_SPECS name to another dataset.table; validation seeds use
    it to write ``eval_val.s<seed>_*`` instead of the live tables.
    """
    specs = {(destinations or {}).get(t, t): spec for t, spec in TABLE_SPECS.items()}
    counts = {table: len(getattr(data, attr)) for table, (attr, _) in specs.items()}
    if not apply:
        return counts
    from google.cloud import bigquery

    client = client or bigquery.Client(project=project, location=LOCATION)
    for table, (attr, cols) in specs.items():
        cfg = bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField(n, t) for n, t in cols],
            write_disposition="WRITE_TRUNCATE",
        )
        client.load_table_from_json(
            getattr(data, attr), f"{project}.{table}", job_config=cfg
        ).result()
    return counts
