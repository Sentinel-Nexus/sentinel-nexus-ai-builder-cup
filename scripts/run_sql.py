"""Render a SQL file's __P__ placeholder and run it on BigQuery.

Dry-run by default (BigQuery validates the script and reports bytes processed, nothing is
executed). Pass --apply to run it for real. Windows-safe: no sed or shell tooling.

    python scripts/run_sql.py sql/graph/market_graph.sql            # dry run
    python scripts/run_sql.py sql/graph/market_graph.sql --apply    # run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PLACEHOLDER = "__P__"
LOCATION = "US"


def render(sql: str, project: str) -> str:
    """Substitute the project id for every __P__; fail if the project looks invalid."""
    if not project or any(c in project for c in " `'\"/\\;"):
        raise ValueError(f"invalid project id: {project!r}")
    rendered = sql.replace(PLACEHOLDER, project)
    if PLACEHOLDER in rendered:  # defensive: cannot happen after replace
        raise ValueError("unrendered placeholder left in SQL")
    return rendered


def run(sql: str, project: str, apply: bool, client=None) -> dict:
    """Run rendered SQL. Returns a small summary dict. `client` is injectable for tests."""
    if client is None:
        from google.cloud import bigquery

        client = bigquery.Client(project=project, location=LOCATION)
    from google.cloud import bigquery

    config = bigquery.QueryJobConfig(dry_run=not apply, use_query_cache=False)
    job = client.query(sql, job_config=config, location=LOCATION)
    if not apply:
        return {"applied": False, "bytes_processed": job.total_bytes_processed}
    job.result()
    return {"applied": True, "job_id": job.job_id}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    ap.add_argument("--apply", action="store_true", help="execute (default is dry run)")
    args = ap.parse_args(argv)

    if not args.project:
        print("error: set PROJECT_ID or pass --project", file=sys.stderr)
        return 2
    sql = render(args.file.read_text(encoding="utf-8"), args.project)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] {args.file} -> project {args.project}, location {LOCATION}")
    print(run(sql, args.project, args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
