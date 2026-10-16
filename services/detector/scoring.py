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


def _freshness(r: Row) -> float:
    age = r["age_first_trade_h"]
    return 0.0 if age is None else math.exp(-max(age, 0.0) / 72.0)


FEATURES: tuple[Feature, ...] = (
    Feature("link_size", 0.12, lambda r: clip((r["comp_size"] - 1) / 7)),
    Feature(
        "link_density",
        0.08,
        lambda r: clip(r["comp_density"]) if r["comp_size"] >= 3 else 0.0,
    ),
    Feature("fill_reciprocity", 0.15, lambda r: clip(r["recip"]) * clip(r["fill_n"] / 10)),
    Feature("fresh_funding_fanin", 0.10, lambda r: clip(r["fresh_fanin"] / 4)),
    Feature("account_freshness", 0.10, _freshness),
    Feature("fresh_cluster", 0.10, lambda r: clip(r["fresh_linked"] / 4)),
    Feature(
        "late_concentration",
        0.15,
        lambda r: clip(r["late_share"]) * clip(r["late_usd"] / 1000),
    ),
    Feature("size_zscore", 0.10, lambda r: clip(r["mean_size_z"] / 3)),
    Feature("timing_zscore", 0.10, lambda r: clip(r["mean_late_z"] / 2)),
)
assert abs(sum(f.weight for f in FEATURES) - 1.0) < 1e-9

# Ring structure the hint looks for. Reciprocal matched fills and late one-sided buying are
# specific; shared funding into fresh accounts is generic (every ring type has it), so it only
# decides the hint when neither specific pattern is present. Fixed a priori (a quarter of a
# term's range), not fitted.
SIGNATURE_MIN = 0.25


def score_row(row: Row) -> tuple[float, dict[str, float], dict[str, float]]:
    """-> (score 0..100, feature terms, per-feature contribution in score points)."""
    terms = {f.name: f.term(row) for f in FEATURES}
    contributions = {f.name: 100 * f.weight * terms[f.name] for f in FEATURES}
    return sum(contributions.values()), terms, contributions


def score_rows(rows: Iterable[Row], threshold: float, review_margin: float = 0.0) -> list[Row]:
    """Score rows. flagged: score >= threshold; in_band: threshold - margin <= score < threshold."""
    out = []
    for row in rows:
        score, terms, contributions = score_row(row)
        out.append(
            {
                "account_id": row["account_id"],
                "ring_id": row["ring_id"],
                "score": round(score, 4),
                "flagged": score >= threshold,
                "in_band": threshold - review_margin <= score < threshold,
                "threshold": threshold,
                "terms": terms,
                "contributions": contributions,
                "row": row,
            }
        )
    return out


def scenario_hint(terms: dict[str, float]) -> str:
    """Name the pattern a ring's mean feature terms point at, from ring structure only.

    Specific signatures first (reciprocal matched fills, late one-sided buying near
    resolution; the stronger wins), then funding fan-in into fresh accounts, else
    'unclassified'. Link size, density and freshness are shared by all patterns and never vote.
    """
    specific = {
        "wash_trading": terms.get("fill_reciprocity", 0.0),
        "coordinated_accumulation": terms.get("late_concentration", 0.0),
    }
    best = max(specific, key=specific.__getitem__)
    if specific[best] >= SIGNATURE_MIN:
        return best
    funding = max(terms.get("fresh_funding_fanin", 0.0), terms.get("fresh_cluster", 0.0))
    return "mule_funding" if funding >= SIGNATURE_MIN else "unclassified"


def build_alerts(scored: list[Row], run_id: str, created_ts: str) -> list[Row]:
    """One alert per ring (connected component) with a flagged or review-band account.

    status is 'flagged' when any member scores at or above the threshold, else
    'needs_review' (every member is in the band just below it). Band members of a
    flagged ring stay in the alert so an analyst sees them; n_flagged counts the
    members that cleared the threshold."""
    rings: dict[str, list[Row]] = defaultdict(list)
    for s in scored:
        if s["flagged"] or s.get("in_band"):
            rings[s["ring_id"]].append(s)
    alerts = []
    for ring_id, members in sorted(rings.items()):
        members.sort(key=lambda s: s["account_id"])
        total: dict[str, float] = defaultdict(float)
        mean_terms: dict[str, float] = defaultdict(float)
        for m in members:
            for name, value in m["contributions"].items():
                total[name] += value / len(members)
            for name, value in m["terms"].items():
                mean_terms[name] += value / len(members)
        top = sorted(total.items(), key=lambda kv: -kv[1])[:4]
        ids = sorted(m["account_id"] for m in members)
        n_flagged = sum(1 for m in members if m["flagged"])
        digest = hashlib.sha1(f"{run_id}|{ring_id}".encode()).hexdigest()[:10]
        alerts.append(
            {
                "alert_id": f"AL_{digest}",
                "ring_id": ring_id,
                "status": "flagged" if n_flagged else "needs_review",
                "n_flagged": n_flagged,
                "scenario_hint": scenario_hint(mean_terms),
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
