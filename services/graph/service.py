"""GraphService: GQL first, automatic SQL + NetworkX fallback, cache, ``engine`` on every reply.

    svc = GraphService(project)
    svc.neighbors("A0100", hops=2)
    svc.ring_subgraph("R_C002")
    svc.trades_for(["W001", "W002"], "M05")

A GQL failure of any kind (error, timeout, reservation unavailable) is answered by the
fallback and opens a short breaker so the next calls go straight to the fallback instead of
waiting for another timeout. Bad input (``GraphInputError``) is never retried on the other engine.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from services.graph import links
from services.graph.cache import TTLCache
from services.graph.engines import FallbackEngine, GqlEngine
from services.graph.links import GraphInputError
from services.graph.runner import DEFAULT_TIMEOUT_S, BigQueryRunner

__all__ = ["GraphInputError", "GraphService"]


class GraphService:
    def __init__(
        self,
        project: str,
        runner=None,
        *,
        force_fallback: bool | None = None,
        gql_timeout: float = DEFAULT_TIMEOUT_S,
        cache: TTLCache | None = None,
        breaker_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.runner = runner or BigQueryRunner(project)
        self.gql = GqlEngine(project, self.runner)
        self.fallback = FallbackEngine(project, self.runner, clock=clock)
        if force_fallback is None:
            force_fallback = os.environ.get("GRAPH_FORCE_FALLBACK", "").lower() in ("1", "true")
        self.force_fallback = force_fallback
        self.gql_timeout, self.breaker_seconds, self._clock = gql_timeout, breaker_seconds, clock
        self.cache = cache if cache is not None else TTLCache(clock=clock)
        self._breaker_until = 0.0

    def warm(self) -> None:
        """Create the BigQuery client now (auth and setup are slow on a cold process), so the
        first GQL call does not spend its 15 s on setup."""
        _ = self.runner.client

    # ---- public API ----

    def neighbors(self, account_id: str, hops: int = 1) -> dict:
        account_id, hops = links.check_id(account_id, "account_id"), links.check_hops(hops)

        def build(engine, deadline):
            exp = engine.expand([account_id], hops, deadline)
            out = links.to_response([account_id], exp)
            out["neighbors"] = [
                {"account_id": a, "distance": d}
                for a, d in sorted(exp.distance.items(), key=lambda kv: (kv[1], kv[0]))
                if a != account_id
            ]
            return {"account_id": account_id, "hops": hops, **out}

        return self._call(("neighbors", account_id, hops), build)

    def ring_subgraph(self, ring_id: str) -> dict:
        seed = links.seed_of(ring_id)

        def build(engine, deadline):
            exp = engine.expand([seed], links.MAX_RING_HOPS, deadline)
            out = links.to_response([seed], exp)
            return {"ring_id": ring_id, "members": sorted(exp.distance), **out}

        return self._call(("ring", ring_id), build)

    def trades_for(self, account_ids, market_id: str) -> dict:
        accounts, market_id = (
            links.check_accounts(account_ids),
            links.check_id(market_id, "market_id"),
        )

        def build(engine, deadline):
            summary, trades, truncated = engine.trades_for(accounts, market_id, deadline)
            return {
                "market_id": market_id,
                "account_ids": accounts,
                "edges": summary,
                "trades": trades,
                "truncated": truncated,
            }

        return self._call(("trades", tuple(accounts), market_id), build)

    # ---- engine selection ----

    def _call(self, key: tuple, build: Callable) -> dict:
        key = (("forced" if self.force_fallback else "auto"), *key)
        hit = self.cache.get(key)
        if hit is not None:
            return {**hit, "cached": True}
        reason = "forced" if self.force_fallback else None
        result = None
        if not self.force_fallback and self._clock() >= self._breaker_until:
            try:
                result = {**build(self.gql, time.monotonic() + self.gql_timeout), "engine": "gql"}
            except GraphInputError:
                raise
            except Exception as e:  # noqa: BLE001  any GQL problem is answered by the fallback
                self._breaker_until = self._clock() + self.breaker_seconds
                reason = f"{type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}"
        elif not self.force_fallback:
            reason = "gql breaker open after a recent failure"
        if result is None:
            result = {**build(self.fallback, None), "engine": "fallback"}
            result["fallback_reason"] = reason
        result["cached"] = False
        self.cache.set(key, result)
        return result
