"""Compare live GCP state with the repo. Read-only. Exit 1 if anything drifted.

    python scripts/verify_sync.py [--project ID] [--no-query]

Checks: BigQuery row counts and key schemas (from table metadata, free), the trades data
fingerprint and ground-truth breakdown vs the canonical generator (one small query, skipped
with --no-query), the property graph, Pub/Sub, Cloud Run (unauthenticated request, public
or not) and the gql-poc reservation and assignment. Only list/describe/get calls and SELECTs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = "project-f8544066-3df8-4f99-8ed"
REGION = "us-central1"
SERVICE = "sentinel-hello"

# Written by the Cloud Shell detect.py (P2 replaces them); a mismatch is a warning, not drift.
SOFT_ROWS = {"risk_curated.alerts": 39, "eval.benchmark": 3, "eval.benchmark_runs": 12}
EXPECTED_TOPICS = {"trades", "trades-dlq"}
EXPECTED_SUBS = {
    "trades-sub": {"ack": 60, "dlq": "trades-dlq", "attempts": 5},
    "trades-dlq-sub": {"ack": 10, "dlq": None, "attempts": None},
}

OK, FAIL, WARN, INFO = "ok", "FAIL", "warn", "info"


# ---------- pure comparison helpers (unit tested) ----------


def expected_graph_rows(data) -> dict[str, int]:
    """Row counts sql/graph/market_graph.sql produces from the generated data."""
    fills = {}
    for t in data.trades:
        fills.setdefault(
            (t["market_id"], t["outcome"], t["ts"], t["price"], t["size_usd"]), {}
        ).setdefault(t["side"], []).append(t["account_id"])
    pairs = {
        (s, b)
        for sides in fills.values()
        for s in sides.get("SELL", [])
        for b in sides.get("BUY", [])
        if s != b
    }
    return {
        "risk_graph.account_nodes": len(data.accounts),
        "risk_graph.device_nodes": len({a["device_id"] for a in data.accounts}),
        "risk_graph.funding_nodes": len({a["funding_source_id"] for a in data.accounts}),
        "risk_graph.market_nodes": len(data.markets),
        "risk_graph.uses_device": len(data.accounts),
        "risk_graph.funded_by": len(data.accounts),
        "risk_graph.traded_in": len({(t["account_id"], t["market_id"]) for t in data.trades}),
        "risk_graph.traded_with": len(pairs),
    }


def parse_ddl_columns(sql: str) -> dict[str, list[tuple[str, str]]]:
    """{'dataset.table': [(column, TYPE), ...]} from CREATE TABLE statements."""
    out = {}
    pattern = r"CREATE TABLE IF NOT EXISTS\s+`__P__\.([\w]+)\.([\w]+)`\s*\((.*?)\n\);"
    for m in re.finditer(pattern, sql, re.S):
        cols = []
        for line in m.group(3).strip().splitlines():
            line = line.strip().rstrip(",")
            if line:
                name, typ = line.split()[:2]
                cols.append((name, typ.upper()))
        out[f"{m.group(1)}.{m.group(2)}"] = cols
    return out


def norm_type(t: str) -> str:
    return {"FLOAT": "FLOAT64", "INTEGER": "INT64", "BOOLEAN": "BOOL"}.get(t.upper(), t.upper())


def compare_columns(live: list[tuple[str, str]], ddl: list[tuple[str, str]]) -> list[str]:
    live_n = [(n, norm_type(t)) for n, t in live]
    ddl_n = [(n, norm_type(t)) for n, t in ddl]
    return [] if live_n == ddl_n else [f"live {live_n} != repo {ddl_n}"]


def check_pubsub(topics: set[str], subs: dict[str, dict]) -> list[tuple[str, str, str]]:
    """subs: {name: {'ack': int, 'dlq': str|None, 'attempts': int|None}}."""
    res = []
    for t in sorted(EXPECTED_TOPICS):
        res.append((f"pubsub topic {t}", OK if t in topics else FAIL, ""))
    for name, want in EXPECTED_SUBS.items():
        got = subs.get(name)
        if got is None:
            res.append((f"pubsub subscription {name}", FAIL, "missing"))
        else:
            res.append(
                (
                    f"pubsub subscription {name}",
                    OK if got == want else FAIL,
                    "" if got == want else f"live {got} != repo {want}",
                )
            )
    return res


def is_public(iam_policy: dict) -> bool:
    return any(
        "allUsers" in b.get("members", []) and b.get("role") == "roles/run.invoker"
        for b in iam_policy.get("bindings", [])
    )


def check_reservation(reservations: list[dict], assignments: list[dict], project: str):
    res = []
    r = next((x for x in reservations if x["name"].endswith("/reservations/gql-poc")), None)
    ok = (
        bool(r)
        and r.get("edition") == "ENTERPRISE"
        and r.get("autoscale", {}).get("maxSlots") == "50"
        and not r.get("slotCapacity")
    )
    res.append(("reservation gql-poc (ENTERPRISE, 0 slots, autoscale 50)", OK if ok else FAIL, ""))
    a = [x for x in assignments if "/reservations/gql-poc/" in x["name"]]
    ok = any(x.get("jobType") == "QUERY" and x.get("assignee") == f"projects/{project}" for x in a)
    res.append(("reservation assignment (QUERY, whole project)", OK if ok else FAIL, ""))
    return res


# ---------- live probes ----------


def _run(cmd: list[str]) -> str:
    env = {**os.environ, "CLOUDSDK_PYTHON": os.environ.get("CLOUDSDK_PYTHON", sys.executable)}
    exe = shutil.which(cmd[0]) or cmd[0]
    p = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, env=env, check=False)
    if p.returncode:
        raise RuntimeError(f"{' '.join(cmd[:4])}... failed: {p.stderr.strip()[:300]}")
    return p.stdout


def _json(cmd: list[str]):
    return json.loads(_run(cmd) or "null")


def bigquery_checks(project: str, run_query: bool):
    from google.cloud import bigquery

    from services.ingest.synth.generator import generate

    results = []
    client = bigquery.Client(project=project, location="US")
    data = generate()
    expected = {
        "risk_raw.trades": len(data.trades),
        "risk_curated.accounts": len(data.accounts),
        "risk_curated.markets": len(data.markets),
        "eval.ground_truth": len(data.ground_truth),
        "eval.decoy_groups": len(data.decoy_groups),
        **expected_graph_rows(data),
    }
    ddl = {}
    for f in sorted((ROOT / "sql" / "schema").glob("*.sql")):
        ddl.update(parse_ddl_columns(f.read_text(encoding="utf-8")))

    for table, want in {**expected, **SOFT_ROWS}.items():
        t = client.get_table(f"{project}.{table}")
        soft = table in SOFT_ROWS
        status = OK if t.num_rows == want else (WARN if soft else FAIL)
        results.append((f"rows {table}", status, f"live {t.num_rows:,} vs repo {want:,}"))
        if table in ddl:
            diff = compare_columns([(f.name, f.field_type) for f in t.schema], ddl[table])
            results.append((f"schema {table}", OK if not diff else FAIL, "; ".join(diff)))
    for ds_table in ddl:
        if ds_table not in expected and ds_table not in SOFT_ROWS:
            results.append((f"table {ds_table} in DDL", FAIL, "not checked"))

    try:
        client.get_dataset(f"{project}.audit")
        results.append(("dataset audit exists", OK, "empty by design until P4"))
    except Exception as e:  # noqa: BLE001
        results.append(("dataset audit exists", FAIL, str(e)[:120]))

    if run_query:
        sql = f"""
          SELECT 'trades' k, CAST(COUNT(*) AS STRING) v1, CAST(ROUND(SUM(size_usd),2) AS STRING) v2
          FROM `{project}.risk_raw.trades`
          UNION ALL
          SELECT scenario_type, CAST(COUNT(*) AS STRING), '' FROM `{project}.eval.ground_truth`
          GROUP BY 1
          UNION ALL
          SELECT 'graph', CAST(COUNT(*) AS STRING), '' FROM
            `{project}`.risk_graph.INFORMATION_SCHEMA.PROPERTY_GRAPHS
            WHERE property_graph_name = 'market_graph'
        """
        rows = {r.k: (r.v1, r.v2) for r in client.query(sql).result()}
        want_sum = f"{sum(t['size_usd'] for t in data.trades):.2f}"
        got = rows.get("trades", ("?", "?"))
        want_sum = sum(t["size_usd"] for t in data.trades)
        got = rows.get("trades", ("?", "0"))
        ok = got[0] == str(len(data.trades)) and abs(float(got[1]) - want_sum) < 0.1
        results.append(
            (
                "trades fingerprint (count, sum size_usd)",
                OK if ok else FAIL,
                f"live {got} vs repo ({len(data.trades)}, {want_sum:.2f})",
            )
        )
        truth = Counter(r["scenario_type"] for r in data.ground_truth)
        live_truth = {k: int(v[0]) for k, v in rows.items() if k not in ("trades", "graph")}
        results.append(
            (
                "ground truth by type",
                OK if live_truth == dict(truth) else FAIL,
                f"live {live_truth} vs repo {dict(truth)}",
            )
        )
        results.append(
            (
                "property graph market_graph exists",
                OK if rows.get("graph", ("0",))[0] == "1" else FAIL,
                "",
            )
        )
    return results


def pubsub_checks(project: str):
    topics = {
        t["name"].rsplit("/", 1)[1]
        for t in _json(
            ["gcloud", "pubsub", "topics", "list", "--project", project, "--format=json"]
        )
    }
    subs = {}
    for s in _json(
        ["gcloud", "pubsub", "subscriptions", "list", "--project", project, "--format=json"]
    ):
        dlp = s.get("deadLetterPolicy")
        subs[s["name"].rsplit("/", 1)[1]] = {
            "ack": s["ackDeadlineSeconds"],
            "dlq": dlp["deadLetterTopic"].rsplit("/", 1)[1] if dlp else None,
            "attempts": dlp["maxDeliveryAttempts"] if dlp else None,
        }
    return check_pubsub(topics, subs)


def cloudrun_checks(project: str):
    svc = _json(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            SERVICE,
            "--region",
            REGION,
            "--project",
            project,
            "--format=json",
        ]
    )
    url = svc["status"]["url"]
    sa = svc["spec"]["template"]["spec"]["serviceAccountName"]
    policy = _json(
        [
            "gcloud",
            "run",
            "services",
            "get-iam-policy",
            SERVICE,
            "--region",
            REGION,
            "--project",
            project,
            "--format=json",
        ]
    )
    res = [("cloud run service account", OK if sa.startswith("sa-agent@") else FAIL, sa)]
    try:
        with urllib.request.urlopen(url + "/", timeout=20) as r:  # no credentials sent
            body = json.loads(r.read().decode())
            res.append(
                (
                    "cloud run responds without auth",
                    OK if r.status == 200 else FAIL,
                    f"{url} -> HTTP {r.status} {body}",
                )
            )
            ok = body == {"status": "ok", "service": "marketsentinel"}
            res.append(("cloud run body matches services/web", OK if ok else FAIL, ""))
    except urllib.error.HTTPError as e:
        res.append(("cloud run responds without auth", INFO, f"{url} -> HTTP {e.code} (private)"))
    except Exception as e:  # noqa: BLE001
        res.append(("cloud run responds without auth", FAIL, f"{url}: {e}"))
    res.append(
        (
            "cloud run public (allUsers invoker)",
            INFO,
            "PUBLIC: anyone on the internet can call it" if is_public(policy) else "private",
        )
    )
    return res


def reservation_checks(project: str):
    base = ["bq", "ls", "--location=US", f"--project_id={project}", "--format=json"]
    return check_reservation(
        _json([*base[:2], "--reservation", *base[2:]]) or [],
        _json([*base[:2], "--reservation_assignment", *base[2:]]) or [],
        project,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID", DEFAULT_PROJECT))
    ap.add_argument("--no-query", action="store_true", help="metadata only, run no SQL")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))

    results = []
    for name, fn in (
        ("bigquery", lambda: bigquery_checks(args.project, not args.no_query)),
        ("pubsub", lambda: pubsub_checks(args.project)),
        ("cloud run", lambda: cloudrun_checks(args.project)),
        ("reservation", lambda: reservation_checks(args.project)),
    ):
        try:
            results += fn()
        except Exception as e:  # noqa: BLE001
            results.append((name, FAIL, f"probe failed: {e}"[:300]))

    for name, status, detail in results:
        print(f"[{status:>4}] {name}" + (f"  ({detail})" if detail else ""))
    failed = [r for r in results if r[1] == FAIL]
    print(
        f"\n{len(results)} checks, {len(failed)} drift, "
        f"{sum(r[1] == WARN for r in results)} warnings"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
