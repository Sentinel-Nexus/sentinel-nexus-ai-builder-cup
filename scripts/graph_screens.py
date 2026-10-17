"""Screenshots of the case graph: default view, walk-through step 3 and a toggled view.

    python scripts/graph_screens.py --base http://localhost:8080

Writes docs/img/ui_graph_default.png, ui_graph_story3.png and ui_graph_toggled.png (the graph card
only; cached views, no model calls). Exit 1 on a console error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
CASE = "AL_584807441a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(30000)
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{a.base}/#/case/{CASE}")
        page.wait_for_selector("#graph canvas")
        page.wait_for_selector(".gx-caption")
        page.wait_for_timeout(2500)  # layout, volumes
        card = page.locator("section[aria-label='Ring graph']")
        card.screenshot(path=str(OUT / "ui_graph_default.png"))
        chip = page.locator("button.ev-chip[title='UsesDevice:W001>DW1']").first
        chip.click()  # the existing evidence-chip highlighting still works
        assert chip.get_attribute("aria-pressed") == "true"
        page.wait_for_timeout(500)
        chip.click()
        page.wait_for_timeout(400)
        page.click("#gx-walk")
        page.click("#gx-next")
        page.click("#gx-next")
        page.wait_for_timeout(700)
        card.screenshot(path=str(OUT / "ui_graph_story3.png"))
        page.click("#gx-exit")
        page.locator(".gx-toggle input").nth(2).uncheck()  # hide the back-and-forth trades
        page.click("[data-layout=circle]")
        page.wait_for_timeout(700)
        card.screenshot(path=str(OUT / "ui_graph_toggled.png"))
        browser.close()
    for e in errors:
        print("console error:", e[:200])
    print("saved docs/img/ui_graph_*.png")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
