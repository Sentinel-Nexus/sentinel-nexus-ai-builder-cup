"""Wiring: publisher, streaming-pull consumer, rate limits and the /stream/* routes.

``attach(app, d, state)`` is called by the app factory. ``GET /stream/config`` always exists and
says whether streaming is on; every other route exists only when ``STREAMING_ENABLED`` is set, so
with the flag off the app is the one that was deployed before this phase.

Honest naming: this is a near-real-time micro-batch pipeline. Latency is measured, not promised.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request

from services.stream import config as C
from services.stream.bq import BigQueryStreamStore
from services.stream.consumer import Consumer
from services.stream.limits import MESSAGES, StreamLimiter
from services.stream.overlay import RoutingGraph, StreamGraph, StreamOverlay
from services.stream.runtime import StreamRuntime

BURST_FILE = Path(__file__).resolve().parents[2] / "data" / "stream" / "burst_01.jsonl"
POISON_BODY = {"event_type": "trade", "trade_id": "POISON-1", "price": "not-a-number"}


def load_burst(path: Path = BURST_FILE) -> list[dict]:
    """The fixed burst, in event-time order. There is no user-supplied body anywhere."""
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class PubSubPublisher:
    def __init__(self, project: str, topic: str = C.TOPIC):
        self.project, self.topic, self._client = project, topic, None

    def warm(self) -> None:
        """Create the client (credentials, channel) before the first replay needs it."""
        if self._client is None:
            from google.cloud import pubsub_v1

            self._client = pubsub_v1.PublisherClient()
            self._path = self._client.topic_path(self.project, self.topic)

    def publish(self, data: bytes, **attributes):
        self.warm()
        return self._client.publish(self._path, data, **attributes)


class StreamController:
    def __init__(self, settings: C.StreamSettings, *, store=None, publisher=None,
                 subscribe: Callable | None = None, burst: list[dict] | None = None):  # fmt: skip
        self.settings = settings
        self.overlay = StreamOverlay()
        self.store = store or BigQueryStreamStore(settings.project)
        self.runtime = StreamRuntime(self.store, self.overlay)
        self.runtime.run_deadline_s = settings.run_deadline_s
        self.consumer = Consumer(self.runtime, self.store, settings.project, self.overlay)
        self.publisher = publisher or PubSubPublisher(settings.project)
        self._subscribe = subscribe
        self._burst = burst
        self.replay_limiter = StreamLimiter(
            settings.per_client_10min, settings.per_hour, settings.per_day
        )
        self.poison_limiter = StreamLimiter(
            settings.per_client_10min, settings.per_hour, settings.per_day
        )
        self.graph: StreamGraph | None = None
        self.pull_error: str | None = None
        self._future = None
        self._sub_client = None

    # ---- lifecycle ----

    def start(self) -> None:
        self.consumer.start()
        threading.Thread(target=self._hydrate, daemon=True, name="stream-hydrate").start()
        threading.Thread(target=self._pull, daemon=True, name="stream-pull").start()

    def stop(self) -> None:
        self.consumer.stop()
        if self._future is not None:
            self._future.cancel()
        self.runtime.flush_events()

    def _hydrate(self) -> None:
        """Rebuild the overlay from the stream tables (free list_rows reads) after a restart."""
        try:
            self.overlay.set_tables(self.store.read_accounts(), self.store.read_trades())
            alerts, scores = [], {}
            for row in self.store.latest_alerts():
                alert, member_scores = self.overlay.hydrate_alert(row)
                alerts.append(alert)
                scores.update(member_scores)
            self.overlay.set_alerts(alerts, scores)
        except Exception as e:  # noqa: BLE001
            print(f"stream: hydrate failed: {type(e).__name__}: {str(e)[:160]}", flush=True)

    def _pull(self) -> None:
        try:
            if hasattr(self.publisher, "warm"):
                self.publisher.warm()
            if self._subscribe is not None:
                self._future = self._subscribe(self.consumer.on_message)
                return
            from google.cloud import pubsub_v1

            self._sub_client = pubsub_v1.SubscriberClient()
            path = self._sub_client.subscription_path(self.settings.project, C.SUBSCRIPTION)
            flow = pubsub_v1.types.FlowControl(max_messages=500)
            self._future = self._sub_client.subscribe(
                path, callback=self.consumer.on_message, flow_control=flow
            )
            print(f"stream: streaming pull on {path}", flush=True)
        except Exception as e:  # noqa: BLE001
            self.pull_error = f"{type(e).__name__}: {str(e)[:160]}"
            print(f"stream: streaming pull failed: {self.pull_error}", flush=True)

    # ---- publishing ----

    def burst(self) -> list[dict]:
        if self._burst is None:
            self._burst = load_burst()
        return self._burst

    def start_replay(self):
        msgs = self.burst()
        run = self.runtime.start_run("replay", len(msgs))
        threading.Thread(target=self._publish, args=(run, msgs), daemon=True).start()
        return run

    def start_poison(self):
        run = self.runtime.start_run("poison", 1)
        threading.Thread(
            target=self._publish_raw,
            args=(run, [(json.dumps(POISON_BODY).encode(), {"event_type": "trade"})]),
            daemon=True,
        ).start()
        return run

    def _publish(self, run, msgs: list[dict]) -> None:
        raw = [
            (json.dumps(m, sort_keys=True).encode(), {"event_type": m["event_type"]}) for m in msgs
        ]
        self._publish_raw(run, raw)

    def _publish_raw(self, run, raw: list[tuple[bytes, dict]]) -> None:
        rt = self.runtime
        done = threading.Event()
        left = [len(raw)]
        lock = threading.Lock()

        def on_done(fut) -> None:
            try:
                mid = fut.result()
            except Exception as e:  # noqa: BLE001
                with rt.lock:
                    run.state, run.error = "error", f"publish failed: {type(e).__name__}"
                    run.t_end = rt._clock()
                done.set()
                return
            with rt.lock:
                run.published += 1
                run.t_last_publish = rt._clock()
                rt.record(run.run_id, "published", str(mid), None, "")
            with lock:
                left[0] -= 1
                if left[0] == 0:
                    done.set()

        with rt.lock:
            run.t_first_publish = rt._clock()
        try:
            for data, attrs in raw:
                self.publisher.publish(data, run_id=run.run_id, **attrs).add_done_callback(on_done)
        except Exception as e:  # noqa: BLE001
            with rt.lock:
                run.state, run.error = (
                    "error",
                    f"publish failed: {type(e).__name__}: {str(e)[:120]}",
                )
                run.t_end = rt._clock()
            return
        done.wait(60)
        with rt.lock:
            if run.state == "publishing":
                run.state = "ingesting"


def attach(app: FastAPI, d, state: dict) -> StreamController | None:
    """Register the stream routes. Returns the controller, or None when the flag is off."""
    from services.app.workbench import client_id

    settings = C.StreamSettings()

    @app.get("/stream/config")
    def stream_config():
        c = state.get("stream")
        return {
            "enabled": settings.enabled,
            "mode": "near-real-time micro-batch",
            "messages_in_burst": len(c.burst()) if c else None,
            "limits": c.replay_limiter.status() if c else None,
            "consumer_ok": bool(c and c.pull_error is None),
            "measured_seconds": _measured_seconds() if settings.enabled else None,
        }

    if not settings.enabled:
        state["stream"] = None
        return None

    ctl = (FACTORY or StreamController)(settings)
    state["stream"] = ctl
    d_store = lambda: d().store  # noqa: E731
    state["stream_wire"] = lambda: _wire(d, ctl, d_store)

    def refuse(limiter: StreamLimiter, request: Request) -> None:
        ok, retry, scope = limiter.check(client_id(request))
        if not ok:
            raise HTTPException(
                429,
                f"{MESSAGES.get(scope, 'Rate limited.')} Streaming is limited to "
                f"{settings.per_client_10min} per client per 10 minutes, {settings.per_hour} per "
                f"hour and {settings.per_day} per day for the service. "
                f"Try again in about {max(1, retry // 60)} min.",
                headers={"Retry-After": str(retry)},
            )

    def busy() -> None:
        run = ctl.runtime.active_run()
        if run is not None:
            raise HTTPException(409, f"A stream run ({run.run_id}) is still in progress.")

    @app.post("/stream/replay")
    def replay(request: Request):
        """Publish the fixed synthetic burst (no request body is read)."""
        busy()
        refuse(ctl.replay_limiter, request)
        run = ctl.start_replay()
        return {"run_id": run.run_id, "total": run.expected, "state": run.state}

    @app.post("/stream/poison")
    def poison(request: Request):
        """Publish one malformed message: it is retried, then dead-lettered."""
        busy()
        refuse(ctl.poison_limiter, request)
        run = ctl.start_poison()
        return {"run_id": run.run_id, "total": 1, "state": run.state}

    @app.get("/stream/status")
    def stream_status(run_id: str | None = Query(default=None, max_length=64)):
        s = ctl.runtime.status(run_id)
        if s is None:
            raise HTTPException(404, "no such stream run (the service may have restarted)")
        s["limits"] = ctl.replay_limiter.status()
        if s["timings"]["last_publish_to_alert_s"] is not None:
            MEASURED["last_publish_to_alert_s"] = s["timings"]["last_publish_to_alert_s"]
        return s

    return ctl


MEASURED: dict = {}
FACTORY = None  # tests replace this with a controller built on fakes
MEASURED_FILE = BURST_FILE.with_name("measured.json")


def _measured_seconds() -> float | None:
    """Seconds from the last publish to the alert, from the recorded measurement
    (data/stream/measured.json, written by scripts/stream_eval.py). None until measured."""
    try:
        return float(json.loads(MEASURED_FILE.read_text(encoding="utf-8"))["seconds"])
    except (OSError, ValueError, KeyError):
        return None


def _wire(d, ctl: StreamController, d_store) -> None:
    """Hook the overlay into the DataStore and route the graph (called once the deps exist)."""
    deps = d()
    deps.store.overlay = ctl.overlay
    ctl.graph = StreamGraph(deps.settings.project, ctl.overlay)
    deps.graph = RoutingGraph(deps.graph, ctl.graph)
