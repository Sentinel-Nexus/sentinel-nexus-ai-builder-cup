"""Scoring, alerts, evaluation metrics and SQL rendering of the P2 detector (no BigQuery)."""

from __future__ import annotations

import json

import pytest

from services.detector import evaluate, pipeline, scoring
from services.detector.__main__ import choose_threshold

QUIET = {
    "account_id": "x1",
    "ring_id": "R_x1",
    "comp_size": 1,
    "comp_density": 0.0,
    "recip": 0.0,
    "fill_n": 0,
    "fresh_fanin": 0,
    "age_first_trade_h": 3000.0,
    "fresh_linked": 0,
    "late_share": 0.0,
    "late_usd": 0.0,
    "mean_size_z": 0.0,
    "mean_late_z": 0.0,
    "evidence_trade_ids": [],
    "evidence_edge_ids": [],
}


def row(**kw):
    return {**QUIET, **kw}


def test_weights_sum_to_one_and_terms_are_bounded():
    assert sum(f.weight for f in scoring.FEATURES) == pytest.approx(1.0)
    extreme = row(
        comp_size=500,
        comp_density=5,
        recip=3,
        fill_n=900,
        fresh_fanin=99,
        age_first_trade_h=-5,
        fresh_linked=99,
        late_share=4,
        late_usd=1e9,
        mean_size_z=99,
        mean_late_z=99,
    )
    score, terms, contributions = scoring.score_row(extreme)
    assert all(0 <= t <= 1 for t in terms.values())
    assert score <= 100.0 + 1e-6
    assert sum(contributions.values()) == pytest.approx(score)


def test_quiet_account_scores_near_zero_and_missing_age_is_not_fresh():
    assert scoring.score_row(QUIET)[0] < 1
    assert scoring.score_row(row(age_first_trade_h=None))[1]["account_freshness"] == 0.0


def test_each_feature_moves_the_score_alone():
    base = scoring.score_row(QUIET)[0]
    for change in (
        {"comp_size": 8},
        {"comp_size": 4, "comp_density": 1.0},
        {"recip": 1.0, "fill_n": 20},
        {"fresh_fanin": 4},
        {"age_first_trade_h": 1.0},
        {"fresh_linked": 4},
        {"late_share": 1.0, "late_usd": 2000.0},
        {"mean_size_z": 3.0},
        {"mean_late_z": 2.0},
    ):
        assert scoring.score_row(row(**change))[0] > base, change


def test_score_rows_flags_at_threshold_and_keeps_contributions():
    rows = [row(account_id="a", comp_size=8, comp_density=1.0), row(account_id="b")]
    scored = scoring.score_rows(rows, threshold=15)
    assert [s["flagged"] for s in scored] == [True, False]
    assert set(scored[0]["contributions"]) == {f.name for f in scoring.FEATURES}


def test_alert_groups_flagged_accounts_by_ring_with_evidence_and_hint():
    rows = [
        row(
            account_id=f"m{i}",
            ring_id="R_m",
            comp_size=6,
            comp_density=0.8,
            recip=1.0,
            fill_n=40,
            evidence_trade_ids=[f"t{i}", "shared"],
            evidence_edge_ids=[f"e{i}"],
        )
        for i in range(3)
    ] + [row(account_id="solo", ring_id="R_solo")]
    alerts = scoring.build_alerts(scoring.score_rows(rows, 25), "run1", "2026-10-16 00:00:00")
    assert len(alerts) == 1
    a = alerts[0]
    assert a["ring_id"] == "R_m" and a["member_accounts"] == ["m0", "m1", "m2"]
    assert a["scenario_hint"] == "wash_trading" and a["n_members"] == 3
    assert a["evidence_trade_ids"] == ["t0", "shared", "t1", "t2"]
    assert a["evidence_edge_ids"] == ["e0", "e1", "e2"]
    assert list(json.loads(a["top_features"]))[0] == "fill_reciprocity"


def test_hint_follows_the_contributing_pattern_not_labels():
    late = {f.name: 0.0 for f in scoring.FEATURES} | {"late_concentration": 9.0}
    fresh = {f.name: 0.0 for f in scoring.FEATURES} | {"account_freshness": 9.0}
    assert scoring.scenario_hint(late) == "coordinated_accumulation"
    assert scoring.scenario_hint(fresh) == "mule_funding"
    assert scoring.scenario_hint({f.name: 0.0 for f in scoring.FEATURES}) == "unclassified"


TRUTH = {"w1": "wash", "w2": "wash", "m1": "mule", "m2": "mule"}
DECOYS = {"G1": ["d1", "d2"], "G2": ["d3"]}
ACCOUNTS = set(TRUTH) | {"d1", "d2", "d3", "n1", "n2", "n3"}


def by(rows, kind, name):
    return next(r for r in rows if r["group_kind"] == kind and r["group_name"] == name)


def test_metrics_overall_scenario_and_decoy_rows():
    flagged = {"w1", "w2", "m1", "d1", "n1"}
    rows = evaluate.metrics(flagged, TRUTH, DECOYS, ACCOUNTS)
    overall = by(rows, "overall", "all_scenarios")
    assert (overall["true_pos"], overall["false_pos"]) == (3, 2)
    assert overall["precision"] == pytest.approx(3 / 5) and overall["recall"] == pytest.approx(
        3 / 4
    )
    wash = by(rows, "scenario", "wash")
    assert wash["recall"] == 1.0 and wash["precision"] == pytest.approx(2 / 4)
    assert by(rows, "scenario", "mule")["recall"] == 0.5
    assert by(rows, "decoy", "G1")["flagged"] == 1 and by(rows, "decoy", "G2")["flagged"] == 0
    background = by(rows, "background", "normal_accounts")
    assert (background["n_members"], background["flagged"]) == (3, 1)


def test_metrics_with_nothing_flagged_has_no_precision():
    rows = evaluate.metrics(set(), TRUTH, DECOYS, ACCOUNTS)
    overall = by(rows, "overall", "all_scenarios")
    assert overall["precision"] is None and overall["recall"] == 0.0 and overall["f1"] is None


def test_threshold_is_chosen_on_validation_inputs_only():
    clean = row(account_id="a1", comp_size=8, comp_density=1.0, recip=1.0, fill_n=20)
    inputs = {
        s: {
            "features": [clean, row(account_id="n1")],
            "truth": {"a1": "wash"},
            "decoys": {},
        }
        for s in (7, 11)
    }
    threshold, info = choose_threshold(inputs)
    low, high = scoring.score_row(row(account_id="n1"))[0], scoring.score_row(clean)[0]
    assert low < threshold <= high and info["mean_f1"] == 1.0


def test_views_render_without_placeholders_for_live_and_validation():
    for target in (pipeline.live_target("proj"), pipeline.validation_target("proj", 7)):
        for name in pipeline.STEPS:
            sql = pipeline.render_step((pipeline.VIEWS / name).read_text("utf-8"), "proj", target)
            assert "__" not in sql
            assert target.out_dataset in sql or name == "baseline_flat_rule.sql"
    assert "proj.eval_val.s7_det_fills" in pipeline.render_step(
        (pipeline.VIEWS / "det_20_links.sql").read_text("utf-8"),
        "proj",
        pipeline.validation_target("proj", 7),
    )
