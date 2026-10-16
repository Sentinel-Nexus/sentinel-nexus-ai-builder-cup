"""Capture a screenshot of each workbench screen into docs/img (needs Playwright + Chromium).

    python scripts/ui_screens.py --base http://localhost:8080 [--theme light|dark]

Reads only cached views (no live investigation, no model calls). Also reports console errors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
CASE = "AL_584807441a"
SHOTS = [
    ("ui_command", "#/", "table"),
    ("ui_case", f"#/case/{CASE}", "#graph canvas"),
    ("ui_lanes", "#/lanes", ".lanes"),
    ("ui_benchmark", "#/benchmark", "canvas"),
    ("ui_governance", "#/governance", "table"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--theme", default="light", choices=["light", "dark"])
    ap.add_argument("--timeout-s", type=int, default=60)
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=a.theme)
        page = ctx.new_page()
        page.set_default_timeout(a.timeout_s * 1000)
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.base + "/")
        page.evaluate(f"localStorage.setItem('ms-theme','{a.theme}')")
        for name, hash_, wait in SHOTS:
            page.goto(f"{a.base}/{hash_}")
            page.reload()
            page.wait_for_selector(wait)
            page.wait_for_timeout(1200)  # layout settles
            suffix = "" if a.theme == "light" else "_dark"
            path = OUT / f"{name}{suffix}.png"
            page.screenshot(path=str(path), full_page=True)
            print("saved", path.relative_to(OUT.parents[1]))
        browser.close()
    for e in errors:
        print("console error:", e[:200])
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
