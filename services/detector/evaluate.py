"""Evaluation: the only detector module allowed to read labels.

Per-scenario precision is one-vs-rest: true positives of that scenario over (those plus every
flagged account that is not an attacker in any scenario). Decoy groups report how many of
their members were flagged (all of them are false positives).
"""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]


def fetch_labels(client, project: str, target):
    """-> ({account_id: scenario_type}, {group_id: [account_id]}) for a pipeline target."""
    if target.name == "live":
        truth_table, decoy_table = f"{project}.eval.ground_truth", f"{project}.eval.decoy_groups"
    else:
        prefix = f"{project}.eval_val.s{target.seed}"
        truth_table, decoy_table = f"{prefix}_ground_truth", f"{prefix}_decoy_groups"
    truth = {
        r["account_id"]: r["scenario_type"]
        for r in client.query(f"SELECT account_id, scenario_type FROM `{truth_table}`").result()
    }
    decoys: dict[str, list[str]] = {}
    for r in client.query(f"SELECT group_id, account_id FROM `{decoy_table}`").result():
        decoys.setdefault(r["group_id"], []).append(r["account_id"])
    return truth, decoys


def _ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None:
        return None
    return 2 * p * r / (p + r) if p + r else 0.0


def metrics(
    flagged: set[str],
    truth: dict[str, str],
    decoys: dict[str, list[str]],
    all_accounts: set[str],
) -> list[Row]:
    """Metric rows: overall, one per scenario, one per decoy group, and the plain background."""
    attackers = set(truth)
    false_pos = flagged - attackers
    rows = []

    def row(kind, name, n, tp, fp, p, r):
        rows.append(
            {
                "group_kind": kind,
                "group_name": name,
                "n_members": n,
                "flagged": tp + fp if kind != "decoy" else fp,
                "true_pos": tp,
                "false_pos": fp,
                "precision": p,
                "recall": r,
                "f1": _f1(p, r),
            }
        )

    tp = len(flagged & attackers)
    p, r = _ratio(tp, len(flagged)), _ratio(tp, len(attackers))
    row("overall", "all_scenarios", len(attackers), tp, len(false_pos), p, r)
    for scenario in sorted(set(truth.values())):
        members = {a for a, s in truth.items() if s == scenario}
        tp_s = len(flagged & members)
        p, r = _ratio(tp_s, tp_s + len(false_pos)), _ratio(tp_s, len(members))
        row("scenario", scenario, len(members), tp_s, len(false_pos), p, r)
    in_decoy: set[str] = set()
    for group_id, members in sorted(decoys.items()):
        in_decoy |= set(members)
        row("decoy", group_id, len(members), 0, len(flagged & set(members)), None, None)
    background = all_accounts - attackers - in_decoy
    row("background", "normal_accounts", len(background), 0, len(flagged & background), None, None)
    return rows


def hint_accuracy(alerts: list[Row], truth: dict[str, str]) -> dict[str, Any]:
    """Compare each alert's scenario_hint with the ground truth of its members.

    An alert's true type is the most common scenario among its members; members that are not
    attackers count as 'none' (a decoy-only or background-only alert has true type 'none' and
    can never be hinted correctly). Returns per-alert rows, a per-scenario summary
    (alerts, correct, accounts, accounts in correctly hinted alerts) and a confusion matrix
    {true type: {hint: alerts}}.
    """
    rows, confusion = [], {}
    summary: dict[str, dict[str, int]] = {}
    for a in alerts:
        kinds = [truth.get(m, "none") for m in a["member_accounts"]]
        true_type = max(sorted(set(kinds)), key=kinds.count)
        ok = a["scenario_hint"] == true_type
        rows.append(
            {
                "alert_id": a["alert_id"],
                "true_type": true_type,
                "hint": a["scenario_hint"],
                "correct": ok,
                "n_members": len(kinds),
                "purity": kinds.count(true_type) / len(kinds),
            }
        )
        confusion.setdefault(true_type, {}).setdefault(a["scenario_hint"], 0)
        confusion[true_type][a["scenario_hint"]] += 1
        s = summary.setdefault(true_type, dict(alerts=0, correct=0, accounts=0, accounts_ok=0))
        s["alerts"] += 1
        s["correct"] += ok
        s["accounts"] += len(kinds)
        s["accounts_ok"] += len(kinds) if ok else 0
    return {"alerts": rows, "by_scenario": summary, "confusion": confusion}


def score_gap(scores: dict[str, float], truth: dict[str, str]) -> float | None:
    """Weakest attacker score minus strongest non-attacker (decoy or background) score."""
    attackers = [v for a, v in scores.items() if a in truth]
    others = [v for a, v in scores.items() if a not in truth]
    return min(attackers) - max(others) if attackers and others else None


def band_counts(
    scores: dict[str, float],
    truth: dict[str, str],
    decoys: dict[str, list[str]],
    threshold: float,
    margin: float,
) -> dict[str, int]:
    """Accounts with threshold - margin <= score < threshold, split by ground-truth kind."""
    band = {a for a, v in scores.items() if threshold - margin <= v < threshold}
    decoy_ids = {a for members in decoys.values() for a in members}
    return {
        "attackers": len(band & set(truth)),
        "decoys": len(band & decoy_ids),
        "background": len(band - set(truth) - decoy_ids),
    }
