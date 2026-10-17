"""Light concurrent read check: N GET requests at concurrency C across the UI endpoints.

    python scripts/load_check.py --base https://<service-url> [--requests 300] [--concurrency 10]

Reads only (cached cases, queue, lanes, summary, graph, timeline, trades): no model calls, nothing
written. Counts every response that is not HTTP 200 and prints the request id of any 500 so the
logged traceback can be found. Hard wall-clock cap (--max-seconds). Exit code 1 if any non-200.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


def fetch(base: str, path: str) -> tuple[str, int, float, str]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(base + path, timeout=60) as r:
            r.read()
            return path, r.status, (time.perf_counter() - t0) * 1000, ""
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        return path, e.code, (time.perf_counter() - t0) * 1000, body
    except Exception as e:  # noqa: BLE001
        return path, 0, (time.perf_counter() - t0) * 1000, f"{type(e).__name__}: {str(e)[:100]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--requests", type=int, default=300)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--max-seconds", type=int, default=240)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    with urllib.request.urlopen(base + "/queue", timeout=60) as r:
        cased = [x for x in json.load(r) if x["has_case"]]
    paths = ["/queue", "/lanes", "/summary"]
    for x in cased:
        i = x["alert_id"]
        paths += [f"/cases/{i}", f"/cases/{i}/detail", f"/cases/{i}/graph", f"/cases/{i}/timeline"]
    plan = list(itertools.islice(itertools.cycle(paths), a.requests))
    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(a.concurrency) as pool:
        futures = [pool.submit(fetch, base, p) for p in plan]
        for f in futures:
            left = a.max_seconds - (time.perf_counter() - t0)
            try:
                results.append(f.result(timeout=max(left, 1)))
            except Exception:  # noqa: BLE001  the cap was hit
                print(f"wall-clock cap of {a.max_seconds}s reached")
                pool.shutdown(wait=False, cancel_futures=True)
                break
    wall = time.perf_counter() - t0
    codes = Counter(r[1] for r in results)
    bad = [r for r in results if r[1] != 200]
    lat = sorted(r[2] for r in results)
    print(f"{len(results)} requests, concurrency {a.concurrency}, {wall:.1f}s, {len(paths)} paths")
    print(f"status codes: {dict(codes)}")
    if lat:
        p95 = lat[int(len(lat) * 0.95) - 1]
        print(f"latency ms: median {lat[len(lat) // 2]:.0f}, p95 {p95:.0f}, max {lat[-1]:.0f}")
    for p, code, ms, body in bad[:10]:
        print(f"NON-200 {code} {p} {ms:.0f} ms {body}")
    print(f"non-200 responses: {len(bad)}")
    return 1 if bad or len(results) < a.requests else 0


if __name__ == "__main__":
    sys.exit(main())
