"""Screenshots of the Overview with the glossary open and with the Evidence popover open.

    python scripts/overview_popover_shots.py --base http://localhost:8080

Writes docs/img/ui_overview_glossary.png and ui_overview_evidence.png (1280x800, viewport shots).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.set_default_timeout(30000)
        page.goto(a.base + "/")
        page.wait_for_selector(".ov-kpis")
        page.wait_for_selector(".ov-chart canvas")
        page.wait_for_timeout(1500)
        page.locator(".ov-q").click()
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "ui_overview_glossary.png"))
        page.keyboard.press("Escape")
        page.locator(".ov-i").last.click()  # Evidence is the last KPI card
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "ui_overview_evidence.png"))
        browser.close()
    print("saved docs/img/ui_overview_glossary.png, ui_overview_evidence.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
