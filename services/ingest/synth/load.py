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
}


def load(data: SyntheticData, project: str, apply: bool = False, client=None) -> dict[str, int]:
    """WRITE_TRUNCATE each table. Returns {table: row count}. Nothing is written unless apply."""
    counts = {table: len(getattr(data, attr)) for table, (attr, _) in TABLE_SPECS.items()}
    if not apply:
        return counts
    from google.cloud import bigquery

    client = client or bigquery.Client(project=project, location=LOCATION)
    for table, (attr, cols) in TABLE_SPECS.items():
        cfg = bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField(n, t) for n, t in cols],
            write_disposition="WRITE_TRUNCATE",
        )
        client.load_table_from_json(
            getattr(data, attr), f"{project}.{table}", job_config=cfg
        ).result()
    return counts
