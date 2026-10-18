"""Evaluate the near-real-time micro-batch path against a running service.

    python scripts/stream_eval.py --base https://...        # one replay, measured and checked
    python scripts/stream_eval.py --base URL --phase second  # a second replay (needs the 10-minute
                                                             # per-client limit to have passed)
    python scripts/stream_eval.py --base URL --poison        # one malformed message -> DLQ
    python scripts/stream_eval.py --base URL --write     # append to docs/results/stream_eval.json

Reports: messages published and ingested, duplicates dropped, seconds from the last publish to the
alert, whether the streamed ring is flagged and the innocent household is NOT, and for a second
replay that no trade, account or alert row was added. It reads the labels
(evals/stream/burst_01_truth.json) only here; the service and the detector never do. A miss is
reported as a miss. No Gemini call is made. Every wait has a cap and prints progress.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRUTH = ROOT / "evals" / "stream" / "burst_01_truth.json"
RESULTS = ROOT / "docs" / "results" / "stream_eval.json"
MEASURED = ROOT / "services" / "app" / "static" / "data" / "stream.json"
PROJECT = "project-f8544066-3df8-4f99-8ed"


def call(base: str, path: str, method: str = "GET", timeout: float = 30.0):
    req = urllib.request.Request(
        base.rstrip("/") + path, method=method, data=b"" if method == "POST" else None
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body), dict(e.headers)
        except ValueError:
            return e.code, {"detail": body[:200].decode("utf-8", "replace")}, dict(e.headers)


def wait_until_idle(base: str, cap: float) -> None:
    """Block until no stream run is active (a 409 would otherwise refuse the next one)."""
    t0 = time.time()
    while time.time() - t0 < cap:
        code, st, _ = call(base, "/stream/status")
        if code == 404 or not st.get("active"):
            return
        time.sleep(2)


def run_replay(base: str, poll_cap: float, wait_cap: float) -> dict:
    wait_until_idle(base, 60)
    code, body, hdr = call(base, "/stream/replay", "POST")
    if code == 429:
        retry = int(hdr.get("Retry-After") or body.get("retry_after") or 0)
        if retry and retry <= wait_cap:
            print(f"rate limited ({body.get('detail', '')[:80]}); waiting {retry}s", flush=True)
            for left in range(retry, 0, -30):
                print(f"  {left}s left", flush=True)
                time.sleep(min(30, left))
            code, body, hdr = call(base, "/stream/replay", "POST")
        if code == 429:
            return {
                "ok": False,
                "error": f"rate limited: {body.get('detail')}",
                "retry_after": retry,
            }
    if code != 200:
        return {"ok": False, "error": f"replay returned {code}: {body.get('detail')}"}
    run_id = body["run_id"]
    t0 = time.time()
    st = {}
    while time.time() - t0 < poll_cap:
        _, st, _ = call(base, f"/stream/status?run_id={run_id}")
        k = st.get("counters", {})
        print(f"  {time.time() - t0:5.1f}s {st.get('state'):9} published {k.get('published')} "
              f"ingested {k.get('ingested')} dup {k.get('duplicates_dropped')} "
              f"alerts {k.get('alerts_raised')}", flush=True)  # fmt: skip
        if st.get("state") in ("finished", "error", "stalled"):
            break
        time.sleep(3)
    return {"ok": st.get("state") == "finished", "run_id": run_id, "status": st}


def judge(base: str, truth: dict, status: dict) -> dict:
    _, queue, _ = call(base, "/queue")
    streamed = [a for a in queue if a.get("streamed")]
    ids = {a["alert_id"] for a in streamed}
    members: set[str] = set()
    for a in streamed:
        _, d, _ = call(base, f"/cases/{a['alert_id']}/detail")
        members |= set(d["alert"]["member_accounts"])
    ring, house = set(truth["expected"]["flag"]), set(truth["expected"]["do_not_flag"])
    t = status.get("timings", {})
    return {
        "alerts": sorted(ids),
        "ring_flagged": sorted(ring & members),
        "ring_all_flagged": ring <= members,
        "household_flagged": sorted(house & members),
        "household_not_flagged": not (house & members),
        "alert_scores": {a["alert_id"]: a["score"] for a in streamed},
        "threshold": streamed[0]["threshold"] if streamed else None,
        "last_publish_to_alert_s": t.get("last_publish_to_alert_s"),
        "publish_s": t.get("publish_s"),
        "total_s": t.get("total_s"),
        "counters": status.get("counters"),
    }


def bq_counts() -> dict:
    from google.cloud import bigquery

    c = bigquery.Client(project=PROJECT, location="US")
    out = {}
    for name, table, key in (("trades", "risk_raw.stream_trades", "trade_id"),
                             ("accounts", "risk_raw.stream_accounts", "account_id"),
                             ("alerts", "risk_curated.stream_alerts", "alert_id")):  # fmt: skip
        ids = [r[key] for r in c.list_rows(f"{PROJECT}.{table}")]  # free read, no query
        out[name] = {"rows": len(ids), "distinct": len(set(ids))}
    return out


def poison(base: str, cap: float) -> dict:
    wait_until_idle(base, 60)
    code, body, hdr = call(base, "/stream/poison", "POST")
    if code != 200:
        return {"ok": False, "error": f"poison returned {code}: {body.get('detail')}"}
    run_id, t0, st = body["run_id"], time.time(), {}
    while time.time() - t0 < cap:
        _, st, _ = call(base, f"/stream/status?run_id={run_id}")
        k = st.get("counters", {})
        print(f"  {time.time() - t0:5.1f}s {st.get('state')} malformed {k.get('malformed')} "
              f"retried {k.get('retried')} dlq {k.get('dead_letter')}", flush=True)  # fmt: skip
        if st.get("state") in ("finished", "error", "stalled"):
            break
        time.sleep(2)
    k = st.get("counters", {})
    in_dlq = peek_dlq()
    return {
        "ok": st.get("state") == "finished" and k.get("dead_letter") == 1,
        "run_id": run_id, "counters": k,
        "attempt_events": [e["event_type"] for e in st.get("events", [])],
        "arrived_in_trades_dlq": in_dlq,
    }  # fmt: skip


def peek_dlq() -> bool | None:
    """Look at trades-dlq-sub without acknowledging (the message stays there)."""
    for _ in range(6):
        out = subprocess.run(
            ["gcloud", "pubsub", "subscriptions", "pull", "trades-dlq-sub", "--project", PROJECT,
             "--limit", "20", "--format", "json"],
            capture_output=True, text=True, timeout=60, shell=(sys.platform == "win32"),
        )  # fmt: skip
        if out.returncode:
            return None
        if "POISON-1" in out.stdout:
            return True
        time.sleep(5)
    return False


def seconds_for(history: list[dict]) -> float | None:
    vals = [r["last_publish_to_alert_s"] for r in history if r.get("last_publish_to_alert_s")]
    return float(5 * math.ceil(statistics.median(vals) / 5)) if vals else None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--phase", choices=["first", "second"], default="first")
    ap.add_argument("--poison", action="store_true")
    ap.add_argument("--poll-cap", type=float, default=240.0)
    ap.add_argument("--wait-cap", type=float, default=0.0, help="seconds to wait out a 429")
    ap.add_argument("--write", action="store_true", help="append to docs/results/stream_eval.json")
    a = ap.parse_args(argv)

    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    cfg = call(a.base, "/stream/config")[1]
    print(f"base {a.base}; streaming enabled: {cfg.get('enabled')}; mode: {cfg.get('mode')}")
    if not cfg.get("enabled"):
        print("streaming is off on this service")
        return 2
    result: dict = {"ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "base": a.base}
    if a.poison:
        result["poison"] = poison(a.base, 90)
        print(json.dumps(result["poison"], indent=1))
        ok = result["poison"]["ok"]
    else:
        before = bq_counts()
        r = run_replay(a.base, a.poll_cap, a.wait_cap)
        if not r["ok"]:
            print("RUN FAILED:", json.dumps(r, default=str)[:600])
            return 1
        j = judge(a.base, truth, r["status"])
        after = bq_counts()
        result.update(phase=a.phase, run_id=r["run_id"], **j, bq_before=before, bq_after=after)
        c = j["counters"]
        print(f"published {c['published']}, ingested {c['ingested']}, duplicates dropped "
              f"{c['duplicates_dropped']}, alerts {c['alerts_raised']}")  # fmt: skip
        print(f"seconds from last publish to alert: {j['last_publish_to_alert_s']} "
              f"(publish took {j['publish_s']} s, run total {j['total_s']} s)")  # fmt: skip
        print(f"streamed ring flagged: {j['ring_all_flagged']} {j['ring_flagged']}; household NOT "
              f"flagged: {j['household_not_flagged']} {j['household_flagged']}")  # fmt: skip
        print(f"alert scores {j['alert_scores']} vs frozen threshold {j['threshold']}")
        print(f"BigQuery rows before {before} after {after}")
        ok = j["ring_all_flagged"] and j["household_not_flagged"]
        if a.phase == "second":
            same = all(before[k]["rows"] == after[k]["rows"] for k in before)
            unique = all(v["rows"] == v["distinct"] for v in after.values())
            result["second_replay_added_no_rows"] = same
            result["no_duplicate_keys"] = unique
            print(f"second replay added no rows: {same}; no duplicate keys: {unique}")
            ok = ok and same and unique and len(j["alerts"]) == 1
    print("RESULT:", "PASS" if ok else "MISS (reported as is; nothing was tuned)")
    if a.write:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        hist = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {"runs": []}
        hist["runs"].append(result)
        sec = seconds_for([x for x in hist["runs"] if x.get("last_publish_to_alert_s")])
        hist["measured_seconds"] = sec
        RESULTS.write_text(
            json.dumps(hist, indent=1, default=str) + "\n", encoding="utf-8", newline="\n"
        )
        if sec:
            MEASURED.write_text(json.dumps({"seconds": sec, "n_runs": len(hist["runs"])}) + "\n",
                                encoding="utf-8", newline="\n")  # fmt: skip
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
