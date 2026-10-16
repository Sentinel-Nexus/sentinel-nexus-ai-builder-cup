"""Hardening report: robustness, ablation, hint accuracy, review band and threshold sweep.

Pure computation over already-built detector inputs (see ``__main__.load_inputs``); it reads
labels only through ``evaluate``. Nothing here selects the full detector's threshold: that stays
frozen in threshold.json. Ablation variants get their own cut-off chosen on the validation seeds
only, with the same procedure.
"""

from __future__ import annotations

import statistics
from typing import Any

from services.detector import ablation, evaluate, scoring

GRID = [x / 2 for x in range(0, 201)]
SWEEP = [float(t) for t in range(0, 101)]


def _overall(rows: list[dict]) -> dict[str, Any]:
    r = next(r for r in rows if r["group_kind"] == "overall")
    keys = ("n_members", "flagged", "true_pos", "false_pos", "precision", "recall", "f1")
    return {k: r[k] for k in keys}


def _scores(inp: dict) -> dict[str, float]:
    return {s["account_id"]: s["score"] for s in scoring.score_rows(inp["features"], 0.0)}


def seed_report(inp: dict, threshold: float, margin: float) -> dict[str, Any]:
    scored = scoring.score_rows(inp["features"], threshold, margin)
    scores = {s["account_id"]: s["score"] for s in scored}
    flagged = {s["account_id"] for s in scored if s["flagged"]}
    truth, decoys = inp["truth"], inp["decoys"]
    det = evaluate.metrics(flagged, truth, decoys, set(scores))
    base = evaluate.metrics(inp["baseline"], truth, decoys, set(scores))
    alerts = scoring.build_alerts(scored, "report", "1970-01-01 00:00:00")
    missed = sorted(
        (
            {
                "scenario": truth[a],
                "score": round(scores[a], 2),
                "in_band": any(s["account_id"] == a and s["in_band"] for s in scored),
            }
            for a in truth
            if a not in flagged
        ),
        key=lambda m: -m["score"],
    )
    nonattacker = [v for a, v in scores.items() if a not in truth]
    return {
        "detector": _overall(det),
        "baseline": _overall(base),
        "scenarios": {
            r["group_name"]: {k: r[k] for k in ("n_members", "true_pos", "recall")}
            for r in det
            if r["group_kind"] == "scenario"
        },
        "decoys_flagged": {
            r["group_name"]: {"n": r["n_members"], "detector": r["false_pos"]}
            for r in det
            if r["group_kind"] == "decoy"
        },
        "background_flagged": next(r for r in det if r["group_kind"] == "background")["false_pos"],
        "band": evaluate.band_counts(scores, truth, decoys, threshold, margin),
        "missed": missed,
        "alerts": {
            "flagged": sum(a["status"] == "flagged" for a in alerts),
            "needs_review": sum(a["status"] == "needs_review" for a in alerts),
        },
        "hint": evaluate.hint_accuracy(alerts, truth),
        "weakest_attacker": round(min(scores[a] for a in truth), 2),
        "strongest_nonattacker": round(max(nonattacker), 2),
    }


def sweep(inp: dict) -> dict[str, list[float | None]]:
    scores, truth, decoys = _scores(inp), inp["truth"], inp["decoys"]
    out: dict[str, list[float | None]] = {"precision": [], "recall": [], "f1": []}
    for t in SWEEP:
        flagged = {a for a, v in scores.items() if v >= t}
        o = _overall(evaluate.metrics(flagged, truth, decoys, set(scores)))
        for k in out:
            out[k].append(o[k])
    return out


def ablate(inputs: dict[int, dict], validation: list[int], heldout: list[int]) -> dict[str, Any]:
    result = {}
    for key, (label, scorer) in ablation.VARIANTS.items():
        scores = {seed: scorer(inp) for seed, inp in inputs.items()}
        labels = {s: (inputs[s]["truth"], inputs[s]["decoys"]) for s in inputs}
        cutoff, info = evaluate.select_threshold(
            {s: scores[s] for s in validation}, {s: labels[s] for s in validation}, GRID
        )
        seeds = {}
        for seed in [*validation, *heldout]:
            truth, decoys = labels[seed]
            flagged = {a for a, v in scores[seed].items() if v >= cutoff}
            seeds[seed] = _overall(evaluate.metrics(flagged, truth, decoys, set(scores[seed])))
        result[key] = {"label": label, "cutoff": cutoff, "selection": info, "seeds": seeds}
    return result


def spread(values: list[float | None]) -> dict[str, float | None]:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"min": None, "median": None, "max": None}
    return {"min": min(vals), "median": statistics.median(vals), "max": max(vals)}


def build(
    inputs: dict[int, dict],
    *,
    threshold: float,
    margin: float,
    validation: list[int],
    evaluation: int,
    robustness: list[int],
    profiles: dict[int, dict],
    validation_gaps: dict[str, float],
) -> dict[str, Any]:
    seeds = {s: seed_report(inputs[s], threshold, margin) for s in inputs}
    heldout = [evaluation, *robustness]
    return {
        "threshold": threshold,
        "review_margin": margin,
        "validation_gaps": validation_gaps,
        "validation_seeds": validation,
        "evaluation_seed": evaluation,
        "robustness_seeds": robustness,
        "profiles": {str(k): v for k, v in profiles.items()},
        "seeds": {str(s): r for s, r in seeds.items()},
        "robustness": {
            m: spread([seeds[s]["detector"][m] for s in robustness])
            for m in ("precision", "recall", "f1")
        }
        | {
            "baseline_" + m: spread([seeds[s]["baseline"][m] for s in robustness])
            for m in ("precision", "recall", "f1")
        },
        "ablation": ablate(inputs, validation, heldout),
        "sweep": {
            "thresholds": SWEEP,
            "seeds": {str(s): sweep(inputs[s]) for s in inputs},
        },
    }
