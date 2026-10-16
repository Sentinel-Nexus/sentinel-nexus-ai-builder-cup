"""Explainable account score: fixed weights over bounded feature terms.

Every term is in [0, 1]; the score is 100 * sum(weight * term), so each feature's
contribution is stored next to the score. Weights are set a priori, never fitted on the
evaluation seed. Only the alert threshold is chosen from data, on the validation seeds.
Nothing here depends on an account, device or funding-source id.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

Row = dict[str, Any]
MAX_EVIDENCE = 100


def clip(x: float | None, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x is None else max(lo, min(hi, x))


@dataclass(frozen=True)
class Feature:
    name: str
    weight: float
    term: Callable[[Row], float]
    group: str  # which kind of pattern the feature points at (used only for the hint)


def _freshness(r: Row) -> float:
    age = r["age_first_trade_h"]
    return 0.0 if age is None else math.exp(-max(age, 0.0) / 72.0)


FEATURES: tuple[Feature, ...] = (
    Feature("link_size", 0.12, lambda r: clip((r["comp_size"] - 1) / 7), "linked"),
    Feature(
        "link_density",
        0.08,
        lambda r: clip(r["comp_density"]) if r["comp_size"] >= 3 else 0.0,
        "linked",
    ),
    Feature("fill_reciprocity", 0.15, lambda r: clip(r["recip"]) * clip(r["fill_n"] / 10), "wash"),
    Feature("fresh_funding_fanin", 0.10, lambda r: clip(r["fresh_fanin"] / 4), "mule"),
    Feature("account_freshness", 0.10, _freshness, "mule"),
    Feature("fresh_cluster", 0.10, lambda r: clip(r["fresh_linked"] / 4), "mule"),
    Feature(
        "late_concentration",
        0.15,
        lambda r: clip(r["late_share"]) * clip(r["late_usd"] / 1000),
        "coordination",
    ),
    Feature("size_zscore", 0.10, lambda r: clip(r["mean_size_z"] / 3), "coordination"),
    Feature("timing_zscore", 0.10, lambda r: clip(r["mean_late_z"] / 2), "coordination"),
)
assert abs(sum(f.weight for f in FEATURES) - 1.0) < 1e-9

HINTS = {"wash": "wash_trading", "coordination": "coordinated_accumulation", "mule": "mule_funding"}


def score_row(row: Row) -> tuple[float, dict[str, float], dict[str, float]]:
    """-> (score 0..100, feature terms, per-feature contribution in score points)."""
    terms = {f.name: f.term(row) for f in FEATURES}
    contributions = {f.name: 100 * f.weight * terms[f.name] for f in FEATURES}
    return sum(contributions.values()), terms, contributions


def score_rows(rows: Iterable[Row], threshold: float) -> list[Row]:
    out = []
    for row in rows:
        score, terms, contributions = score_row(row)
        out.append(
            {
                "account_id": row["account_id"],
                "ring_id": row["ring_id"],
                "score": round(score, 4),
                "flagged": score >= threshold,
                "threshold": threshold,
                "terms": terms,
                "contributions": contributions,
                "row": row,
            }
        )
    return out


def scenario_hint(contributions: dict[str, float]) -> str:
    """Name the pattern the ring's evidence mostly points at ('linked' alone gives no hint)."""
    by_group: dict[str, float] = defaultdict(float)
    for f in FEATURES:
        by_group[f.group] += contributions.get(f.name, 0.0)
    best = max(HINTS, key=lambda g: by_group[g])
    return HINTS[best] if by_group[best] > 0 else "unclassified"


def build_alerts(scored: list[Row], run_id: str, created_ts: str) -> list[Row]:
    """One alert per ring (connected component) that has at least one flagged account."""
    rings: dict[str, list[Row]] = defaultdict(list)
    for s in scored:
        if s["flagged"]:
            rings[s["ring_id"]].append(s)
    alerts = []
    for ring_id, members in sorted(rings.items()):
        members.sort(key=lambda s: s["account_id"])
        total: dict[str, float] = defaultdict(float)
        for m in members:
            for name, value in m["contributions"].items():
                total[name] += value / len(members)
        top = sorted(total.items(), key=lambda kv: -kv[1])[:4]
        ids = sorted(m["account_id"] for m in members)
        digest = hashlib.sha1(f"{run_id}|{ring_id}".encode()).hexdigest()[:10]
        alerts.append(
            {
                "alert_id": f"AL_{digest}",
                "ring_id": ring_id,
                "scenario_hint": scenario_hint(total),
                "member_accounts": ids,
                "n_members": len(ids),
                "score": round(sum(m["score"] for m in members) / len(members), 4),
                "top_features": json.dumps({k: round(v, 2) for k, v in top}),
                "evidence_trade_ids": _evidence(members, "evidence_trade_ids"),
                "evidence_edge_ids": _evidence(members, "evidence_edge_ids"),
                "threshold": members[0]["threshold"],
                "run_id": run_id,
                "created_ts": created_ts,
            }
        )
    return alerts


def _evidence(members: list[Row], key: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in members:
        for ref in m["row"].get(key) or []:
            seen.setdefault(ref)
    return list(seen)[:MAX_EVIDENCE]


def to_score_table_rows(scored: list[Row], run_id: str, created_ts: str) -> list[Row]:
    """Rows for risk_curated.account_scores (JSON columns as strings)."""
    return [
        {
            "account_id": s["account_id"],
            "ring_id": s["ring_id"],
            "score": s["score"],
            "flagged": s["flagged"],
            "threshold": s["threshold"],
            "features": json.dumps({k: round(v, 4) for k, v in s["terms"].items()}),
            "contributions": json.dumps({k: round(v, 4) for k, v in s["contributions"].items()}),
            "run_id": run_id,
            "created_ts": created_ts,
        }
        for s in scored
    ]
