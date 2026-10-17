"""Build services/app/static/data/overview.json for the Overview screen.

    python scripts/build_overview_data.py --base http://localhost:8765   # cached BQ totals
    python scripts/build_overview_data.py --base URL --refresh-bq         # estimate, one query

Inputs: benchmark.json, the service's own /summary, /queue and /cases/*/detail (stored reports and
decisions, test records excluded by the service), docs/results/cache_latency.json, and at most
one capped BigQuery query (USD traded by flagged accounts per scenario, plus trade and account
counts). The estimate is printed first; the result is cached in docs/results/overview_bq.json so
a rebuild does not query again. The page itself makes no BigQuery or Gemini call. Synthetic data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "services" / "app" / "static" / "data" / "overview.json"
BENCH = REPO / "services" / "app" / "static" / "data" / "benchmark.json"
BQ_CACHE = REPO / "docs" / "results" / "overview_bq.json"
CACHE_LAT = REPO / "docs" / "results" / "cache_latency.json"
MAX_BYTES = 50 * 1024 * 1024  # hard cap on the one query

SQL = """
WITH gt AS (
  SELECT account_id, ANY_VALUE(scenario_type) AS scenario_type
  FROM `__P__.eval.ground_truth` GROUP BY account_id
),
flagged AS (SELECT account_id FROM `__P__.risk_curated.account_scores` WHERE flagged)
SELECT gt.scenario_type AS k, SUM(t.size_usd) AS usd, COUNT(*) AS n_trades,
       COUNT(DISTINCT t.account_id) AS n_accounts
