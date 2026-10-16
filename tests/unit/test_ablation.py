"""Ablation variants and the shared threshold-selection procedure (no BigQuery)."""

from __future__ import annotations

import pytest

from services.detector import ablation, evaluate, scoring
from tests.unit.test_detector import row


def test_feature_split_covers_every_feature_exactly_once():
    names = {f.name for f in scoring.FEATURES}
    assert set(ablation.GRAPH_FEATURES) | set(ablation.BEHAVIOR_FEATURES) == names
    assert not set(ablation.GRAPH_FEATURES) & set(ablation.BEHAVIOR_FEATURES)


def test_full_variant_equals_the_detector_score_and_subsets_span_zero_to_hundred():
    r = row(account_id="a", comp_size=5, comp_density=0.7, recip=0.5, fill_n=6, late_share=0.4)
    full = ablation.subset_scores([r], ablation.scoring_names())["a"]
    assert full == pytest.approx(scoring.score_row(r)[0])
    maxed = {f.name: 1.0 for f in scoring.FEATURES}
    for names in (ablation.GRAPH_FEATURES, ablation.BEHAVIOR_FEATURES):
        chosen = [f for f in scoring.FEATURES if f.name in names]
        total = sum(f.weight for f in chosen)
        assert 100 * sum(f.weight * maxed[f.name] for f in chosen) / total == pytest.approx(100)


def test_graph_only_ignores_behavior_and_behavior_only_ignores_links():
    linked = row(account_id="a", comp_size=8, comp_density=1.0)
    solo_fresh = row(account_id="b", age_first_trade_h=1.0, mean_size_z=3.0)
    g = ablation.subset_scores([linked, solo_fresh], ablation.GRAPH_FEATURES)
    b = ablation.subset_scores([linked, solo_fresh], ablation.BEHAVIOR_FEATURES)
    assert g["a"] > 0 and g["b"] == 0
    assert b["a"] == pytest.approx(0, abs=1e-9) and b["b"] > 0


def test_components_follow_device_or_funding_and_skip_hubs():
    accounts = [
        {"account_id": f"a{i}", "device_id": f"d{i}", "funding_source_id": f"f{i}"}
        for i in range(40)
    ]
    accounts[1]["device_id"] = accounts[0]["device_id"]  # a0-a1 via device
    accounts[2]["funding_source_id"] = accounts[1]["funding_source_id"]  # a1-a2 via funding
    for a in accounts[10:38]:  # 28 accounts on one funding source: a hub, creates no links
        a["funding_source_id"] = "hub"
    scores = ablation.device_funding_component_scores({"accounts": accounts})
    assert scores["a0"] == scores["a1"] == scores["a2"] == 3
    assert scores["a10"] == 1 and scores["a39"] == 1


def test_flat_funding_score_is_the_group_size():
    feats = [{"account_id": "a", "funding_group": 7}, {"account_id": "b", "funding_group": 1}]
    assert ablation.flat_funding_scores({"features": feats}) == {"a": 7.0, "b": 1.0}


def test_select_threshold_takes_the_plateau_midpoint_over_seeds():
    scores = {1: {"w": 80.0, "n": 10.0}, 2: {"w": 60.0, "n": 20.0}}
    labels = {1: ({"w": "wash"}, {}), 2: ({"w": "wash"}, {})}
    grid = [float(x) for x in range(0, 101, 5)]
    cut, info = evaluate.select_threshold(scores, labels, grid)
    assert info["plateau"] == [25.0, 60.0] and cut == 45.0 and info["mean_f1"] == 1.0
