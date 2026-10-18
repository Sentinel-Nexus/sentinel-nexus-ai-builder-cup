"""Strict validation of stream messages. Anything that does not match exactly is malformed.

Two event types, each with an exact key set (extra or missing keys are rejected), typed fields and
bounded ranges. The Pub/Sub attributes must carry the same ``event_type`` and a ``run_id``.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass

ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
RUN_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
SIDES = ("BUY", "SELL")
OUTCOMES = ("YES", "NO")
ACCOUNT_KEYS = {
    "event_type", "account_id", "device_id", "funding_source_id", "kyc_tier", "created_ts", "ts",
}  # fmt: skip
TRADE_KEYS = {
    "event_type", "trade_id", "market_id", "account_id", "side", "outcome", "price", "size_usd",
    "ts",
}  # fmt: skip
MAX_BYTES = 4096
MAX_SIZE_USD = 10_000_000.0


class Malformed(ValueError):
    """The message is not a valid stream event; ``str(e)`` is the reason."""


@dataclass(frozen=True)
class Event:
    kind: str  # "account_registered" | "trade"
    key: str  # trade_id or account_id
    run_id: str
    row: dict  # BigQuery-ready, timestamps as ISO strings

    @property
    def ident(self) -> tuple[str, str]:
        return (self.kind, self.key)


def _ident(d: dict, name: str) -> str:
    v = d.get(name)
    if not isinstance(v, str) or not ID.match(v):
        raise Malformed(f"{name} must be an id string")
    return v


def _ts(d: dict, name: str) -> str:
    v = d.get(name)
    if not isinstance(v, str):
        raise Malformed(f"{name} must be an ISO timestamp string")
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise Malformed(f"{name} is not an ISO timestamp") from e
    if t.tzinfo is None:
        raise Malformed(f"{name} needs a timezone")
    return t.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(d: dict, name: str, lo: float, hi: float, *, open_lo=False) -> float:
    v = d.get(name)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise Malformed(f"{name} must be a finite number")
    if v < lo or v > hi or (open_lo and v == lo):
        raise Malformed(f"{name} is out of range")
    return float(v)


def parse_message(data: bytes, attributes: dict[str, str] | None) -> Event:
    """Validate one Pub/Sub message. Raises Malformed with a reason, never anything else."""
    attributes = attributes or {}
    if not isinstance(data, bytes | bytearray) or not data or len(data) > MAX_BYTES:
        raise Malformed("empty or oversized body")
    try:
        d = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise Malformed("body is not valid JSON") from e
    if not isinstance(d, dict):
        raise Malformed("body must be a JSON object")
    kind = d.get("event_type")
    if kind not in ("account_registered", "trade"):
        raise Malformed("unknown event_type")
    if attributes.get("event_type") != kind:
        raise Malformed("attribute event_type missing or different from the body")
    run_id = attributes.get("run_id", "")
    if not RUN_ID.match(run_id):
        raise Malformed("attribute run_id missing or invalid")
    keys = ACCOUNT_KEYS if kind == "account_registered" else TRADE_KEYS
    if set(d) != keys:
        extra, missing = sorted(set(d) - keys), sorted(keys - set(d))
        raise Malformed(f"unexpected keys {extra}, missing keys {missing}")
    if kind == "account_registered":
        tier = d.get("kyc_tier")
        if isinstance(tier, bool) or tier not in (1, 2, 3):
            raise Malformed("kyc_tier must be 1, 2 or 3")
        row = {
            "account_id": _ident(d, "account_id"),
            "created_ts": _ts(d, "created_ts"),
            "kyc_tier": tier,
            "device_id": _ident(d, "device_id"),
            "funding_source_id": _ident(d, "funding_source_id"),
        }
        _ts(d, "ts")
        return Event(kind, row["account_id"], run_id, row)
    if d.get("side") not in SIDES:
        raise Malformed("side must be BUY or SELL")
    if d.get("outcome") not in OUTCOMES:
        raise Malformed("outcome must be YES or NO")
    row = {
        "trade_id": _ident(d, "trade_id"),
        "ts": _ts(d, "ts"),
        "market_id": _ident(d, "market_id"),
        "account_id": _ident(d, "account_id"),
        "side": d["side"],
        "outcome": d["outcome"],
        "price": _num(d, "price", 0.0, 1.0, open_lo=True),
        "size_usd": _num(d, "size_usd", 0.0, MAX_SIZE_USD, open_lo=True),
    }
    return Event(kind, row["trade_id"], run_id, row)
