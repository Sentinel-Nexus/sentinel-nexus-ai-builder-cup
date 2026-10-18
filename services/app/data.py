"""Read-only data the agent tools need: alerts, scores, accounts, markets and link tables.

Tables are read with list_rows (no query, no slots) and cached in memory plus on disk.
``fixtures`` replaces BigQuery in tests.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from collections import defaultdict
from pathlib import Path

from services.app.config import REPO

CACHE_DIR = REPO / ".cache" / "agents"
CACHE_TTL_S = 3600.0
RELATED_WINDOW_H = 6.0  # rings whose main markets resolve within this many hours are related

TABLES = {
    "alerts": "risk_curated.alerts",
    "scores": "risk_curated.account_scores",
    "accounts": "risk_curated.accounts",
    "markets": "risk_curated.markets",
    "traded_in": "risk_graph.traded_in",
    "uses_device": "risk_graph.uses_device",
    "funded_by": "risk_graph.funded_by",
}


def _ts(value) -> dt.datetime | None:
    if value is None or isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _json(value) -> dict:
    return json.loads(value) if isinstance(value, str) else (value or {})


class DataStore:
    def __init__(
        self,
        project: str,
        client=None,
        *,
        fixtures: dict[str, list[dict]] | None = None,
        extra_alerts: list[dict] | None = None,
        cache_dir: Path | None = CACHE_DIR,
    ):
        self.project, self._client, self._fixtures = project, client, fixtures
        self.cache_dir = cache_dir
        self.extra_alerts = list(extra_alerts or [])
        self._tables: dict[str, list[dict]] = {}
        self._idx: dict[str, object] = {}
        self._lock = threading.RLock()
        self.overlay = (
            None  # streamed data (services.stream.overlay), set only when streaming is on
        )

    # ---- loading ----

    def _rows(self, name: str) -> list[dict]:
        with self._lock:
            if name not in self._tables:
                if self._fixtures is not None:
                    self._tables[name] = self._fixtures.get(name, [])
                else:
                    self._tables[name] = self._read(name)
            return self._tables[name]

    def _read(self, name: str) -> list[dict]:
        path = self.cache_dir / f"{name}.json" if self.cache_dir else None
        if path and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_S:
            return json.loads(path.read_text(encoding="utf-8"))
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project, location="US")
        rows = [dict(r.items()) for r in self._client.list_rows(f"{self.project}.{TABLES[name]}")]
        rows = json.loads(json.dumps(rows, default=str))
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows), encoding="utf-8", newline="\n")
        return rows

    def _index(self, key: str, build):
        with self._lock:
            if key not in self._idx:
                self._idx[key] = build()
            return self._idx[key]

    def warm(self) -> None:
        for name in TABLES:
            self._rows(name)

    # ---- indexes ----

    def _by(self, table: str, col: str) -> dict[str, dict]:
        return self._index(f"{table}.{col}", lambda: {r[col]: r for r in self._rows(table)})

    def _traded(self) -> dict[str, list[dict]]:
        def build():
            by_acc: dict[str, list[dict]] = defaultdict(list)
            for r in self._rows("traded_in"):
                by_acc[r["account_id"]].append(r)
            return by_acc

        return self._index("traded_by_account", build)

    def _sharers(self, table: str, col: str) -> tuple[dict[str, str], dict[str, int]]:
        def build():
            of: dict[str, str] = {}
            count: dict[str, int] = defaultdict(int)
            for r in self._rows(table):
                of[r["account_id"]] = r[col]
                count[r[col]] += 1
            return of, count

        return self._index(f"sharers.{table}", build)

    # ---- alerts ----

    def alerts(self) -> list[dict]:
        return [self._alert_row(a) for a in [*self._rows("alerts"), *self.extra_alerts]]

    @staticmethod
    def _alert_row(a: dict) -> dict:
        a = dict(a)
        for key in ("member_accounts", "evidence_trade_ids", "evidence_edge_ids"):
            v = a.get(key)
            a[key] = json.loads(v) if isinstance(v, str) else list(v or [])
        return a

    def get_alert(self, alert_id: str) -> dict | None:
        found = next((a for a in self.alerts() if a["alert_id"] == alert_id), None)
        if found is None and self.overlay is not None:
            found = self.overlay.get_alert(alert_id)
        return found

    def stream_alerts(self) -> list[dict]:
        """Streamed alerts of the latest run; never part of alerts() or the detector counts."""
        return self.overlay.alerts() if self.overlay is not None else []

    def _streamed(self, account_id: str) -> bool:
        return self.overlay is not None and self.overlay.has_account(account_id)

    # ---- per-entity lookups ----

    def score_of(self, account_id: str) -> dict | None:
        if self._streamed(account_id):
            return self.overlay.score_of(account_id)
        row = self._by("scores", "account_id").get(account_id)
        if row is None:
            return None
        return {
            "score": round(float(row["score"]), 2),
            "flagged": bool(row["flagged"]),
            "threshold": row.get("threshold"),
            "ring_id": row.get("ring_id"),
            "features": _json(row.get("features")),
            "contributions": _json(row.get("contributions")),
        }

    def market(self, market_id: str) -> dict | None:
        return self._by("markets", "market_id").get(market_id)

    def account(self, account_id: str) -> dict | None:
        if self._streamed(account_id):
            return self.overlay.account(account_id)
        return self._by("accounts", "account_id").get(account_id)

    def markets_of(self, account_id: str) -> list[dict]:
        if self._streamed(account_id):
            return self.overlay.markets_of(account_id)
        rows = self._traded().get(account_id, [])
        return sorted(rows, key=lambda r: -float(r["total_usd"]))

    def sharing(self, account_id: str) -> dict:
        """How many accounts share this account's device and funding source. The ids are not
        returned: generated ids can leak how the data was built."""
        dev_of, dev_n = self._sharers("uses_device", "device_id")
        fund_of, fund_n = self._sharers("funded_by", "funding_source_id")
        if self._streamed(account_id):
            return self.overlay.sharing(account_id)
        d, f = dev_of.get(account_id), fund_of.get(account_id)
        return {
            "device_shared_by": dev_n[d] if d else 0,
            "funding_source_shared_by": fund_n[f] if f else 0,
        }

    def data_end(self) -> dt.datetime | None:
        ends = [_ts(m["resolve_ts"]) for m in self._rows("markets")]
        return max((e for e in ends if e), default=None)

    def account_age_days(self, account_id: str) -> float | None:
        acc, end = self.account(account_id), self.data_end()
        created = _ts(acc["created_ts"]) if acc else None
        return round((end - created).total_seconds() / 86400, 1) if created and end else None

    def dominant_market(self, members: list[str]) -> str | None:
        total: dict[str, float] = defaultdict(float)
        for a in members:
            for r in self.markets_of(a):
                total[r["market_id"]] += float(r["total_usd"])
        return max(total, key=total.get) if total else None

    def related_rings(self, alert: dict) -> list[dict]:
        """Other alerts whose main market is the same, or resolves within the related window."""
        mine = self.dominant_market(alert["member_accounts"])
        if mine is None:
            return []
        m = self.market(mine)
        t0 = _ts(m["resolve_ts"]) if m else None
        out = []
        for other in self.alerts():
            if other["alert_id"] == alert["alert_id"] or other["ring_id"] == alert["ring_id"]:
                continue
            theirs = self.dominant_market(other["member_accounts"])
            if theirs is None:
                continue
            tm = self.market(theirs)
            t1 = _ts(tm["resolve_ts"]) if tm else None
            hours = abs((t1 - t0).total_seconds()) / 3600 if t0 and t1 else None
            if theirs != mine and (hours is None or hours > RELATED_WINDOW_H):
                continue
            traded = self._traded()
            edge_ids = sorted(
                f"TradedIn:{a}>{market}"
                for members, market in (
                    (alert["member_accounts"], mine),
                    (other["member_accounts"], theirs),
                )
                for a in members
                if any(r["market_id"] == market for r in traded.get(a, []))
            )
            out.append(
                {
                    "ring_id": other["ring_id"],
                    "alert_id": other["alert_id"],
                    "status": other["status"],
                    "scenario_hint": other["scenario_hint"],
                    "members": other["member_accounts"],
                    "dominant_market": theirs,
                    "same_market": theirs == mine,
                    "hours_apart": None if hours is None else round(hours, 2),
                    "edge_ids": edge_ids,
                }
            )
        return out
