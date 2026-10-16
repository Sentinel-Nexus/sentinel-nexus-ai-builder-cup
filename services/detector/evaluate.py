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
