"""Precompute case reports for every detector alert (flagged and needs_review).

    python scripts/precompute_cases.py                 # dry run: lists alerts, prints the estimate
    python scripts/precompute_cases.py --apply         # runs the agents (spends Gemini tokens)

Safe by construction: a hard cap on investigations (--max-runs), the cost estimate is printed
before anything is sent to Gemini, alerts that already have a cached case are skipped unless
--force, and the loop stops at the first run that fails. Reports go to
risk_curated.case_reports, so the demo and the public link serve cached cases by default.
"""

from __future__ import annotations

import argparse
import sys
import time

from services.app.audit import drain_all
from services.app.config import Settings
from services.app.cost import estimate, format_estimate
from services.app.main import default_deps
from services.app.pipeline import investigate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="run the agents (default: dry run)")
    ap.add_argument("--max-runs", type=int, default=10, help="hard cap on investigations")
    ap.add_argument("--force", action="store_true", help="re-run alerts that already have a case")
    args = ap.parse_args(argv)

    settings = Settings()
    deps = default_deps(settings)
    alerts = [a for a in deps.store.alerts() if a["status"] in ("flagged", "needs_review")]
    have = deps.cases.latest()
    todo = [a for a in alerts if args.force or a["alert_id"] not in have]
    print(f"{len(alerts)} alerts, {len(alerts) - len(todo)} already cached, {len(todo)} to run:")
    for a in todo:
        print(f"  {a['alert_id']}  {a['ring_id']:<8} {a['status']:<12} {a['scenario_hint']}")
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
        res = investigate(deps, a["alert_id"], force=True, source="precompute")
        spent += res["tokens"]
        if res["status"] != "ok":
            print(f"{a['alert_id']} {res['status']}: {res.get('error')} (stopping)", flush=True)
            drain_all()
            return 1
        rep = res["report"]
        print(
            f"{a['alert_id']} {rep['decision']} conf={rep['confidence']} tokens={res['tokens']} "
            f"{time.monotonic() - t0:.0f}s engines={res['engines']} budget={res['budget_status']}",
            flush=True,
        )
    drain_all()
    print(f"done: {len(todo)} cases cached, {spent:,} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
