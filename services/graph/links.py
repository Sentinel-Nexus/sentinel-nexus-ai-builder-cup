"""Engine-independent graph rules: node ids, stable edge ids, hub filter, result shape.

Both engines produce the same ``Expansion`` from the same market_graph edges, so a response
differs only in its ``engine`` field. Edge ids use the same format as the detector's
``evidence_edge_ids`` (``UsesDevice:<account>><device>``), so an alert's evidence can be
looked up in a ring subgraph by id.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

HUB_LIMIT = 25  # a device or funding source shared by more accounts links nothing (as in P2)
MAX_RING_HOPS = 12
MAX_NEIGHBOR_HOPS = 4
MAX_ACCOUNTS = 200
MAX_TRADES = 500

# link type -> (node type, property name used in the graph tables)
NODE_LINKS = {"UsesDevice": "Device", "FundedBy": "FundingSource"}

_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_PROJECT = re.compile(r"^[a-z][a-z0-9\-]{4,28}[a-z0-9]$")


class GraphInputError(ValueError):
    """The caller passed something that is not a valid id or range."""


def check_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise GraphInputError(f"invalid {what}: {value!r}")
    return value


def check_project(project: str) -> str:
    if not isinstance(project, str) or not _PROJECT.match(project):
        raise GraphInputError(f"invalid project id: {project!r}")
    return project


def check_hops(hops: int, maximum: int = MAX_NEIGHBOR_HOPS) -> int:
    if isinstance(hops, bool) or not isinstance(hops, int) or not 1 <= hops <= maximum:
        raise GraphInputError(f"hops must be an integer from 1 to {maximum}")
    return hops


def check_accounts(account_ids) -> list[str]:
    ids = sorted({check_id(a, "account_id") for a in account_ids})
    if not ids or len(ids) > MAX_ACCOUNTS:
        raise GraphInputError(f"account_ids must hold 1 to {MAX_ACCOUNTS} ids")
    return ids


def seed_of(ring_id: str) -> str:
    """ring_id is 'R_<lowest account id of the component>' (P2 detector)."""
    check_id(ring_id, "ring_id")
    if not ring_id.startswith("R_") or len(ring_id) == 2:
        raise GraphInputError(f"ring_id must look like R_<account_id>: {ring_id!r}")
    return ring_id[2:]


def node_id(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def edge_id(kind: str, source: str, target: str) -> str:
    return f"{kind}:{source}>{target}"


def make_edge(kind: str, source: tuple[str, str], target: tuple[str, str]) -> dict:
    """source/target are (node type, key)."""
    return {
        "id": edge_id(kind, source[1], target[1]),
        "type": kind,
        "source": node_id(*source),
        "target": node_id(*target),
    }


@dataclass
class Expansion:
    distance: dict[str, int]
    edges: dict[str, dict] = field(default_factory=dict)
    truncated: bool = False


def hop_edges(
    node_rows: Iterable[tuple[str, str, str]], fills: Iterable[tuple[str, str]]
) -> tuple[dict[str, dict], set[str]]:
    """Edges and reached accounts from one hop of raw rows.

    ``node_rows`` are (link type, account, node key) for the frontier accounts AND every
    account sharing the node; ``fills`` are (seller, buyer) pairs touching the frontier.
    Nodes shared by fewer than 2 or more than HUB_LIMIT accounts are dropped.
    """
    members: dict[tuple[str, str], set[str]] = defaultdict(set)
    for kind, account, node in node_rows:
        members[(kind, node)].add(account)
    edges: dict[str, dict] = {}
    reached: set[str] = set()
    for (kind, node), accounts in members.items():
        if not 2 <= len(accounts) <= HUB_LIMIT:
            continue
        reached |= accounts
        for a in accounts:
            e = make_edge(kind, ("Account", a), (NODE_LINKS[kind], node))
            edges[e["id"]] = e
    for seller, buyer in fills:
        e = make_edge("TradedWith", ("Account", seller), ("Account", buyer))
        edges[e["id"]] = e
        reached |= {seller, buyer}
    return edges, reached


def to_response(seed_accounts: list[str], expansion: Expansion) -> dict:
    """Sorted, JSON-ready nodes and edges."""
    nodes: dict[str, dict] = {}
    for a in list(expansion.distance):
        nodes[node_id("Account", a)] = {"id": node_id("Account", a), "type": "Account", "key": a}
    for e in expansion.edges.values():
        for end in (e["source"], e["target"]):
            kind, key = end.split(":", 1)
            nodes.setdefault(end, {"id": end, "type": kind, "key": key})
    return {
        "nodes": [nodes[k] for k in sorted(nodes)],
        "edges": [expansion.edges[k] for k in sorted(expansion.edges)],
        "truncated": expansion.truncated,
    }
