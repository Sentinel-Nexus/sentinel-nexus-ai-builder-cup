"""WCAG AA for the workbench's colour tokens (text 4.5:1, interface boundaries 3:1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[2] / "services" / "app" / "static" / "css" / "app.css"

TEXT_PAIRS = [
    ("text", "bg"), ("text", "surface"), ("text", "surface-2"), ("text-2", "bg"),
    ("text-2", "surface"), ("text-2", "surface-2"), ("accent", "surface"), ("accent", "bg"),
    ("accent", "accent-soft"), ("accent-ink", "accent"), ("crit", "crit-bg"),
    ("high", "high-bg"), ("med", "med-bg"), ("ok", "ok-bg"), ("review", "review-bg"),
    ("neutral", "neutral-bg"), ("crit", "surface"), ("crit", "bg"), ("text", "accent-soft"),
    ("neutral", "surface"),
]  # fmt: skip
BOUNDARY_PAIRS = [("border-strong", "surface"), ("border-strong", "bg"), ("g-edge", "surface"),
                  ("g-account", "surface"), ("g-device", "surface"), ("g-funding", "surface"),
                  ("g-hot", "surface")]  # fmt: skip


def tokens(selector_start: str) -> dict[str, str]:
    css = CSS.read_text(encoding="utf-8")
    start = css.index(selector_start)
    block = css[css.index("{", start) + 1 : css.index("}", start)]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def themes() -> dict[str, dict[str, str]]:
    light = tokens(":root {")
    dark = {**light, **tokens(':root[data-theme="dark"]')}
    return {"light": light, "dark": dark}


def luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_pairs_meet_aa(theme):
    t = themes()[theme]
    bad = {(f, b): round(ratio(t[f], t[b]), 2) for f, b in TEXT_PAIRS if ratio(t[f], t[b]) < 4.5}
    assert not bad, bad


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_boundaries_meet_3_to_1(theme):
    t = themes()[theme]
    bad = {(f, b): round(ratio(t[f], t[b]), 2) for f, b in BOUNDARY_PAIRS if ratio(t[f], t[b]) < 3}
    assert not bad, bad
