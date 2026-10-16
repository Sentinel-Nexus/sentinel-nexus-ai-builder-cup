"""Time how long a cached case takes to serve. No Gemini calls: it only reads cached cases.

    python -m evals.agents.cache_latency            # 4 real alerts x 25 repeats

Uses time.perf_counter (time.monotonic has about 15 ms resolution on Windows, which rounds a
1 ms cache hit to 0). The numbers are the service-side time of `investigate()` for a cache hit
(lookup, event emit, audit hand-off to the background writer); HTTP overhead is not included.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from services.app.audit import MemoryAudit
from services.app.config import Settings
from services.app.main import default_deps
from services.app.pipeline import investigate

OUT = Path(__file__).resolve().parents[2] / "docs" / "results" / "cache_latency.json"
REPEATS = 25


def main() -> int:
    deps = default_deps(Settings())
    deps.store.warm()
    deps.cases.latest()  # loads risk_curated.case_reports once (what the service does at start-up)
    deps.audit_factory = lambda: MemoryAudit()  # measure the service path, not BigQuery inserts
    results = {}
    for alert in [a for a in deps.store.alerts() if deps.cases.get(a["alert_id"])]:
        samples = []
        for _ in range(REPEATS):
            t0 = time.perf_counter()
            res = investigate(deps, alert["alert_id"])
            samples.append((time.perf_counter() - t0) * 1000)
            assert res["cached"] and res["status"] == "ok"
        results[alert["alert_id"]] = {
            "n": REPEATS,
            "median_ms": round(statistics.median(samples), 3),
            "max_ms": round(max(samples), 3),
        }
        print(alert["alert_id"], results[alert["alert_id"]])
    allv = [v["max_ms"] for v in results.values()]
    summary = {"per_alert": results, "worst_ms": max(allv) if allv else None,
               "note": "service-side cache hit, perf_counter; excludes HTTP"}  # fmt: skip
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=1), encoding="utf-8", newline="\n")
    print("worst", summary["worst_ms"], "ms ->", OUT.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
