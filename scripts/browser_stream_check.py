"""Browser checks for the streaming controls (used by scripts/browser_check.py).

Every state of the Pipeline card is exercised: idle, running, finished, rate-limited and error.
States are stubbed with route interception, so nothing is published and no rate-limit slot is
used, unless ``real`` clicks the real button once. A flag-off service must show none of it.
"""

from __future__ import annotations

import json
import re

BANNED = re.compile(r"(?<!near-)real[- ]time|live monitoring", re.I)
GUARDS = (
    "Stream schema validation",
    "Stream idempotency",
    "Retries, then dead-letter",
    "Stream rate limits",
)
LIMIT_TEXT = (
    "You have already streamed a burst in the last 10 minutes. Streaming is limited to 1 per "
    "client per 10 minutes, 6 per hour and 30 per day for the service. Try again in about 9 min."
)


def stub(route, status, body, headers=None):
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(body),
        headers=headers or {},
    )


def status_handler(run_id, kind, steps, mode=None):
    """A /stream/status handler that walks through ``steps`` (state, active, progress, counters)."""
    calls = {"n": 0}

    def handler(route):
        state, active, progress, k = steps[min(calls["n"], len(steps) - 1)]
        calls["n"] += 1
        counters = dict.fromkeys(
            (
                "published", "ingested", "duplicates_dropped", "retried", "malformed",
                "dead_letter", "alerts_raised",
            ),
            0,
        )  # fmt: skip
        counters.update(k)
        stub(
            route,
            200,
            {
                "run_id": run_id,
                "kind": kind,
                "mode": mode,
                "state": state,
                "active": active,
                "expected": 310 if kind == "replay" else 1,
                "progress": progress,
                "counters": counters,
                "scoring_queries": 1,
                "alert_ids": [],
                "timings": {
                    "publish_s": 1.2,
                    "last_publish_to_alert_s": 33.4,
                    "total_s": 40.0,
                    "elapsed_s": 40.0,
                },
                "error": None,
                "events": [],
                "limits": {},
            },
        )

    return handler