FROM `__P__.risk_raw.trades` t JOIN flagged USING (account_id) JOIN gt USING (account_id)
GROUP BY k
UNION ALL
SELECT '__all_trades', SUM(size_usd), COUNT(*), COUNT(DISTINCT account_id)
FROM `__P__.risk_raw.trades`
UNION ALL
SELECT '__accounts', 0.0, COUNT(*), COUNT(*) FROM `__P__.risk_curated.accounts`
"""

TYPE_LABEL = {
    "wash_trading": "Trading back and forth",
    "coordinated_accumulation": "Coordinated buying",
    "mule_funding": "Shared funding",
}
GROUP_LABEL = {
    "DG1_household": "Households",
    "DG2_exchange_cluster": "Exchange clusters",
    "DG3_busy_honest": "Busy honest traders",
    "normal_accounts": "Normal accounts",
}


def fetch(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=60) as r:  # noqa: S310
        return json.loads(r.read())


def bq_totals(refresh: bool) -> dict:
    if BQ_CACHE.exists() and not refresh:
        return json.loads(BQ_CACHE.read_text(encoding="utf-8"))
    from google.cloud import bigquery

    from scripts.run_sql import render

    project = os.environ.get("PROJECT_ID", "project-f8544066-3df8-4f99-8ed")
    client = bigquery.Client(project=project, location="US")
    sql = render(SQL, project)
    dry = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True), location="US")
    print(f"BigQuery estimate: {dry.total_bytes_processed:,} bytes (cap {MAX_BYTES:,}); 1 query")
    if dry.total_bytes_processed > MAX_BYTES:
        raise SystemExit("estimate above the cap; not running")
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES)
    rows = [dict(r.items()) for r in client.query(sql, job_config=cfg, location="US").result()]
    out = {
        "queried_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d"),
        "rows": {r["k"]: {k: r[k] for k in ("usd", "n_trades", "n_accounts")} for r in rows},
    }
    BQ_CACHE.parent.mkdir(parents=True, exist_ok=True)
    BQ_CACHE.write_text(json.dumps(out, indent=1), encoding="utf-8", newline="\n")
    return out


def build(base: str, refresh_bq: bool = False) -> dict:
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    summary = fetch(base, "/summary")
    queue = fetch(base, "/queue")
    bq = bq_totals(refresh_bq)["rows"]
    det, base_rule = bench["kpi"]["detector"], bench["kpi"]["baseline"]
    seed = [r for r in bench["seed42"] if r["group"] != "all_scenarios"]
    types = list(dict.fromkeys(r["group"] for r in seed))

    def of(group: str, method: str) -> dict:
        return next(r for r in seed if r["group"] == group and r["method"].startswith(method))

    cases = []
    for a in queue:
        if not a["has_case"]:
            continue
        d = fetch(base, f"/cases/{a['alert_id']}/detail")
        rep = d["report"]
        cases.append(
            {
                "alert_id": a["alert_id"],
                "injected": bool(a["injected"]),
                "seconds": round((d["latency_ms"] or 0) / 1000, 1),
                "kept": len(rep.get("evidence_table") or []),
                "removed": len(rep.get("removed_claims") or []),
                "lane": d["effective_lane"],
            }
        )
    secs = [c["seconds"] for c in cases]
    cached_ms = None
    if CACHE_LAT.exists():
        per = json.loads(CACHE_LAT.read_text(encoding="utf-8")).get("per_alert", {})
        cached_ms = (
            round(statistics.median(v["median_ms"] for v in per.values()), 3) if per else None
        )
    real = [a for a in queue if not a["injected"]]
    vol = {t: bq.get(t, {}) for t in types}
    total_usd = round(sum(v.get("usd", 0) for v in vol.values()), 2)
    det_sum = summary["detector_alerts"]
    return {
        "synthetic": True,
        "as_of": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d"),
        "sources": [
            "benchmark.json",
            "/summary",
            "/queue",
            "stored case reports",
            "BigQuery (1 query)",
        ],
        "summary": {
            "detector_alerts": det_sum,
            "evaluation_cases": summary["evaluation_cases"],
            "decided": summary["decided"],
        },
        "top_alert": real[0]["alert_id"] if real else None,
        "kpi": {
            "caught": {"caught": det["tp"], "total": 39, "pct": det["recall"]},
            "false_alarms": {"marketsentinel": det["fp"], "simple_rule": base_rule["fp"]},
            "volume_usd": total_usd,
            "report_time": {
                "median_s": round(statistics.median(secs), 1) if secs else None,
                "min_s": min(secs, default=None),
                "max_s": max(secs, default=None),
                "n": len(secs),
                "cached_read_ms": cached_ms,
            },
            "evidence": {
                "kept": sum(c["kept"] for c in cases),
                "removed": sum(c["removed"] for c in cases),
                "cases": len(cases),
            },
        },
        "catch_by_type": [
            {
                "type": t,
                "label": TYPE_LABEL.get(t, t.replace("_", " ")),
                "simple_rule": of(t, "Flat")["recall"],
                "marketsentinel": of(t, "Detector")["recall"],
            }
            for t in types
        ],
        "false_alarms": [
            {
                "group": g["group"],
                "label": GROUP_LABEL.get(g["group"], g["group"]),
                "accounts": g["accounts"],
                "simple_rule": g["flat_rule"],
                "marketsentinel": g["detector"],
            }
            for g in bench["innocent_groups"]
        ],
        "funnel": [
            {"label": "Trades", "value": int(bq["__all_trades"]["n_trades"])},
            {"label": "Accounts", "value": int(bq["__accounts"]["n_trades"])},
            {"label": "Flagged accounts", "value": det["flagged"]},
            {"label": "Alerts", "value": det_sum["alerts"]},
            {"label": "Escalated cases", "value": det_sum["lanes"]["escalated"]},
            {"label": "Analyst decisions", "value": det_sum["decided"]},
        ],
        "types_found": [
            {"type": t, "label": TYPE_LABEL.get(t, t), "accounts": of(t, "Detector")["tp"]}
            for t in types
        ],
        "volume_by_type": [
            {"type": t, "label": TYPE_LABEL.get(t, t), "usd": round(vol[t].get("usd", 0), 2)}
            for t in types
        ],
        "speed": cases,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8765")
    ap.add_argument("--refresh-bq", action="store_true")
    a = ap.parse_args(argv)
    data = build(a.base.rstrip("/"), a.refresh_bq)
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    raise SystemExit(main(sys.argv[1:]))
