"""Create the stream tables and grant sa-agent exactly the access the stream path needs.

    python infra/stream_setup.py             # dry run: prints every statement, command and grant
    python infra/stream_setup.py --apply     # create the tables, apply the grants, read them back
    python infra/stream_setup.py --verify    # read-only: print the current policies

What it does, and nothing else:
  1. sql/schema/60_stream.sql: four NEW tables, CREATE TABLE IF NOT EXISTS
       risk_raw.stream_accounts, risk_raw.stream_trades (partitioned by ts, clustered by
       account_id), risk_curated.stream_alerts, audit.stream_events
  2. sql/iam/sa_agent_stream_tables.sql: roles/bigquery.dataEditor on those four tables only
  3. roles/pubsub.publisher on topic `trades` only, roles/pubsub.subscriber on subscription
     `trades-sub` only, both for sa-agent
No project-level role, no deletion, no change to an existing topic, subscription or table, and
the gql-poc reservation is not touched. Every command has a timeout.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.run_sql import render, run  # noqa: E402

PROJECT = os.environ.get("PROJECT_ID", "project-f8544066-3df8-4f99-8ed")
SA = f"serviceAccount:sa-agent@{PROJECT}.iam.gserviceaccount.com"
TABLES = (
    "risk_raw.stream_accounts",
    "risk_raw.stream_trades",
    "risk_curated.stream_alerts",
    "audit.stream_events",
)
TIMEOUT_S = 120


def split_statements(sql: str) -> list[str]:
    """Statements of a DDL/GRANT file (no semicolons inside strings in these files)."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def gcloud(args: list[str], timeout: int = TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gcloud", *args], capture_output=True, text=True, timeout=timeout, shell=(os.name == "nt")
    )


def pubsub_commands() -> list[list[str]]:
    return [
        ["pubsub", "topics", "add-iam-policy-binding", "trades", "--project", PROJECT,
         "--member", SA, "--role", "roles/pubsub.publisher", "--quiet"],
        ["pubsub", "subscriptions", "add-iam-policy-binding", "trades-sub", "--project", PROJECT,
         "--member", SA, "--role", "roles/pubsub.subscriber", "--quiet"],
    ]  # fmt: skip


def table_policy(table: str) -> list[str]:
    """Roles sa-agent holds on one table (read-only)."""
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT, location="US")
    try:
        policy = client.get_iam_policy(f"{PROJECT}.{table}")
    except Exception as e:  # noqa: BLE001
        return [f"(not readable: {type(e).__name__})"]
    return sorted(b["role"] for b in policy.bindings if SA in set(b["members"])) or [
        "(no binding for sa-agent)"
    ]


def print_pubsub_policy(kind: str, name: str) -> None:
    out = gcloud(["pubsub", kind, "get-iam-policy", name, "--project", PROJECT, "--format",
                  "json(bindings)"])  # fmt: skip
    print(f"  {kind[:-1]} {name}: {(out.stdout or out.stderr).strip()[:600]}")


def verify() -> None:
    print("grants held by sa-agent on the four stream tables:")
    for t in TABLES:
        print(f"  {t}: {table_policy(t)}")
    print_pubsub_policy("topics", "trades")
    print_pubsub_policy("subscriptions", "trades-sub")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really create and grant")
    ap.add_argument("--verify", action="store_true", help="read-only: print current grants")
    a = ap.parse_args(argv)
    if a.verify:
        verify()
        return 0

    ddl = split_statements(render((REPO / "sql/schema/60_stream.sql").read_text("utf-8"), PROJECT))
    grants = split_statements(
        render((REPO / "sql/iam/sa_agent_stream_tables.sql").read_text("utf-8"), PROJECT)
    )
    print(f"mode: {'APPLY' if a.apply else 'DRY RUN (nothing is created or granted)'}")
    print(f"project {PROJECT}; BigQuery location US\n")
    print(f"1. BigQuery: {len(ddl)} CREATE TABLE IF NOT EXISTS statements (new tables only)")
    for s in ddl:
        print("   " + s.splitlines()[0])
    print(f"\n2. BigQuery table-level grants to sa-agent ({len(grants)}):")
    for s in grants:
        print("   " + " ".join(s.split()))
    print("\n3. Pub/Sub grants to sa-agent (resource-level):")
    for c in pubsub_commands():
        print("   gcloud " + " ".join(c))
    print(
        "\nNot touched: gql-poc reservation, existing topics, subscriptions, tables, project IAM."
    )
    print("Nothing is deleted.\n")

    if not a.apply:
        for s in ddl:  # a dry run validates each statement with BigQuery (free)
            print("   dry run:", run(s, PROJECT, apply=False))
        print("\ncurrent state (before):")
        verify()
        print("\ndry run only. Re-run with --apply after approval.")
        return 0

    for s in ddl:
        print("   created:", s.splitlines()[0], run(s, PROJECT, apply=True))
    for s in grants:
        print("   granted:", " ".join(s.split())[:90], run(s, PROJECT, apply=True))
    for c in pubsub_commands():
        r = gcloud(c)
        print("   gcloud", " ".join(c[:4]), "->", "ok" if r.returncode == 0 else r.stderr[-300:])
        if r.returncode:
            return r.returncode
    print("\nresulting grants (read back):")
    verify()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