def run_stream_checks(pg, real: bool = False) -> None:
    p = pg.page
    pg.start("Stream pipeline")
    p.goto(pg.base + "/#/")
    p.wait_for_selector(".kpis")
    cfg = p.evaluate("fetch('/stream/config').then(r => r.json())")
    if BANNED.search(p.inner_text("#view")):
        pg.bad("the Command centre says real-time or live monitoring")
    if not cfg.get("enabled"):
        if p.locator("#stream-card, #stream-btn").count():
            pg.bad("streaming controls are shown while the flag is off")
        return
    p.wait_for_selector("#stream-card")

    # idle: wording, counters, buttons
    note = p.inner_text("#stream-note")
    if "Pub/Sub" not in note or "micro-batches" not in note or "within about" not in note:
        pg.bad(f"the stream note is missing its wording: {note!r}")
    secs = cfg.get("measured_seconds")
    if secs is not None and f"{secs:g} seconds" not in note:
        pg.bad("the stream note does not show the measured seconds")
    for k in (
        "published",
        "ingested",
        "duplicates_dropped",
        "retry",
        "dead_letter",
        "alerts_raised",
    ):
        if not p.locator(f"[data-k={k}]").count():
            pg.bad(f"Pipeline card has no {k} counter")
    if p.inner_text("#stream-state") not in ("Idle", "Finished", "Error", "Stalled"):
        pg.bad(f"unexpected first state {p.inner_text('#stream-state')!r}")
    if not p.locator("#stream-bad-btn").is_visible():
        pg.bad("'Send a bad message' button missing")
    if not p.locator("#stream-same-btn").is_visible():
        pg.bad("'Replay the same burst' button missing")
    elif p.locator("#stream-same-btn").is_disabled() == bool(cfg.get("can_replay_same")):
        pg.bad("'Replay the same burst' is not enabled exactly when an earlier burst exists")
    modes = p.inner_text("#stream-modes")
    for want in ("Stream a scenario", "fresh burst", "new ids", "Replay the same burst",
                 "idempotency", "dropped as a duplicate", "alert id stays the same"):  # fmt: skip
        if want not in modes:
            pg.bad(f"the Pipeline card does not explain the modes ({want!r} missing)")

    # rate-limited
    pg.expected = {429}
    p.route(
        "**/stream/replay", lambda r: stub(r, 429, {"detail": LIMIT_TEXT}, {"Retry-After": "540"})
    )
    p.click("#stream-btn")
    p.wait_for_selector("#stream-limit")
    if "Rate limited" not in p.inner_text("#stream-state") or "10 minutes" not in p.inner_text(
        "#stream-limit"
    ):
        pg.bad("rate-limited state not shown with its message")
    if p.locator("#stream-btn").is_disabled():
        pg.bad("the button stays disabled after a 429")
    p.unroute("**/stream/replay")

    # error
    pg.expected = {500}
    p.route("**/stream/replay", lambda r: stub(r, 500, {"detail": "boom"}))
    p.click("#stream-btn")
    p.wait_for_selector("#stream-card .state.error")
    if p.inner_text("#stream-state") != "Error":
        pg.bad("error state not shown")
    p.unroute("**/stream/replay")

    # running -> finished (stubbed), with a streamed alert appearing in the queue
    pg.expected = set()
    steps = [
        ("ingesting", True, 0.45, {"published": 310, "ingested": 140}),
        ("scoring", True, 0.95, {"published": 310, "ingested": 310}),
        ("finished", False, 1.0, {"published": 310, "ingested": 310, "alerts_raised": 1}),
    ]
    real_queue = p.evaluate("fetch('/queue').then(r => r.json())")
    fake = {**real_queue[0], "alert_id": "AL_STREAMSTUB", "streamed": True} if real_queue else None
    calls = {"queue": 0}

    def queue_handler(route):
        calls["queue"] += 1
        stub(route, 200, list(real_queue) + ([fake] if fake and calls["queue"] > 1 else []))

    p.route(
        "**/stream/replay",
        lambda r: stub(r, 200, {"run_id": "r-stub", "total": 310, "state": "publishing"}),
    )
    p.route("**/stream/status?run_id=r-stub", status_handler("r-stub", "replay", steps, "fresh"))
    p.route("**/queue", queue_handler)
    p.click("#stream-btn")
    p.wait_for_function(
        "document.querySelector('.stream-bar')?.getAttribute('aria-valuenow') === '45'",
        timeout=15000,
    )
    if not p.locator("#stream-btn").is_disabled():
        pg.bad("the button is enabled while a run is active")
    p.wait_for_function(
        "document.querySelector('#stream-state')?.textContent === 'Finished'", timeout=20000
    )
    if p.locator(".stream-bar").get_attribute("aria-valuenow") != "100":
        pg.bad("progress bar did not reach 100")
    if (
        "310" not in p.inner_text("[data-k=published]")
        or p.inner_text("[data-k=alerts_raised]").strip() != "1"
    ):
        pg.bad("finished counters wrong")
    if "33.4" not in p.inner_text("#stream-caption"):
        pg.bad("the finished caption does not state the measured seconds")
    if "310 new rows were ingested" not in p.inner_text("#stream-caption"):
        pg.bad("the fresh-run caption does not say new rows were ingested")
    if fake and not p.locator("#queue-holder .chip", has_text="Streamed").count():
        pg.bad("a streamed alert in the queue has no Streamed chip")
    for pattern in ("**/queue", "**/stream/replay", "**/stream/status?run_id=r-stub"):
        p.unroute(pattern)

    # "Replay the same burst": enabled after a fresh run, rate-limited, then idempotent (stubbed)
    if p.locator("#stream-same-btn").is_disabled():
        pg.bad("'Replay the same burst' stays disabled after a fresh run")
    same_url = re.compile(r"/stream/replay\?same=true")
    pg.expected = {429}
    p.route(same_url, lambda r: stub(r, 429, {"detail": LIMIT_TEXT}, {"Retry-After": "540"}))
    p.click("#stream-same-btn")
    p.wait_for_selector("#stream-limit")
    if "Rate limited" not in p.inner_text("#stream-state"):
        pg.bad("the same-burst replay does not show the rate-limited state")
    if p.locator("#stream-same-btn").is_disabled() or p.locator("#stream-btn").is_disabled():
        pg.bad("buttons stay disabled after a same-burst 429")
    p.unroute(same_url)
    pg.expected = set()
    same_steps = [
        ("ingesting", True, 0.5, {"published": 310, "duplicates_dropped": 150}),
        ("finished", False, 1.0, {"published": 310, "duplicates_dropped": 310, "alerts_raised": 1}),
    ]
    p.route(
        same_url,
        lambda r: stub(r, 200, {"run_id": "r-same", "total": 310, "state": "publishing",
                                "mode": "same"}),
    )  # fmt: skip
    p.route(
        "**/stream/status?run_id=r-same", status_handler("r-same", "replay", same_steps, "same")
    )
    p.click("#stream-same-btn")
    p.wait_for_function(
        "document.querySelector('#stream-caption')?.textContent.includes('no new rows')",
        timeout=20000,
    )
    cap = p.inner_text("#stream-caption")
    if (
        "310 dropped as duplicates" not in cap
        or "0 ingested" not in cap
        or "did not change" not in cap
    ):
        pg.bad(f"the same-burst caption is wrong: {cap!r}")
    if (
        p.inner_text("[data-k=ingested]").strip() != "0"
        or p.inner_text("[data-k=duplicates_dropped]").strip() != "310"
    ):
        pg.bad("same-burst counters should read ingested 0, duplicates dropped 310")
    p.unroute(same_url)
    p.unroute("**/stream/status?run_id=r-same")

    # the bad-message demo (stubbed) ends in the dead-letter queue
    bad = ("finished", False, 1.0, {"published": 1, "malformed": 1, "retried": 4, "dead_letter": 1})
    p.route(
        "**/stream/poison",
        lambda r: stub(r, 200, {"run_id": "r-bad", "total": 1, "state": "publishing"}),
    )
    p.route("**/stream/status?run_id=r-bad", status_handler("r-bad", "poison", [bad]))
    p.click("#stream-bad-btn")
    p.wait_for_function(  # the caption of THIS run (the badge may still say Finished from before)
        "document.querySelector('#stream-caption')?.textContent.includes('dead-letter queue')",
        timeout=15000,
    )
    if "dead-letter" not in p.inner_text("#stream-caption"):
        pg.bad("the bad-message caption does not mention the dead-letter queue")
    p.unroute("**/stream/poison")
    p.unroute("**/stream/status?run_id=r-bad")
    if p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
        pg.bad("the Command centre scrolls sideways")

    # a real streamed alert from an earlier run opens like any other case
    real_streamed = [a for a in real_queue if a.get("streamed")]
    if real_streamed:
        p.goto(f"{pg.base}/#/case/{real_streamed[0]['alert_id']}")
        p.wait_for_selector("#graph canvas")
        p.wait_for_timeout(1500)
        if "could not be loaded" in p.inner_text("#view"):
            pg.bad("a streamed case shows an error state")
        p.goto(pg.base + "/#/")
        p.wait_for_selector(".kpis")

    if real:
        p.click("#stream-btn")
        p.wait_for_function(
            "['Finished','Error','Stalled','Rate limited'].includes("
            "document.querySelector('#stream-state')?.textContent)",
            timeout=240000,
        )
        print("  real fresh run state:", p.inner_text("#stream-state"))
        if p.inner_text("#stream-state") == "Finished":
            if "new rows were ingested" not in p.inner_text("#stream-caption"):
                pg.bad("the real fresh run did not ingest new rows")
            p.click("#stream-same-btn")  # the real same-burst replay (its own rate-limit slot)
            p.wait_for_function(
                "['Finished','Error','Stalled','Rate limited'].includes("
                "document.querySelector('#stream-state')?.textContent)"
                " && document.querySelector('#stream-caption')?.textContent.includes('Same')",
                timeout=240000,
            )
            print("  real same-burst state:", p.inner_text("#stream-state"))
            if p.inner_text("[data-k=ingested]").strip() != "0":
                pg.bad("the real same-burst replay ingested rows")

    # Governance lists the stream guards
    p.goto(pg.base + "/#/governance")
    p.wait_for_selector("table")
    text = p.inner_text("#view")
    for want in GUARDS:
        if want not in text:
            pg.bad(f"Governance is missing {want!r}")
    if BANNED.search(text):
        pg.bad("Governance says real-time or live monitoring")
