"""The two engines: GQL on risk_graph.market_graph, and SQL plus NetworkX.

Both return the same ``Expansion``. The queries are fixed templates (``-- q: <tag>``) with
every value passed as a query parameter; the runner adds the read-only guard, the byte cap
and the timeout.
"""

from __future__ import annotations

import concurrent.futures
import time
from collections import defaultdict

import networkx as nx

from services.graph import links
from services.graph.links import Expansion
from services.graph.runner import GraphTimeout

ROW_CAP = 20_000  # a one-hop GQL query that hits this is refused rather than truncated


def q_gql_nodes(project: str, kind: str) -> str:
    node_label, prop = ("Device", "device_id") if kind == "UsesDevice" else (
        "FundingSource", "funding_source_id")  # fmt: skip
    return f"""-- q: gql_{kind}
GRAPH `{project}.risk_graph.market_graph`
MATCH (a:Account)-[:{kind}]->(n:{node_label})<-[:{kind}]-(b:Account)
WHERE a.account_id IN UNNEST(@frontier)
RETURN a.account_id AS a, n.{prop} AS node, b.account_id AS b
LIMIT {ROW_CAP + 1}"""


def q_gql_fills(project: str) -> str:
    return f"""-- q: gql_TradedWith
GRAPH `{project}.risk_graph.market_graph`
MATCH (s:Account)-[:TradedWith]->(o:Account)
WHERE s.account_id IN UNNEST(@frontier) OR o.account_id IN UNNEST(@frontier)
RETURN s.account_id AS seller, o.account_id AS buyer
LIMIT {ROW_CAP + 1}"""


def q_gql_traded_in(project: str) -> str:
    return f"""-- q: gql_traded_in
GRAPH `{project}.risk_graph.market_graph`
MATCH (a:Account)-[e:TradedIn]->(m:Market)
WHERE m.market_id = @market AND a.account_id IN UNNEST(@accounts)
RETURN a.account_id AS account_id, e.n_trades AS n_trades, e.total_usd AS total_usd"""


def q_sql_links(project: str, table: str, col: str) -> str:
    return f"""-- q: sql_{table}
SELECT account_id, {col} AS node FROM `{project}.risk_graph.{table}`"""


def q_sql_fills(project: str) -> str:
    return f"""-- q: sql_traded_with
SELECT seller_id AS seller, buyer_id AS buyer FROM `{project}.risk_graph.traded_with`"""


def q_sql_traded_in(project: str) -> str:
    return f"""-- q: sql_traded_in
SELECT account_id, n_trades, total_usd FROM `{project}.risk_graph.traded_in`
WHERE market_id = @market AND account_id IN UNNEST(@accounts)"""


def q_sql_trades(project: str) -> str:
    return f"""-- q: sql_trades
SELECT trade_id, CAST(ts AS STRING) AS ts, account_id, side, outcome, price, size_usd
FROM `{project}.risk_raw.trades`
WHERE market_id = @market AND account_id IN UNNEST(@accounts)
ORDER BY ts, trade_id
LIMIT {links.MAX_TRADES + 1}"""


def _summary(rows: list[dict], market_id: str) -> list[dict]:
    return [
        {
            "id": links.edge_id("TradedIn", r["account_id"], market_id),
            "account_id": r["account_id"],
            "n_trades": int(r["n_trades"]),
            "total_usd": round(float(r["total_usd"]), 2),
        }
        for r in sorted(rows, key=lambda r: r["account_id"])
    ]


def _trades(rows: list[dict]) -> tuple[list[dict], bool]:
    out = [
        {
            "id": r["trade_id"],
            "ts": r["ts"],
            "account_id": r["account_id"],
            "side": r["side"],
            "outcome": r["outcome"],
            "price": float(r["price"]),
            "size_usd": float(r["size_usd"]),
        }
        for r in rows[: links.MAX_TRADES]
    ]
    return out, len(rows) > links.MAX_TRADES


