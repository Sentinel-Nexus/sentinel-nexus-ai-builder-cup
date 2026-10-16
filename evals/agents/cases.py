"""Eval cases: the real detector alerts plus injected review-band alerts, and the labels.

Labels (eval.ground_truth, eval.decoy_groups, the evasion marker) are read here for scoring only.
Agents never see them: the account_profile tool hides raw device and funding ids, and injected
alerts carry only detector fields.

Seed 42 has no needs_review alert, so the borderline cases are built from live accounts:
  - EV_<attacker>: the one attacker that scored in the review band (42.99), as its own alert
  - EV_<decoy group>: each of the three decoy groups, at their real detector scores
The decoys score far below the band, so clearing them tests the investigation, not the detector.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from services.app.data import DataStore
from services.detector.scoring import scenario_hint

THRESHOLD = json.loads(
    (Path(__file__).resolve().parents[2] / "services" / "detector" / "threshold.json").read_text()
)
BAND_LOW = THRESHOLD["threshold"] - THRESHOLD["review_margin"]


def load_labels(client, project: str) -> dict:
    def rows(table):
        return [dict(r.items()) for r in client.list_rows(f"{project}.{table}")]

    truth = rows("eval.ground_truth")
    decoys = rows("eval.decoy_groups")
    accounts = {r["account_id"]: r for r in rows("risk_curated.accounts")}
    attackers = {r["account_id"]: r["scenario_type"] for r in truth}
    return {
        "attackers": attackers,
        # an attacker that left the shared funding source got a unique FE_ id (ADR 0002)
        "evaded": {a for a in attackers if str(accounts[a]["funding_source_id"]).startswith("FE_")},
        "decoy_groups": {
            g: sorted(r["account_id"] for r in decoys if r["group_id"] == g)
            for g in sorted({r["group_id"] for r in decoys})
        },
    }


def _injected(store: DataStore, alert_id: str, members: list[str], kind: str) -> dict:
    scores = [store.score_of(a) for a in members]
    n = len(members)
    terms: dict[str, float] = defaultdict(float)
    contrib: dict[str, float] = defaultdict(float)
    for s in scores:
        for k, v in (s["features"] or {}).items():
            terms[k] += v / n
        for k, v in (s["contributions"] or {}).items():
            contrib[k] += v / n
    top = sorted(contrib.items(), key=lambda kv: -kv[1])[:4]
    ring = sorted(s["ring_id"] for s in scores if s["ring_id"])
    return {
        "alert_id": alert_id,
        "ring_id": ring[0] if ring else f"R_{members[0]}",
        "status": "needs_review",
        "scenario_hint": scenario_hint(dict(terms)),
        "member_accounts": sorted(members),
        "n_members": n,
        "score": round(sum(s["score"] for s in scores) / n, 4),
        "top_features": json.dumps({k: round(v, 2) for k, v in top}),
        "evidence_trade_ids": [],
        "evidence_edge_ids": [],
        "threshold": THRESHOLD["threshold"],
        "run_id": "eval-injected",
        "created_ts": "2026-10-16 00:00:00",
        "n_flagged": 0,
        "injected": kind,
    }


def injected_alerts(store: DataStore, labels: dict) -> list[dict]:
    out = []
    band = [
        a
        for a in labels["attackers"]
        if (s := store.score_of(a)) and not s["flagged"] and s["score"] >= BAND_LOW
    ]
    for a in sorted(band):
        out.append(_injected(store, f"EV_{a}", [a], "borderline_attacker"))
    for group, members in labels["decoy_groups"].items():
        out.append(_injected(store, f"EV_{group}", members, "decoy"))
    return out
