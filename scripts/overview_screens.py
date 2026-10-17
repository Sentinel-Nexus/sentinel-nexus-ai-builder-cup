"""Screenshots of the Overview (1920x1080 and 390x844) and a check that loading it calls no API.

    python scripts/overview_screens.py --base http://localhost:8080

The page may fetch only /, /static/* and the pinned CDN scripts: no /summary, /queue, /cases/*, no
investigate (those are the only routes that can reach BigQuery or Gemini). Exit 1 on a violation
or a console error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
SIZES = [("1920x1080", 1920, 1080), ("390x844", 390, 844)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    bad: list[str] = []
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, w, h in SIZES:
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme="light")
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            seen: list[str] = []
            page.on("request", lambda r, seen=seen: seen.append(r.url))
            page.goto(a.base + "/")
            page.wait_for_selector(".ov-kpis")
            page.wait_for_selector(".ov-chart canvas")
            page.wait_for_timeout(1800)  # count-up and chart animation
            assert (
                page.evaluate("location.hash") == "#/overview"
            ), "empty hash must become #/overview"
            for u in seen:
                pu = urlparse(u)
                local = pu.netloc == urlparse(a.base).netloc
                if local and not (pu.path == "/" or pu.path.startswith("/static/")):
                    bad.append(u)
            path = OUT / f"ui_overview_{name}.png"
            page.screenshot(path=str(path), full_page=(w < 800))
            print("saved", path.name, f"{len(seen)} requests")
            ctx.close()
        browser.close()
    for u in bad:
        print("API request from the Overview:", u)
    for e in errors:
        print("console error:", e[:200])
    return 1 if bad or errors else 0


if __name__ == "__main__":
    sys.exit(main())