class GqlEngine:
    """Frontier expansion with one-hop GQL queries (at most one set per hop)."""

    name = "gql"

    def __init__(self, project: str, runner):
        self.project, self.runner = links.check_project(project), runner

    def _incident(self, frontier: list[str], deadline: float):
        jobs = {
            "UsesDevice": q_gql_nodes(self.project, "UsesDevice"),
            "FundedBy": q_gql_nodes(self.project, "FundedBy"),
            "TradedWith": q_gql_fills(self.project),
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                k: pool.submit(self.runner.query, sql, {"frontier": frontier}, deadline)
                for k, sql in jobs.items()
            }
            rows = {k: f.result() for k, f in futures.items()}
        if any(len(r) > ROW_CAP for r in rows.values()):
            raise RuntimeError("GQL hop returned too many rows (hub?); using the fallback")
        node_rows = [
            (kind, account, r["node"])
            for kind in links.NODE_LINKS
            for r in rows[kind]
            for account in (r["a"], r["b"])
        ]
        fills = [(r["seller"], r["buyer"]) for r in rows["TradedWith"]]
        return links.hop_edges(node_rows, fills)

    def expand(self, seeds: list[str], max_hops: int, deadline: float) -> Expansion:
        distance = dict.fromkeys(seeds, 0)
        frontier, edges = list(seeds), {}
        for hop in range(1, max_hops + 1):
            if not frontier:
                break
            if time.monotonic() >= deadline:
                raise GraphTimeout("deadline reached before the next hop")
            hop_edge_set, reached = self._incident(frontier, deadline)
            edges.update(hop_edge_set)
            frontier = sorted(reached - distance.keys())
            distance.update(dict.fromkeys(frontier, hop))
        return Expansion(
            distance, edges, truncated=bool(frontier) and max_hops == links.MAX_RING_HOPS
        )

    def trades_for(self, accounts: list[str], market_id: str, deadline: float):
        params = {"accounts": accounts, "market": market_id}
        summary = self.runner.query(q_gql_traded_in(self.project), params, deadline)
        active = sorted(r["account_id"] for r in summary)
        rows = []
        if active:
            sql = q_sql_trades(self.project)  # trade rows are not graph data
            rows = self.runner.query(sql, {"accounts": active, "market": market_id}, deadline)
        return _summary(summary, market_id), *_trades(rows)


class FallbackEngine:
    """SQL pulls the (small) edge tables once; NetworkX does the traversal."""

    name = "fallback"

    def __init__(self, project: str, runner, ttl: float = 300.0, clock=time.monotonic):
        self.project, self.runner, self.ttl, self._clock = (
            links.check_project(project),
            runner,
            ttl,
            clock,
        )
        self._loaded: tuple[float, dict, dict, list] | None = None

    def _graph(self):
        if self._loaded and self._clock() < self._loaded[0]:
            return self._loaded[1:]
        p = self.project
        by_node: dict[tuple[str, str], set[str]] = defaultdict(set)
        for kind, table, col in (
            ("UsesDevice", "uses_device", "device_id"),
            ("FundedBy", "funded_by", "funding_source_id"),
        ):
            for r in self.runner.query(q_sql_links(p, table, col)):
                by_node[(kind, r["node"])].add(r["account_id"])
        by_node = {k: v for k, v in by_node.items() if 2 <= len(v) <= links.HUB_LIMIT}
        fills = [(r["seller"], r["buyer"]) for r in self.runner.query(q_sql_fills(p))]
        memberships: dict[str, list[tuple[str, str]]] = defaultdict(list)
        graph = nx.Graph()
        for key, accounts in by_node.items():
            ordered = sorted(accounts)
            for a in ordered:
                memberships[a].append(key)
            graph.add_edges_from((a, b) for i, a in enumerate(ordered) for b in ordered[i + 1 :])
        graph.add_edges_from(fills)
        self._loaded = (self._clock() + self.ttl, graph, (by_node, memberships), fills)
        return self._loaded[1:]

    def expand(self, seeds: list[str], max_hops: int, deadline: float | None = None) -> Expansion:
        graph, (by_node, memberships), fills = self._graph()
        present = [s for s in seeds if s in graph]
        found = nx.multi_source_dijkstra_path_length(graph, present, cutoff=max_hops)
        distance = {**dict.fromkeys(seeds, 0), **{a: int(d) for a, d in found.items()}}
        expanded = {a for a, d in distance.items() if d < max_hops}
        edges: dict[str, dict] = {}
        for a in expanded:
            for kind, node in memberships.get(a, []):
                for member in by_node[(kind, node)]:
                    e = links.make_edge(kind, ("Account", member), (links.NODE_LINKS[kind], node))
                    edges[e["id"]] = e
        for seller, buyer in fills:
            if seller in expanded or buyer in expanded:
                e = links.make_edge("TradedWith", ("Account", seller), ("Account", buyer))
                edges[e["id"]] = e
        truncated = max_hops == links.MAX_RING_HOPS and any(
            d == max_hops for d in distance.values()
        )
        return Expansion(distance, edges, truncated)

    def trades_for(self, accounts: list[str], market_id: str, deadline: float | None = None):
        params = {"accounts": accounts, "market": market_id}
        summary = self.runner.query(q_sql_traded_in(self.project), params)
        active = sorted(r["account_id"] for r in summary)
        rows = (
            self.runner.query(q_sql_trades(self.project), {**params, "accounts": active})
            if active
            else []
        )
        return _summary(summary, market_id), *_trades(rows)
