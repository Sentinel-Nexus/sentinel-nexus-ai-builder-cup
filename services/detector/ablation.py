"""Ablation variants: which parts of the detector earn their place.

Every variant is a function from one target's inputs to ``{account_id: score}``; a higher score
is more suspicious and the cut-off is chosen on the validation seeds with the same plateau
procedure as the full detector (``evaluate.select_threshold``). The feature split is fixed
a priori by what each feature reads:

* graph: needs links between accounts (shared device or funding, matched fills, linked fresh
  accounts);
* behavior: needs only one account's own trades and age.

Subset scores renormalise the original weights so they still span 0..100. No ids, no labels.
"""

from __future__ import annotations

from collections.abc import Callable

from services.detector import scoring

GRAPH_FEATURES = (
    "link_size",
    "link_density",
    "fill_reciprocity",
    "fresh_funding_fanin",
    "fresh_cluster",
)
BEHAVIOR_FEATURES = ("account_freshness", "late_concentration", "size_zscore", "timing_zscore")
HUB_LIMIT = 25  # same hub rule as det_20_links.sql: larger shared nodes create no links

Inputs = dict
Scorer = Callable[[Inputs], dict[str, float]]


def subset_scores(features: list[dict], names: tuple[str, ...]) -> dict[str, float]:
    chosen = [f for f in scoring.FEATURES if f.name in names]
    assert {f.name for f in chosen} == set(names), "unknown feature name"
    total = sum(f.weight for f in chosen)
    return {
        r["account_id"]: 100 * sum(f.weight * f.term(r) for f in chosen) / total for r in features
    }


def flat_funding_scores(inp: Inputs) -> dict[str, float]:
    """Size of the account's funding-source group; cut-off k is 'flag groups of k or more'."""
    return {r["account_id"]: float(r["funding_group"]) for r in inp["features"]}


def device_funding_component_scores(inp: Inputs) -> dict[str, float]:
    """Size of the connected component over shared devices or funding sources only.

    No matched fills and no behaviour. Nodes shared by more than HUB_LIMIT accounts create no
    links, as in the full detector.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_node: dict[tuple[str, str], list[str]] = {}
    for a in inp["accounts"]:
        parent.setdefault(a["account_id"], a["account_id"])
        by_node.setdefault(("d", a["device_id"]), []).append(a["account_id"])
        by_node.setdefault(("f", a["funding_source_id"]), []).append(a["account_id"])
    for members in by_node.values():
        if 2 <= len(members) <= HUB_LIMIT:
            root = find(members[0])
            for m in members[1:]:
                parent[find(m)] = root
    sizes: dict[str, int] = {}
    for a in parent:
        sizes[find(a)] = sizes.get(find(a), 0) + 1
    return {a: float(sizes[find(a)]) for a in parent}


VARIANTS: dict[str, tuple[str, Scorer]] = {
    "flat_funding": ("Flat funding rule (group size k)", flat_funding_scores),
    "components_only": (
        "Device/funding components only",
        device_funding_component_scores,
    ),
    "graph_only": (
        "Graph features only (no behavior)",
        lambda inp: subset_scores(inp["features"], GRAPH_FEATURES),
    ),
    "behavior_only": (
        "Behavior features only (no graph)",
        lambda inp: subset_scores(inp["features"], BEHAVIOR_FEATURES),
    ),
    "full": ("Full detector", lambda inp: subset_scores(inp["features"], scoring_names())),
}


def scoring_names() -> tuple[str, ...]:
    return tuple(f.name for f in scoring.FEATURES)
