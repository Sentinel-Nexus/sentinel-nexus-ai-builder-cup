"""Fakes for the stream path: Pub/Sub messages, a loopback publisher and a feature scorer."""

from __future__ import annotations

import json

from services.stream.bq import MemoryStore
from services.stream.config import StreamSettings
from services.stream.service import StreamController


class FakeMsg:
    def __init__(
        self, data: bytes, attributes: dict, message_id: str = "m1", attempt: int | None = 1
    ):
        self.data, self.attributes, self.message_id = data, attributes, message_id
        self.delivery_attempt = attempt
        self.acked = self.nacked = 0

    def ack(self):
        self.acked += 1

    def nack(self):
        self.nacked += 1


def account(a="SW01", **kw):
    d = dict(event_type="account_registered", account_id=a, device_id="SDW1",
             funding_source_id="SFW1", kyc_tier=1, created_ts="2026-09-27T00:00:00Z",
             ts="2026-09-27T00:00:00Z")  # fmt: skip
    return {**d, **kw}


def trade(t="ST000001", a="SW01", **kw):
    d = dict(event_type="trade", trade_id=t, market_id="M05", account_id=a, side="BUY",
             outcome="YES", price=0.5, size_usd=300.0, ts="2026-10-01T10:00:00Z")  # fmt: skip
    return {**d, **kw}


def msg(body: dict | bytes, run_id="r-1", mid="m1", attempt=1, **attrs) -> FakeMsg:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    et = body.get("event_type") if isinstance(body, dict) else None
    a = {"run_id": run_id, **({"event_type": et} if et else {}), **attrs}
    return FakeMsg(data, a, mid, attempt)


def features(accounts, trades):
    """Stand-in for the SQL step: the ring (SW*) looks like a ring, everyone else does not."""
    out = []
    seed = min((a["account_id"] for a in accounts if a["account_id"].startswith("SW")), default="")
    for a in accounts:
        ring = a["account_id"].startswith("SW")
        out.append(dict(
            account_id=a["account_id"], ring_id="R_" + (seed if ring else a["account_id"]),
            comp_size=6 if ring else 1, comp_density=1.0 if ring else 0.0,
            fill_n=14 if ring else 0, recip=1.0 if ring else 0.0,
            fresh_fanin=4 if ring else 0, age_first_trade_h=48.0 if ring else 5000.0,
            fresh_linked=5 if ring else 0, late_share=0.0, late_usd=0.0,
            mean_size_z=1.0, mean_late_z=0.0,
            evidence_trade_ids=["ST000001"] if ring else [],
            evidence_edge_ids=[f"UsesDevice:{a['account_id']}>SDW1"] if ring else [],
        ))  # fmt: skip
    return out


class FakeFuture:
    def __init__(self, mid):
        self._mid = mid

    def add_done_callback(self, cb):
        cb(self)

    def result(self, timeout=None):
        return self._mid


class LoopbackPublisher:
    """publish() hands the message straight to the controller's consumer, as Pub/Sub would."""

    def __init__(self):
        self.sent: list[FakeMsg] = []
        self.consumer = None

    def publish(self, data: bytes, **attrs):
        m = FakeMsg(data, {k: str(v) for k, v in attrs.items()}, f"pub-{len(self.sent) + 1}")
        self.sent.append(m)
        if self.consumer:
            self.consumer.on_message(m)
        return FakeFuture(m.message_id)


def make_controller(enabled=True, burst=None, **limits):
    store = MemoryStore(scorer=features)
    pub = LoopbackPublisher()
    settings = StreamSettings(project="p", enabled=enabled, **limits)
    ctl = StreamController(settings, store=store, publisher=pub, subscribe=lambda cb: None,
                           burst=burst)  # fmt: skip
    pub.consumer = ctl.consumer
    ctl.consumer.window_s = 0.2
    ctl.consumer.idle_score_s = 0.5
    return ctl, store, pub
