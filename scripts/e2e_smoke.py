"""End-to-end smoke test against a deployed URL, with no credentials, plus latency numbers.

    python scripts/e2e_smoke.py --base https://<service-url> [--write] [--json out.json]

1. waits for /health `ready` (reports how long the first response and readiness took);
2. runs every UI endpoint check from scripts/ui_smoke.py (read-only; `--write` records one
   approval with a reason and reads it back from the audit table, proving the service account can
   write and read audit events);
3. times the cached case over 30 keep-alive requests (network included).
It never starts a live investigation, so it spends no tokens. Every wait has a wall-clock cap.
"""

from __future__ import annotations

import argparse
import http.client
import json
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_smoke  # noqa: E402

READY_CAP_S = 120


def wait_ready(base: str) -> dict:
    t0 = time.perf_counter()
    first_ms, ready_s, last = None, None, {}
    while time.perf_counter() - t0 < READY_CAP_S:
        try:
            s, body, ms, _ = ui_smoke.call(base, "/health")
        except Exception as e:  # noqa: BLE001
            last = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
            time.sleep(2)
            continue
        if first_ms is None and s == 200:
            first_ms = ms
        last = body if isinstance(body, dict) else {}
        if s == 200 and last.get("ready"):
            ready_s = time.perf_counter() - t0
            break
        print(f"waiting for ready ({time.perf_counter() - t0:.0f}s): {last}")
        time.sleep(2)
    return {"first_response_ms": first_ms, "ready_after_s": ready_s, "health": last}


def time_cached_case(base: str, alert_id: str, n: int = 30) -> dict:
    u = urllib.parse.urlparse(base)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.netloc, timeout=30)
    ms = []
    for _ in range(n):
        t0 = time.perf_counter()
        conn.request("GET", f"/cases/{alert_id}")
        r = conn.getresponse()
        r.read()
        ms.append((time.perf_counter() - t0) * 1000)
        if r.status != 200:
            return {"error": f"status {r.status}"}
    conn.close()
    warm = ms[1:]  # the first request pays for the TLS handshake
    return {
        "n": n,
        "first_ms": round(ms[0], 1),
        "median_ms": round(statistics.median(warm), 1),
        "p95_ms": round(sorted(warm)[int(len(warm) * 0.95) - 1], 1),
        "max_ms": round(max(warm), 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    startup = wait_ready(base)
    print("startup:", json.dumps(startup, default=str))
    checks = ui_smoke.run(base, a.write)
    cased = next(
        (r for r in ui_smoke.call(base, "/queue")[1] if r["has_case"]),
        None,
    )
    cached = time_cached_case(base, cased["alert_id"]) if cased else {"error": "no cached case"}
    print("cached case latency:", cached)
    passed = len(checks.rows) - checks.failed
    print(f"\n{passed}/{len(checks.rows)} checks passed")
    if a.json:
        out = {
            "base": base.split("//")[-1].split(".")[0],
            "startup": startup,
            "cached_case": cached,
            "checks": [
                {"name": n, "ok": ok, "detail": d, "ms": round(ms)} for n, ok, d, ms in checks.rows
            ],
        }
        Path(a.json).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    ready = startup["ready_after_s"] is not None
    return 0 if ready and not checks.failed and "error" not in cached else 1


if __name__ == "__main__":
    sys.exit(main())
