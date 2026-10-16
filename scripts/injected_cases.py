"""Build and store the four injected review cases (EV_C004 and the three decoy groups).

    python -m scripts.injected_cases                    # dry run: alert file, plan, estimate
    python -m scripts.injected_cases --apply            # runs the agents (spends Gemini tokens)

What it does:
1. reads the detector scores and the evaluation labels (free `list_rows` reads) and writes
   data/injected_alerts.json (detector fields only, `injected: "evaluation"`; no labels);
2. with --apply, runs the final pipeline once per case that has no stored case yet and stores it
   in risk_curated.case_reports with source "eval_injected".

Safe by construction: a hard cap on runs (--max-runs, default 4), the estimate is printed before
anything is sent to Gemini, each run has the pipeline's 120 s wall clock, and the loop stops at the
first run that fails. Cases that already exist are skipped unless --force.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from services.app.audit import drain_all
from services.app.config import Settings
from services.app.cost import estimate, format_estimate
from services.app.injected import PATH

SOURCE = "eval_injected"


def build_alerts(settings: Settings) -> list[dict]:
    from google.cloud import bigquery

    from evals.agents.cases import injected_alerts, load_labels
    from services.app.data import DataStore

    client = bigquery.Client(project=settings.project, location="US")
    store = DataStore(settings.project)
    labels = load_labels(client, settings.project)
    out = []
    for a in injected_alerts(store, labels):
        # the kind (borderline attacker or decoy) is a label: it stays out of the file
        out.append({**a, "injected": "evaluation"})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="run the agents (default: dry run)")
    ap.add_argument("--max-runs", type=int, default=4, help="hard cap on investigations")
    ap.add_argument("--force", action="store_true", help="re-run cases that already exist")
    args = ap.parse_args(argv)

    settings = Settings()
    alerts = build_alerts(settings)
    PATH.write_text(json.dumps(alerts, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {PATH.name}: {[a['alert_id'] for a in alerts]}")

    from services.app.main import default_deps
    from services.app.pipeline import investigate

    deps = default_deps(settings)  # loads the file just written as extra alerts
    have = deps.cases.latest()
    todo = [a for a in alerts if args.force or a["alert_id"] not in have]
    print(f"{len(alerts)} injected alerts, {len(alerts) - len(todo)} stored, {len(todo)} to run")
    print(format_estimate(estimate(len(todo), settings)))
    if len(todo) > args.max_runs:
        print(f"refusing: {len(todo)} runs > --max-runs {args.max_runs}", file=sys.stderr)
        return 2
    if not args.apply:
        print("dry run: nothing was sent to Gemini. Re-run with --apply.")
        return 0

    deps.graph.warm()
    spent = 0
    for a in todo:
        t0 = time.monotonic()
        res = investigate(deps, a["alert_id"], force=True, source=SOURCE)
        spent += res["tokens"]
        if res["status"] != "ok":
            print(f"{a['alert_id']} {res['status']}: {res.get('error')} (stopping)", flush=True)
            drain_all()
            return 1
        rep = res["report"]
        print(
            f"{a['alert_id']} {rep['decision']} lane={rep['lane']} conf={rep['confidence']} "
            f"tokens={res['tokens']} {time.monotonic() - t0:.0f}s engines={res['engines']} "
            f"budget={res['budget_status']} members={len(rep['members_alert'])}"
            f"+{len(rep['members_linked'])}",
            flush=True,
        )
    drain_all()
    print(f"done: {len(todo)} cases stored (source {SOURCE}), {spent:,} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
