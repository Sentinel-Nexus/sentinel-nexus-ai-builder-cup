"""Smoke test of every endpoint the workbench UI calls, over HTTP, with no credentials.

    python scripts/ui_smoke.py --base http://localhost:8080 [--write]

Read-only by default (no model calls, nothing written). The probes of the write endpoints send an
empty reason and expect a 422, so they write nothing either. `--write` additionally records one
approval with a reason on one alert (one audit event) and reads it back.
It never starts a live investigation. Exit code is the number of failed checks (capped at 1).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

TIMEOUT_S = 60


def call(base: str, path: str, method: str = "GET", body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw, status, headers = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw, status, headers = e.read(), e.code, e.headers
    ms = (time.perf_counter() - t0) * 1000
    ctype = headers.get("content-type", "")
    return status, (json.loads(raw) if "json" in ctype and raw else raw), ms, headers


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str, float]] = []

    def check(self, name: str, ok: bool, detail: str = "", ms: float = 0.0) -> bool:
        self.rows.append((name, bool(ok), detail, ms))
        print(f"{'ok  ' if ok else 'FAIL'} {name:<44} {ms:7.0f} ms  {detail}")
        return bool(ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if not r[1])


def run(base: str, write: bool = False, out: Checks | None = None) -> Checks:
    c = out or Checks()

    def get(path, expect=200):
        s, b, ms, h = call(base, path)
        return s == expect, b, ms, h, s

    ok, page, ms, _, s = get("/")
    c.check("GET /", ok and b"MarketSentinel" in page and b"integrity=" in page, f"{s}", ms)
    for p in ("/static/css/app.css", "/static/js/app.js", "/static/js/views/case.js"):
        ok, _, ms, _, s = get(p)
        c.check(f"GET {p}", ok, f"{s}", ms)
    ok, h, ms, _, s = get("/health")
    c.check("GET /health", ok and h["status"] == "ok", f"ready={h.get('ready')}", ms)
    ok, summ, ms, _, s = get("/summary")
    c.check(
        "GET /summary", ok and summ["synthetic"] and summ["alerts"] >= 1, str(summ.get("lanes")), ms
    )
    ok, queue, ms, _, s = get("/queue")
    c.check("GET /queue", ok and len(queue) >= 1, f"{len(queue)} alerts", ms)
    ok, al, ms, _, s = get("/alerts")
    c.check(
        "GET /alerts",
        ok and len(al) == len([a for a in queue if not a.get("streamed")]),
        "detector alerts only: streamed alerts are in /queue, not here",
        ms,
    )
    ok, lanes, ms, _, s = get("/lanes")
    c.check(
        "GET /lanes", ok and set(lanes) == {"escalated", "agent_cleared", "needs_analyst"}, "", ms
    )
    ok, bench, ms, _, s = get("/benchmark")
    c.check("GET /benchmark", ok and bench["kpi"]["detector"] and len(bench["limits"]) >= 5, "", ms)
    ok, gov, ms, _, s = get("/governance")
    c.check(
        "GET /governance", ok and gov["limits"]["max_tool_calls"] == 8 and gov["guards"], "", ms
    )
    ok, aud, ms, _, s = get("/audit?limit=20")
    c.check("GET /audit", ok and "events" in aud, f"{aud.get('n') if ok else s} events", ms)

    cased = [a for a in queue if a["has_case"]]
    c.check("cached cases exist", bool(cased), f"{len(cased)} of {len(queue)}")
    for a in cased:
        aid = a["alert_id"]
        ok, d, ms, _, s = get(f"/cases/{aid}/detail")
        c.check(
            f"GET /cases/{aid}/detail",
            ok and d["cached"] and d["report"]["evidence_table"] is not None,
            "",
            ms,
        )
        ok, plain, ms, _, s = get(f"/cases/{aid}")
        c.check(f"GET /cases/{aid}", ok and plain["cached"], "", ms)
        ok, g, ms, _, s = get(f"/cases/{aid}/graph")
        c.check(
            f"GET /cases/{aid}/graph",
            ok and g["nodes"] and g["edges"],
            f"engine={g.get('engine') if ok else s}",
            ms,
        )
        ok, t, ms, _, s = get(f"/cases/{aid}/timeline")
        c.check(
            f"GET /cases/{aid}/timeline",
            ok and len(t["events"]) > 5,
            f"{len(t['events']) if ok else s} events",
            ms,
        )
        acct = d["alert"]["member_accounts"][0]
        ok, tr, ms, _, s = get(f"/cases/{aid}/trades?account={acct}")
        c.check(
            f"GET /cases/{aid}/trades",
            ok and "trades" in tr,
            f"{tr.get('n_trades') if ok else s} trades",
            ms,
        )
        ok, ex, ms, h, s = get(f"/cases/{aid}/export")
        c.check(
            f"GET /cases/{aid}/export",
            ok and "attachment" in h.get("content-disposition", "") and ex["synthetic_data"],
            "",
            ms,
        )
    ok, _, ms, _, s = get("/cases/AL_NOPE/detail", expect=404)
    c.check("404 for an unknown alert", ok, "", ms)

    if cased:
        aid = cased[0]["alert_id"]
        body = {"analyst": "smoke-test", "decision": "approve", "note": ""}
        s, b, ms, _ = call(base, f"/cases/{aid}/approve", "POST", body)
        c.check("POST approve without a reason is refused", s == 422, f"{s}", ms)
        body = {"analyst": "smoke-test", "decision": "override_to_escalate", "note": ""}
        s, b, ms, _ = call(base, f"/cases/{aid}/override", "POST", body)
        c.check("POST override without a reason is refused", s == 422, f"{s}", ms)
        s, b, ms, _ = call(base, "/cases/AL_NOPE/investigate", "POST")
        c.check("POST investigate unknown alert is a 404", s == 404, f"{s}", ms)
        if write:
            _, before, _, _ = call(base, "/summary")
            body = {"analyst": "smoke-test", "decision": "approve", "note": "ui_smoke audit check"}
            s, b, ms, _ = call(base, f"/cases/{aid}/approve", "POST", body)
            c.check(
                "POST approve with a reason writes audit",
                s == 200 and b.get("recorded"),
                f"{s}",
                ms,
            )
            time.sleep(6)  # streaming insert, then the audit read cache
            s, a2, ms, _ = call(base, f"/audit?alert_id={aid}&event_type=approval&limit=50")
            found = s == 200 and any("smoke-test" in (e.get("detail") or "") for e in a2["events"])
            c.check("approval is readable from the audit table", found, "", ms)
            ok2 = found and all(
                e["test_record"] for e in a2["events"] if "smoke-test" in (e.get("detail") or "")
            )
            c.check("audit marks smoke-test events as test records", ok2, "", ms)
            _, after, _, _ = call(base, "/summary")
            c.check(
                "test records do not change decided or lanes",
                after["decided"] == before["decided"] and after["lanes"] == before["lanes"],
                f"decided {before['decided']} -> {after['decided']}",
            )
            cleared = lanes["agent_cleared"]
            if cleared:
                cid = cleared[0]["alert_id"]
                body = {
                    "analyst": "smoke-test",
                    "decision": "override_to_escalate",
                    "note": "ui_smoke override check",
                }
                s, b, ms, _ = call(base, f"/cases/{cid}/override", "POST", body)
                c.check(
                    "POST override moves a cleared case to escalated",
                    s == 200 and b.get("lane") == "escalated",
                    f"{cid}",
                    ms,
                )
                time.sleep(2)
                _, tv, _, _ = call(base, "/lanes?include_test=true")
                _, dv, _, _ = call(base, "/lanes")
                c.check(
                    "test view shows the override", cid in [x["alert_id"] for x in tv["escalated"]]
                )
                c.check(
                    "default view is unchanged (the test reset)",
                    cid in [x["alert_id"] for x in dv["agent_cleared"]],
                )
                s, tr, _, _ = call(base, f"/audit?alert_id={cid}&event_type=override&limit=20")
                c.check(
                    "override is in the audit table",
                    s == 200 and any(e["test_record"] for e in tr["events"]),
                )
    overview_checks(base, c)
    return c


def overview_checks(base: str, c: Checks) -> None:
    """Overview screen: its static data, scripts and nav tab (added after the original checks)."""
    s, ov, ms, _ = call(base, "/static/data/overview.json")
    ok = s == 200 and isinstance(ov, dict) and ov.get("synthetic") is True
    c.check("GET /static/data/overview.json", ok, f"{s}", ms)
    if not ok:
        return
    _, bench, _, _ = call(base, "/benchmark")
    _, summ, _, _ = call(base, "/summary")
    det = bench["kpi"]["detector"]
    c.check(
        "overview KPIs match benchmark",
        ov["kpi"]["caught"]["caught"] == det["tp"]
        and ov["kpi"]["false_alarms"]["marketsentinel"] == det["fp"]
        and ov["kpi"]["false_alarms"]["simple_rule"] == bench["kpi"]["baseline"]["fp"],
    )
    c.check(
        "overview alert counts match /summary",
        ov["summary"]["detector_alerts"]["alerts"] == summ["detector_alerts"]["alerts"]
        and ov["summary"]["evaluation_cases"]["alerts"] == summ["evaluation_cases"]["alerts"],
    )
    for p in ("/static/js/views/overview.js", "/static/css/overview.css"):
        s, _, ms, _ = call(base, p)
        c.check(f"GET {p}", s == 200, f"{s}", ms)
    s, page, ms, _ = call(base, "/")
    c.check("nav has the Overview tab", s == 200 and b'href="#/overview"' in page, "", ms)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--write", action="store_true", help="record one approval with a reason")
    a = ap.parse_args()
    res = run(a.base.rstrip("/"), a.write)
    print(f"\n{len(res.rows) - res.failed}/{len(res.rows)} checks passed")
    return 1 if res.failed else 0


if __name__ == "__main__":
    sys.exit(main())
