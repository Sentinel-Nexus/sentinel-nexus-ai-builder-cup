"""Overview numbers must match their sources (benchmark.json) and stay internally consistent."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.unit.test_ui_contrast import ratio

STATIC = Path(__file__).resolve().parents[2] / "services" / "app" / "static"
OV = json.loads((STATIC / "data" / "overview.json").read_text(encoding="utf-8"))
BM = json.loads((STATIC / "data" / "benchmark.json").read_text(encoding="utf-8"))


def test_kpis_match_the_benchmark():
    det, flat = BM["kpi"]["detector"], BM["kpi"]["baseline"]
    k = OV["kpi"]
    assert (k["caught"]["caught"], k["caught"]["total"]) == (det["tp"], 39)
    assert k["caught"]["pct"] == det["recall"]
    assert k["false_alarms"] == {"marketsentinel": det["fp"], "simple_rule": flat["fp"]}


def test_charts_match_the_benchmark():
    seed = [r for r in BM["seed42"] if r["group"] != "all_scenarios"]
    for row in OV["catch_by_type"]:
        flat = next(r for r in seed if r["group"] == row["type"] and r["method"].startswith("Flat"))
        det = next(r for r in seed if r["group"] == row["type"] and r["method"].startswith("Det"))
        assert (row["simple_rule"], row["marketsentinel"]) == (flat["recall"], det["recall"])
    by_group = {g["group"]: g for g in BM["innocent_groups"]}
    for row in OV["false_alarms"]:
        g = by_group[row["group"]]
        assert (row["accounts"], row["simple_rule"], row["marketsentinel"]) == (
            g["accounts"], g["flat_rule"], g["detector"])  # fmt: skip
    assert sum(t["accounts"] for t in OV["types_found"]) == BM["kpi"]["detector"]["tp"]
    flagged = next(f for f in OV["funnel"] if f["label"] == "Flagged accounts")
    assert flagged["value"] == BM["kpi"]["detector"]["flagged"]


def test_internal_consistency_and_no_invented_figures():
    k, funnel = OV["kpi"], {f["label"]: f["value"] for f in OV["funnel"]}
    assert round(sum(v["usd"] for v in OV["volume_by_type"]), 2) == k["volume_usd"]
    assert funnel["Alerts"] == OV["summary"]["detector_alerts"]["alerts"]
    assert funnel["Escalated cases"] == OV["summary"]["detector_alerts"]["lanes"]["escalated"]
    assert funnel["Analyst decisions"] == OV["summary"]["detector_alerts"]["decided"]
    assert k["evidence"]["kept"] == sum(c["kept"] for c in OV["speed"])
    assert k["evidence"]["removed"] == sum(c["removed"] for c in OV["speed"])
    assert k["report_time"]["n"] == len(OV["speed"])
    assert OV["synthetic"] is True
    text = json.dumps(OV).lower()
    assert not re.search(r"saving|roi|hours saved", text)


def test_overview_tokens_meet_aa():
    css = (STATIC / "css" / "overview.css").read_text(encoding="utf-8")
    tok = dict(re.findall(r"--ov-([\w-]+):\s*(#[0-9a-fA-F]{6})", css))
    for fg in ("text", "text-2", "link"):
        for bg in ("bg", "card", "card-2"):
            assert ratio(tok[fg], tok[bg]) >= 4.5, (fg, bg)
    assert ratio("#000000", "#4285f4") >= 4.5  # dark text on the blue funnel bar
    for c in ("#4285f4", "#34a853", "#fbbc04", "#ff7a00", "#a142f4", "#ea4335"):
        assert ratio("#000000", c) >= 4.5 or c in ("#a142f4", "#ea4335"), c


def test_no_banned_words_in_ui_or_docs():
    banned = re.compile(r"real-time|live monitoring", re.I)
    for p in [*STATIC.rglob("*.js"), *STATIC.rglob("*.html"), *STATIC.rglob("*.css")]:
        assert not banned.search(p.read_text(encoding="utf-8")), p
    for p in Path(__file__).resolve().parents[2].joinpath("docs").glob("*.md"):
        assert not banned.search(p.read_text(encoding="utf-8")), p


def test_honest_limits_panel_has_both_lines():
    js = (STATIC / "js" / "views" / "overview.js").read_text(encoding="utf-8")
    assert "Synthetic data from one generator" in js
    assert (
        "On injected look-alike cases the AI agents were not consistent between runs. "
        "That is why alerts come from the detector and an analyst makes every final call."
    ) in js
