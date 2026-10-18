"""Evaluate the near-real-time micro-batch path against a running service.

    python scripts/stream_eval.py --base URL --phase fresh   # one fresh burst, measured and checked
    python scripts/stream_eval.py --base URL --phase same    # replay the previous burst's ids
    python scripts/stream_eval.py --base URL --phase full --wait-cap 700 --write
        # fresh burst A, the same burst again, then fresh burst B (waits out the 10-minute limit)
    python scripts/stream_eval.py --base URL --poison        # one malformed message -> DLQ

A fresh burst carries new account, device, funding-source and trade ids, so it must ingest every
message, raise a NEW streamed alert (flagging the ring and not the household) and report the delay
from the last publish to the alert. The same-burst replay must ingest 0, drop every message as a
duplicate, add no row to any stream table and keep the alert id. Two fresh bursts back to back must
add their own rows and raise two different alerts with disjoint ids; only the newest alert is in
the queue. It reads the labels
(evals/stream/burst_01_truth.json) only here; the service and the detector never do. A miss is
reported as a miss. No Gemini call is made. Every wait has a cap and prints progress.
"""

from __future__ import annotations

import argparse
import base64
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


def run_replay(base: str, poll_cap: float, wait_cap: float, same: bool = False) -> dict:
    wait_until_idle(base, 60)
    path = "/stream/replay" + ("?same=true" if same else "")
    code, body, hdr = call(base, path, "POST")
    if code == 429:
        retry = int(hdr.get("Retry-After") or body.get("retry_after") or 0)
        if retry and retry <= wait_cap:
            print(f"rate limited ({body.get('detail', '')[:80]}); waiting {retry}s", flush=True)
            for left in range(retry, 0, -30):
                print(f"  {left}s left", flush=True)
                time.sleep(min(30, left))
            code, body, hdr = call(base, path, "POST")
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
    suffixes = {m.rsplit("-", 1)[1] for m in members if "-" in m}
    members = {m.rsplit("-", 1)[0] if "-" in m else m for m in members}  # drop the burst suffix
    t = status.get("timings", {})
    return {
        "mode": status.get("mode"),
        "suffixes": sorted(suffixes),
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
        try:  # gcloud prints the body base64-encoded
            bodies = [
                base64.b64decode(m["message"]["data"]).decode() for m in json.loads(out.stdout)
            ]
        except (ValueError, KeyError, TypeError):
            bodies = []
        if any("POISON-1" in b for b in bodies):
            return True
        time.sleep(5)
    return False


def seconds_for(history: list[dict]) -> float | None:
    """Median delay, rounded up to 5 s, over fresh bursts (older entries have no mode)."""
    runs = [r for r in history if r.get("last_publish_to_alert_s") and r.get("mode") != "same"]
    fresh = [r for r in runs if r.get("mode") == "fresh"]
    vals = [r["last_publish_to_alert_s"] for r in fresh or runs]
    return float(5 * math.ceil(statistics.median(vals) / 5)) if vals else None


def one_run(a, truth: dict, same: bool) -> tuple[dict | None, bool]:
    """Run one burst; -> (result entry, checks passed)."""
    before = bq_counts()
    r = run_replay(a.base, a.poll_cap, a.wait_cap, same)
    if not r["ok"]:
        print("RUN FAILED:", json.dumps(r, default=str)[:600])
        return None, False
    j = judge(a.base, truth, r["status"])
    after = bq_counts()
    c = j["counters"]
    label = "same burst" if same else "fresh burst"
    added = {k: after[k]["rows"] - before[k]["rows"] for k in before}
    print(f"[{label}] published {c['published']}, ingested {c['ingested']}, duplicates dropped "
          f"{c['duplicates_dropped']}, alerts {c['alerts_raised']}")  # fmt: skip
    print(f"[{label}] seconds from last publish to alert: {j['last_publish_to_alert_s']} "
          f"(publish {j['publish_s']} s, total {j['total_s']} s)")  # fmt: skip
    print(f"[{label}] ring flagged: {j['ring_all_flagged']} {j['ring_flagged']}; household NOT "
          f"flagged: {j['household_not_flagged']} {j['household_flagged']}")  # fmt: skip
    print(f"[{label}] alert scores {j['alert_scores']} vs frozen threshold {j['threshold']}")
    print(f"[{label}] BigQuery rows added {added} (now {after})")
    entry = {"run_id": r["run_id"], **j, "rows_added": added, "bq_after": after}
    entry["no_duplicate_keys"] = all(v["rows"] == v["distinct"] for v in after.values())
    detect = j["ring_all_flagged"] and j["household_not_flagged"] and len(j["alerts"]) == 1
    n = c["published"]
    if same:
        ok = (detect and c["ingested"] == 0 and c["duplicates_dropped"] == n
              and not any(added.values()))  # fmt: skip
    else:
        ok = (detect and c["ingested"] == n and c["duplicates_dropped"] == 0
              and added["alerts"] == 1 and added["trades"] > 0
              and added["accounts"] > 0)  # fmt: skip
    ok = ok and entry["no_duplicate_keys"]
    print(f"[{label}] {'ok' if ok else 'MISS'}")
    return entry, ok


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--phase", choices=["fresh", "same", "full"], default="fresh")
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
        plan = {"fresh": [False], "same": [True], "full": [False, True, False]}[a.phase]
        entries, ok = [], True
        for same in plan:
            entry, good = one_run(a, truth, same)
            if entry is None:
                return 1
            entries.append(entry)
            ok = ok and good
        fresh = [e for e in entries if e["mode"] == "fresh"]
        if a.phase == "full":
            x, y = fresh
            same_run = next(e for e in entries if e["mode"] == "same")
            checks = {
                "same_burst_kept_the_alert_id": same_run["alerts"] == x["alerts"],
                "fresh_bursts_raised_different_alerts": set(x["alerts"]).isdisjoint(y["alerts"]),
                "fresh_bursts_used_different_ids": set(x["suffixes"]).isdisjoint(y["suffixes"]),
                "each_fresh_burst_added_its_own_rows": all(
                    e["rows_added"]["trades"] > 0 for e in fresh
                ),
            }
            result["checks"] = checks
            print("checks:", json.dumps(checks))
            ok = ok and all(checks.values())
        result["runs"] = entries
    print("RESULT:", "PASS" if ok else "MISS (reported as is; nothing was tuned)")
    if a.write:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        hist = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {"runs": []}
        result.update(phase=a.phase, passed=ok)
        hist["runs"].append(result)
        flat = [e for r in hist["runs"] for e in (r.get("runs") or [r])]
        sec = seconds_for([x for x in flat if x.get("last_publish_to_alert_s")])
        hist["measured_seconds"] = sec
        RESULTS.write_text(
            json.dumps(hist, indent=1, default=str) + "\n", encoding="utf-8", newline="\n"
        )
        if sec:
            MEASURED.write_text(json.dumps({"seconds": sec, "n_runs": len(flat)}) + "\n",
                                encoding="utf-8", newline="\n")  # fmt: skip
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
