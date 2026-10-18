"""In-memory view of the streamed data, so the existing case endpoints can serve streamed alerts.

``StreamOverlay`` holds the latest run's alerts, the scores of the accounts it scored and the
stream accounts and trades. The workbench's ``DataStore`` consults it only for streamed ids, and
``StreamGraphRunner`` answers the graph service's SQL fallback queries from it, so the existing
``GraphService`` (fallback engine) builds the ring subgraph of a streamed alert with the same code
and the same edge ids as for any other alert.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from collections import defaultdict

from services.graph import links
from services.graph.service import GraphService


def _iso(v) -> str:
    if isinstance(v, dt.datetime):
        return v.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(v)


class StreamOverlay:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._alerts: list[dict] = []
        self._scores: dict[str, dict] = {}
        self._accounts: dict[str, dict] = {}
        self._trades: list[dict] = []
        self.version = 0  # bumps whenever the tables change, so graph caches are rebuilt

    # ---- writes ----

    def set_tables(self, accounts: list[dict], trades: list[dict]) -> None:
        with self._lock:
            self._accounts = {
                a["account_id"]: {**a, "created_ts": _iso(a["created_ts"])} for a in accounts
            }
            self._trades = [{**t, "ts": _iso(t["ts"])} for t in trades]
            self.version += 1

    def set_alerts(self, alerts: list[dict], scores: dict[str, dict]) -> None:
        with self._lock:
            self._alerts = [dict(a, streamed=True) for a in alerts]
            self._scores = dict(scores)

    def clear_alerts(self) -> None:
        with self._lock:
            self._alerts, self._scores = [], {}

    # ---- DataStore-facing reads (the same shapes DataStore returns) ----

    def alerts(self) -> list[dict]:
        with self._lock:
            return [dict(a) for a in self._alerts]

    def get_alert(self, alert_id: str) -> dict | None:
        return next((a for a in self.alerts() if a["alert_id"] == alert_id), None)

    def has_account(self, account_id: str) -> bool:
        with self._lock:
            return account_id in self._accounts

    def account(self, account_id: str) -> dict | None:
        with self._lock:
            a = self._accounts.get(account_id)
            return dict(a, source="streamed") if a else None

    def score_of(self, account_id: str) -> dict | None:
        with self._lock:
            s = self._scores.get(account_id)
        if s is None:
            return None
        return {
            "score": round(float(s["score"]), 2),
            "flagged": bool(s["flagged"]),
            "threshold": s.get("threshold"),
            "ring_id": s.get("ring_id"),
            "features": s.get("features", {}),
            "contributions": s.get("contributions", {}),
        }

    def markets_of(self, account_id: str) -> list[dict]:
        with self._lock:
            agg: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
            for t in self._trades:
                if t["account_id"] == account_id:
                    agg[t["market_id"]][0] += 1
                    agg[t["market_id"]][1] += float(t["size_usd"])
        rows = [
            {"market_id": m, "n_trades": n, "total_usd": round(u, 2)} for m, (n, u) in agg.items()
        ]
        return sorted(rows, key=lambda r: -r["total_usd"])

    def sharing(self, account_id: str) -> dict:
        with self._lock:
            a = self._accounts.get(account_id)
            if a is None:
                return {"device_shared_by": 0, "funding_source_shared_by": 0}
            dev = sum(1 for x in self._accounts.values() if x["device_id"] == a["device_id"])
            fund = sum(
                1
                for x in self._accounts.values()
                if x["funding_source_id"] == a["funding_source_id"]
            )
        return {"device_shared_by": dev, "funding_source_shared_by": fund}

    # ---- rows for the graph runner ----

    def snapshot(self) -> tuple[list[dict], list[dict]]:
        with self._lock:
            return list(self._accounts.values()), list(self._trades)

    # ---- (de)serialising alerts for stream_alerts ----

    @staticmethod
    def hydrate_alert(row: dict) -> tuple[dict, dict[str, dict]]:
        """A stream_alerts row -> (alert dict as the DataStore shapes it, member scores)."""
        alert = {k: row[k] for k in row if k != "member_scores"}
        alert["created_ts"] = _iso(row.get("created_ts"))
        scores = json.loads(row["member_scores"]) if row.get("member_scores") else {}
        return alert, scores


class StreamGraphRunner:
    """Answers the fallback engine's fixed ``-- q: sql_*`` queries from the overlay."""

    def __init__(self, overlay: StreamOverlay):
        self.overlay = overlay
        self.client = object()  # GraphService.warm() touches .client

    def query(self, sql: str, params: dict | None = None, deadline: float | None = None):
        tag = sql.split("\n", 1)[0].replace("-- q:", "").strip()
        accounts, trades = self.overlay.snapshot()
        params = params or {}
        if tag == "sql_uses_device":
            return [{"account_id": a["account_id"], "node": a["device_id"]} for a in accounts]
        if tag == "sql_funded_by":
            return [
                {"account_id": a["account_id"], "node": a["funding_source_id"]} for a in accounts
            ]
        if tag == "sql_traded_with":
            return _fills(trades)
        if tag == "sql_traded_in":
            want, market = set(params["accounts"]), params["market"]
            agg: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
            for t in trades:
                if t["market_id"] == market and t["account_id"] in want:
                    agg[t["account_id"]][0] += 1
                    agg[t["account_id"]][1] += float(t["size_usd"])
            return [
                {"account_id": a, "n_trades": n, "total_usd": u}
                for a, (n, u) in sorted(agg.items())
            ]
        if tag == "sql_trades":
            want, market = set(params["accounts"]), params["market"]
            rows = sorted(
                (t for t in trades if t["market_id"] == market and t["account_id"] in want),
                key=lambda t: (t["ts"], t["trade_id"]),
            )[: links.MAX_TRADES + 1]
            return [
                {
                    k: t[k]
                    for k in (
                        "trade_id",
                        "ts",
                        "account_id",
                        "side",
                        "outcome",
                        "price",
                        "size_usd",
                    )
                }
                for t in rows
            ]
        raise ValueError(f"unsupported stream graph query {tag!r}")


