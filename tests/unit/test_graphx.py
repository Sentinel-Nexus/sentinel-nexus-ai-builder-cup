"""Case-graph helpers (caption, feature names, story steps, merge, layout), run in Chromium."""

from __future__ import annotations

from pathlib import Path

SCRIPT = (Path(__file__).parent / "js" / "graphx_probe.js").read_text(encoding="utf-8")


def test_caption_numbers_match_the_payload(js_page):
    r = js_page.evaluate(SCRIPT)
    w = r["wash"]
    assert (w["n"], w["d"], w["f"]) == (8, 2, 1)
    assert w["caption"] == (
        "8 accounts trade back and forth with each other and connect through "
        "2 shared devices and 1 shared funding source. Real traders rarely do both."
    )
    assert (
        r["mule"]["caption"]
        == "25 accounts are connected through 3 shared devices and 1 shared funding source."
    )
    assert (
        r["single"]["caption"]
        == "2 accounts use 1 shared device, so some of them may be run by the same person."
    )


def test_feature_names_and_bars(js_page):
    r = js_page.evaluate(SCRIPT)
    assert r["features"] == [
        "Accounts buying from and selling to each other",
        "Size of the connected group",
        "Newly created accounts funded together",
        "How tightly accounts are connected",
        "Some new signal",
        "Unnamed signal",
    ]
    assert [(b["key"], b["pct"]) for b in r["bars"]] == [
        ("fill_reciprocity", 100),
        ("link_size", 87),
    ]


def test_story_steps_reveal_layers_in_order_with_counts(js_page):
    w = js_page.evaluate(SCRIPT)["wash"]
    assert len(w["steps"]) == 4
    assert "8 accounts" in w["steps"][0] and "2 devices are used" in w["steps"][1]
    assert "1 funding source feeds 8" in w["steps"][2]
    assert f"{w['pairs']} pairs" in w["steps"][3]
    assert w["layers"] == [
        {"devices": False, "funding": False, "trades": False},
        {"devices": True, "funding": False, "trades": False},
        {"devices": True, "funding": True, "trades": False},
        {"devices": True, "funding": True, "trades": True},
    ]


def test_dense_rings_merge_reciprocal_trades_into_pairs(js_page):
    w = js_page.evaluate(SCRIPT)["wash"]
    assert w["dense"] and w["edges"] > 40
    assert w["merged"] == w["pairs"] < w["edges"]


def test_hub_and_account_facts_and_node_size(js_page):
    r = js_page.evaluate(SCRIPT)
    assert r["single"]["hub"] == "2 accounts use this device."
    assert r["single"]["acct"]["facts"] == ["Shares a device with 1 other account."]
    assert r["single"]["linked"].startswith("Linked to the group")
    assert r["sizes"][0] == 18 and r["sizes"][1] == 46 and r["sizes"][2] == 46
    assert 18 < r["sizes"][3] < 46


def test_layout_is_deterministic_and_nothing_overlaps(js_page):
    lay = js_page.evaluate(SCRIPT)["layout"]
    assert lay["same"] and lay["all"] == 11
    assert lay["washMin"] >= 40 and lay["muleMin"] >= 40
    assert lay["circleMin"] >= 40 and lay["circleWash"] >= 40


def test_cited_accounts_come_from_evidence_ids(js_page):
    r = js_page.evaluate(SCRIPT)
    assert r["cited"] == ["W001", "W002", "W003", "W004", "W005"]  # unknown ids are ignored
