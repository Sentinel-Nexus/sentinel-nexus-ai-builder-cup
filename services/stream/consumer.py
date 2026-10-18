"""Micro-batch consumer: validate, dedupe, MERGE into BigQuery, ack, then score.

Messages arrive on a streaming-pull callback thread. Valid ones are queued; a batcher thread
collects up to 200 messages or 3 seconds, writes them, acks them and, once a run has everything it
published (or has gone quiet), asks the scorer for one scoring query. A malformed message is nacked
and recorded on every delivery, so Pub/Sub's dead-letter policy (5 attempts) moves it to
trades-dlq. Duplicates are dropped by an in-memory set per instance and, behind it, by the MERGE.
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from services.stream import config as C
from services.stream import scoring as S
from services.stream.runtime import StreamRuntime
from services.stream.schema import Event, Malformed, parse_message

IDLE_SCORE_S = 8.0  # score what has arrived when a run goes quiet for this long


class Incoming(Protocol):
    data: bytes
    attributes: dict
    message_id: str
    delivery_attempt: int | None

    def ack(self) -> None: ...
    def nack(self) -> None: ...


class Consumer:
    def __init__(self, runtime: StreamRuntime, store, project: str, overlay=None,
                 window_s: float = C.BATCH_WINDOW_S, max_batch: int = C.BATCH_MAX_MESSAGES,
                 max_attempts: int = C.MAX_DELIVERY_ATTEMPTS, idle_score_s: float = IDLE_SCORE_S,
                 clock=time.monotonic):  # fmt: skip
        self.rt, self.store, self.project, self.overlay = runtime, store, project, overlay
        self.window_s, self.max_batch, self.max_attempts = window_s, max_batch, max_attempts
        self.idle_score_s, self._clock = idle_score_s, clock
        self.q: queue.Queue = queue.Queue()
        self._seen: set[tuple[str, str]] = set()
        self._bad_seen: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scorer = ThreadPoolExecutor(1, thread_name_prefix="stream-score")
        self._lock = threading.Lock()

    # ---- callback thread ----

    def on_message(self, msg: Incoming) -> None:
        attempt = msg.delivery_attempt or 1
        try:
            event = parse_message(msg.data, dict(msg.attributes or {}))
        except Malformed as e:
            self._bad(msg, attempt, str(e))
            return
        except Exception as e:  # noqa: BLE001  nothing may escape the callback
            self._bad(msg, attempt, f"unexpected {type(e).__name__}")
            return
        self.q.put((event, msg))

    def _bad(self, msg: Incoming, attempt: int, reason: str) -> None:
        rid = (msg.attributes or {}).get("run_id") or "unattributed"
        run = (
            self.rt.run_for_message(rid)
            if rid != "unattributed"
            else self.rt.run_for_message("unattributed")
        )
        with self.rt.lock:
            if msg.message_id not in self._bad_seen:
                self._bad_seen.add(msg.message_id)
                run.malformed += 1
                self.rt.record(rid, "malformed", msg.message_id, attempt, reason)
            if attempt >= self.max_attempts:
                run.dead_letter += 1
                self.rt.record(
                    rid, "dead_letter_candidate", msg.message_id, attempt,
                    f"attempt {attempt} of {self.max_attempts}: Pub/Sub forwards it to trades-dlq",
                )  # fmt: skip
                if run.kind == "poison":
                    self._finish(run)
            else:
                run.retried += 1
                self.rt.record(rid, "retry", msg.message_id, attempt, f"nacked: {reason}")
        msg.nack()

    # ---- batching ----

    def collect(self, first_timeout: float = 0.5) -> list[tuple[Event, Incoming]]:
        try:
            batch = [self.q.get(timeout=first_timeout)]
        except queue.Empty:
            return []
        deadline = self._clock() + self.window_s
        while len(batch) < self.max_batch:
            if self._all_in_hand(batch):  # take what is queued and stop waiting
                while len(batch) < self.max_batch:
                    try:
                        batch.append(self.q.get_nowait())
                    except queue.Empty:
                        break
                break
            left = deadline - self._clock()
            if left <= 0:
                break
            try:
                batch.append(self.q.get(timeout=min(left, 0.2)))
            except queue.Empty:
                continue
        return batch

    def _all_in_hand(self, batch) -> bool:
        """The active replay's messages have all arrived: no reason to wait out the window."""
        run = self.rt.active_run()
        if run is None or run.kind != "replay" or run.expected <= 0:
            return False
        mine = sum(1 for e, _ in batch if e.run_id == run.run_id)
        return run.processed + mine + self.q.qsize() >= run.expected

    def process_batch(self, batch: list[tuple[Event, Incoming]]) -> None:
        fresh: list[tuple[Event, Incoming]] = []
        in_batch: set[tuple[str, str]] = set()
        for event, msg in batch:
            run = self.rt.run_for_message(event.run_id)
            if event.ident in self._seen or event.ident in in_batch:
                with self.rt.lock:
                    run.duplicates_dropped += 1
                    run.processed += 1
                    self.rt.record(event.run_id, "duplicate_dropped", msg.message_id,
                                   msg.delivery_attempt, f"{event.kind} {event.key} already seen")  # fmt: skip
                msg.ack()
                continue
            in_batch.add(event.ident)
            fresh.append((event, msg))
        if fresh:
            self._write(fresh)
        for run_id in {e.run_id for e, _ in batch}:
            self._after_batch(run_id)

    def _write(self, fresh: list[tuple[Event, Incoming]]) -> None:
        accounts = [
            {**e.row, "run_id": e.run_id} for e, _ in fresh if e.kind == "account_registered"
        ]
        trades = [{**e.row, "run_id": e.run_id} for e, _ in fresh if e.kind == "trade"]
        result: dict[str, int] = {}
        errors: list[Exception] = []

        def go(name, fn, rows):
            try:
                result[name] = fn(rows) if rows else 0
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=go, args=("accounts", self.store.merge_accounts, accounts)),
            threading.Thread(target=go, args=("trades", self.store.merge_trades, trades)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:  # nack everything: Pub/Sub redelivers, the MERGE keeps it idempotent
            for event, msg in fresh:
                run = self.rt.run_for_message(event.run_id)
                with self.rt.lock:
                    run.retried += 1
                    self.rt.record(event.run_id, "retry", msg.message_id, msg.delivery_attempt,
                                   f"BigQuery write failed: {type(errors[0]).__name__}")  # fmt: skip
                msg.nack()
            return
        new = {"account_registered": result["accounts"], "trade": result["trades"]}
        sent = {"account_registered": len(accounts), "trade": len(trades)}
        for event, msg in fresh:
            run = self.rt.run_for_message(event.run_id)
            partial = new[event.kind] < sent[event.kind]
            with self.rt.lock:
                run.processed += 1
                self.rt.record(
                    event.run_id, "ingested", msg.message_id, msg.delivery_attempt,
                    f"{event.kind} {event.key}"
                    + (f" (batch: {new[event.kind]} of {sent[event.kind]} rows were new)" if partial else ""),
                )  # fmt: skip
            self._seen.add(event.ident)
            msg.ack()
        # ingested = rows new to BigQuery; the MERGE reports how many it inserted, the rest were
        # already there (another instance, an earlier run) and count as duplicates dropped.
        for kind in new:
            dup = sent[kind] - new[kind]
            by_run: dict[str, int] = {}
            for e, _ in fresh:
                if e.kind == kind:
                    by_run[e.run_id] = by_run.get(e.run_id, 0) + 1
            for run_id, n in by_run.items():
                d = min(dup, n)
                dup -= d
                with self.rt.lock:
                    run = self.rt.run_for_message(run_id)
                    run.ingested += n - d
                    run.duplicates_dropped += d
                    if d:
                        self.rt.record(run_id, "duplicate_dropped", "bigquery-merge", None,
                                       f"{d} {kind} rows already in BigQuery (MERGE inserted none)")  # fmt: skip

    # ---- scoring ----

    def _after_batch(self, run_id: str) -> None:
        run = self.rt.run_for_message(run_id)
        with self.rt.lock:
            if run.kind == "replay" and run.state in ("publishing", "ingesting"):
                run.state = "ingesting"
        if run.kind == "replay" and run.expected and run.processed >= run.expected:
            self.request_scoring(run_id)

    def request_scoring(self, run_id: str) -> bool:
        run = self.rt.run_for_message(run_id)
        with self.rt.lock:
            if run.scoring_active or run.scoring_queries >= C.MAX_SCORING_QUERIES:
                return False
            run.scoring_active, run.state = True, "scoring"
        self._scorer.submit(self._score, run_id)
        return True

    def _score(self, run_id: str) -> None:
        run = self.rt.run_for_message(run_id)
        t0 = self._clock()
        try:
            with self.rt.lock:
                run.scoring_queries += 1
            features = self.store.score_features(S.build_script(self.project))
            alerts, scores = S.alerts_from_features(features, run_id)
            if alerts:
                self.store.merge_alerts(alerts)
            if self.overlay is not None:
                self.overlay.set_tables(self.store.read_accounts(), self.store.read_trades())
                self.overlay.set_alerts(alerts, scores)
            with self.rt.lock:
                run.alerts_raised = len(alerts)
                run.alert_ids = [a["alert_id"] for a in alerts]
                run.t_alert = self._clock()
                self.rt.record(run_id, "scored", "", None,
                               f"{len(scores)} accounts scored in {self._clock() - t0:.1f}s, "
                               f"{len(alerts)} alert(s)")  # fmt: skip
                for a in alerts:
                    self.rt.record(run_id, "alert_raised", a["alert_id"], None,
                                   f"{a['ring_id']} score {a['score']} members {a['n_members']}")  # fmt: skip
            self._finish(run)
        except Exception as e:  # noqa: BLE001
            with self.rt.lock:
                run.state, run.error = "error", f"{type(e).__name__}: {str(e)[:200]}"
                run.t_end = self._clock()
            print(f"stream: scoring failed for {run_id}: {run.error}", flush=True)
        finally:
            run.scoring_active = False

    def _finish(self, run) -> None:
        with self.rt.lock:
            run.state, run.t_end = "finished", self._clock()

    # ---- thread ----

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="stream-batcher")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._scorer.shutdown(wait=False)

    def _loop(self) -> None:
        last_flush = self._clock()
        while not self._stop.is_set():
            try:
                batch = self.collect()
                if batch:
                    self.process_batch(batch)
                self._watch()
                if self._clock() - last_flush >= 1.0:
                    self.rt.flush_events()
                    last_flush = self._clock()
            except Exception as e:  # noqa: BLE001  the loop must survive anything
                print(f"stream: batcher error {type(e).__name__}: {str(e)[:200]}", flush=True)
                time.sleep(0.5)
        self.rt.flush_events()

    def _watch(self) -> None:
        """Idle fallback and run deadline."""
        now = self._clock()
        with self.rt.lock:
            runs = [r for r in self.rt.runs.values() if r.active]
        for r in runs:
            if (
                r.kind == "replay"
                and r.state == "ingesting"
                and r.processed > 0
                and not r.scoring_active
                and r.scoring_queries == 0
                and now - r.t_activity > self.idle_score_s
            ):
                self.request_scoring(r.run_id)
            deadline = getattr(self.rt, "run_deadline_s", 180.0)
            if r.kind != "external" and now - r.t_start > deadline and not r.scoring_active:
                with self.rt.lock:
                    r.state = "stalled"
                    r.error = f"no completion within {deadline:.0f}s"
                    r.t_end = now


def pubsub_callback(consumer: Consumer):
    """Adapter from a pubsub_v1 Message to the Incoming protocol (it already matches)."""
    return consumer.on_message
