"""Capture release screenshots of the LIVE workbench into docs/img (read-only, needs Playwright).

    python scripts/release_screens.py --base https://sentinel-app-578558579917.us-central1.run.app

Opens cached views only: no investigation, no model call, no stream run. The pipeline card is
captured as the server shows it: the state of the most recent stream run, not a run started here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
CASE = "AL_584807441a"
SIZES = {"1920x1080": (1920, 1080), "390x844": (390, 844)}
SHOTS = [
    ("overview", "#/overview", ".ov-stream-line", True),
    ("case_graph", f"#/case/{CASE}", "#graph canvas", False),
    ("lanes", "#/lanes", ".lanes", True),
    ("benchmark", "#/benchmark", "canvas", True),
    ("governance", "#/governance", "table", True),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--timeout-s", type=int, default=60)
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, (w, h) in SIZES.items():
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme="light")
            ctx.add_init_script("try { localStorage.setItem('ms-theme', 'light'); } catch (e) {}")
            page = ctx.new_page()
            page.set_default_timeout(a.timeout_s * 1000)
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            for name, hash_, wait, full in SHOTS:
                page.goto(f"{a.base}/{hash_}")
                page.reload()
                page.wait_for_selector(wait)
                page.wait_for_timeout(1500)  # layout settles
                path = OUT / f"live_{name}_{label}.png"
                page.screenshot(path=str(path), full_page=full)
                print("saved", path.name)
            page.goto(a.base + "/#/")
            page.reload()
            page.wait_for_selector("#stream-card")
            page.wait_for_timeout(1500)
            path = OUT / f"live_pipeline_{label}.png"
            page.locator("#stream-card").screenshot(path=str(path))
            print("saved", path.name)
            ctx.close()
        browser.close()
    for e in errors:
        print("console error:", e[:200])
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