def _fills(trades: list[dict]) -> list[dict]:
    """Matched fills: a SELL and a BUY with the same market, outcome, time, price and size
    (the rule of risk_graph.traded_with and sql/views/det_10_fills.sql)."""
    sells: dict[tuple, list[str]] = defaultdict(list)
    for t in trades:
        if t["side"] == "SELL":
            sells[(t["market_id"], t["outcome"], t["ts"], t["price"], t["size_usd"])].append(
                t["account_id"]
            )
    pairs = set()
    for t in trades:
        if t["side"] == "BUY":
            for seller in sells.get(
                (t["market_id"], t["outcome"], t["ts"], t["price"], t["size_usd"]), []
            ):
                if seller != t["account_id"]:
                    pairs.add((seller, t["account_id"]))
    return [{"seller": s, "buyer": b} for s, b in sorted(pairs)]


class StreamGraph:
    """The existing GraphService (fallback engine) over the overlay; rebuilt when data changes."""

    def __init__(self, project: str, overlay: StreamOverlay):
        self.project, self.overlay = project, overlay
        self._svc: GraphService | None = None
        self._version = -1
        self._lock = threading.Lock()

    def service(self) -> GraphService:
        with self._lock:
            if self._svc is None or self._version != self.overlay.version:
                self._svc = GraphService(
                    self.project, runner=StreamGraphRunner(self.overlay), force_fallback=True
                )
                self._version = self.overlay.version
            return self._svc


class RoutingGraph:
    """Streamed ids go to the stream graph, everything else to the live graph service."""

    def __init__(self, live, stream: StreamGraph):
        self.live, self.stream = live, stream

    def warm(self) -> None:
        self.live.warm()

    def _is_stream(self, account_id: str) -> bool:
        return self.stream.overlay.has_account(account_id)

    def ring_subgraph(self, ring_id: str) -> dict:
        if self._is_stream(links.seed_of(ring_id)):
            return self._tag(self.stream.service().ring_subgraph(ring_id))
        return self.live.ring_subgraph(ring_id)

    def neighbors(self, account_id: str, hops: int = 1) -> dict:
        if self._is_stream(account_id):
            return self._tag(self.stream.service().neighbors(account_id, hops))
        return self.live.neighbors(account_id, hops)

    def trades_for(self, account_ids, market_id: str) -> dict:
        if any(self._is_stream(a) for a in account_ids):
            return self._tag(self.stream.service().trades_for(account_ids, market_id))
        return self.live.trades_for(account_ids, market_id)

    @staticmethod
    def _tag(res: dict) -> dict:
        res["fallback_reason"] = "streamed data: answered from the stream tables"
        return res

    def __getattr__(self, name):  # anything else is the live service's
        return getattr(self.live, name)
