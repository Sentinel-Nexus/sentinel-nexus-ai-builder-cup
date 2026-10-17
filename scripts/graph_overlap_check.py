"""Browser checks for the case graph: no overlap with the legend, wheel behaviour, controls.

    python scripts/graph_overlap_check.py --base http://localhost:8080 [--shots DIR]
        [--api-base https://other-service]     # answer API paths from another service

For the 8-account and the 25-account case, at 1280x800 and 390x844, it hovers every node and every
line, uses the mouse wheel over the graph and checks that

- the tooltip's box never leaves #graph, and nothing but the legend paints over a legend item;
- a plain wheel scrolls the page and does not zoom the graph; Ctrl + wheel zooms;
- after zooming, Fit and a window resize, every node sits inside #graph;
- evidence chips, edge toggles, layouts, "Walk me through it", the side panel and the list view
  behave as before.

Exit code 1 on any problem. `run()` is also called by tests/unit/test_graph_browser.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CASES = {"AL_584807441a": 8, "AL_fa9727ee1e": 25}
SIZES = [(1280, 800), (390, 844)]
PROBE = (Path(__file__).with_name("graph_probe.js")).read_text(encoding="utf-8")
P = "window.__probe"
GX = f"{P}.gx()"


def proxy(page, base: str, api_base: str) -> None:
    """Forward API calls (not / or /static) to another service."""
    import urllib.request

    def handler(route):
        path = route.request.url[len(base) :]
        if path in ("", "/") or path.startswith("/static/"):
            return route.continue_()
        try:
            with urllib.request.urlopen(api_base + path, timeout=60) as r:  # noqa: S310
                return route.fulfill(
                    status=r.status, body=r.read(), headers={"content-type": "application/json"}
                )
        except Exception:  # noqa: BLE001
            return route.abort()

    page.route(f"{base}/**", handler)


def inside(page, box: dict, pad: float = 0) -> bool:
    return all(
        pad <= x <= box["width"] - pad and pad <= y <= box["height"] - pad
        for x, y in page.evaluate(f"{GX}.positions()")
    )


def sweep(page, tag: str, box: dict, points: list, problems: list[str]) -> None:
    """Hover each point of #graph: the tooltip stays inside it and the legend stays clean."""
    for x, y in points or []:
        if not (0 <= x <= box["width"] and 0 <= y <= box["height"]):
            continue
        page.mouse.move(box["x"] + x, box["y"] + y)
        page.wait_for_timeout(50)
        r = page.evaluate(f"{P}.rects()")
        g, t = r["graph"], r["tip"]
        out = t and (
            t["x"] < g["x"] - 0.5
            or t["y"] < g["y"] - 0.5
            or t["r"] > g["r"] + 0.5
            or t["b"] > g["b"] + 0.5
        )
        if out:
            problems.append(f"{tag}: tooltip leaves #graph at ({x:.0f},{y:.0f})")
        problems += [f"{tag}: {m}" for m in page.evaluate(f"{P}.legendCovered()")]


def graph_box(page) -> dict:
    page.locator("#graph").scroll_into_view_if_needed()
    return page.locator("#graph").bounding_box()


def wheel_checks(page, tag: str, problems: list[str]) -> None:
    box = graph_box(page)
    z0 = page.evaluate(f"{GX}.zoom()")
    y0 = page.evaluate("scrollY")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, 300)
    page.wait_for_timeout(400)
    if page.evaluate(f"{GX}.zoom()") != z0:
        problems.append(f"{tag}: plain wheel zoomed the graph")
    if page.evaluate("scrollY") == y0:
        problems.append(f"{tag}: plain wheel did not scroll the page")
    box = graph_box(page)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -300)
    page.keyboard.up("Control")
    page.wait_for_timeout(400)
    if page.evaluate(f"{GX}.zoom()") == z0:
        problems.append(f"{tag}: Ctrl + wheel did not zoom")
    box = graph_box(page)
    sweep(page, f"{tag} zoomed", box, page.evaluate(f"{GX}.positions()"), problems)
    sweep(page, f"{tag} zoomed lines", box, page.evaluate(f"{GX}.edgePoints()"), problems)
    page.click("#fit-btn")
    page.wait_for_timeout(400)
    if not inside(page, box, 10):
        problems.append(f"{tag}: a node is outside #graph after Fit")
    if page.evaluate(f"{P}.legendCovered()"):
        problems.append(f"{tag}: legend covered after the wheel test")


def behaviour(page, tag: str, n_accounts: int, problems: list[str]) -> None:
    """Things that worked before must still work."""
    box = graph_box(page)
    chip = page.locator("button.ev-chip[title^='UsesDevice:']").first
    chip.click()
    page.wait_for_timeout(500)
    if chip.get_attribute("aria-pressed") != "true" or page.evaluate(f"{GX}.focused()") == 0:
        problems.append(f"{tag}: evidence chip did not highlight the graph")
    chip.click()
    page.wait_for_timeout(500)
    if page.evaluate(f"{GX}.focused()") != 0:
        problems.append(f"{tag}: evidence chip did not clear")
    e0 = page.evaluate(f"{GX}.visibleEdges()")
    first = page.locator(".gx-toggle input").first
    first.uncheck()
    page.wait_for_timeout(200)
    if page.evaluate(f"{GX}.visibleEdges()") >= e0:
        problems.append(f"{tag}: edge toggle hid nothing")
    first.check()
    page.wait_for_timeout(200)
    if page.evaluate(f"{GX}.visibleEdges()") != e0:
        problems.append(f"{tag}: edge toggle did not restore")
    before = page.evaluate(f"{GX}.modelPositions()")
    page.click("[data-layout=circle]")
    page.wait_for_timeout(400)
    if page.evaluate(f"{GX}.modelPositions()") == before:
        problems.append(f"{tag}: Circle did not change the layout")
    page.click("[data-layout=clusters]")
    page.wait_for_timeout(400)
    after = page.evaluate(f"{GX}.modelPositions()")
    if any(
        abs(a[0] - b[0]) > 1 or abs(a[1] - b[1]) > 1 for a, b in zip(before, after, strict=True)
    ):
        problems.append(f"{tag}: Clusters layout is not deterministic")
    page.click("#gx-walk")
    for _ in range(3):
        page.click("#gx-next")
    if "Step 4 of 4" not in page.locator(".gx-step-no").inner_text():
        problems.append(f"{tag}: walk-through did not reach step 4")
    page.keyboard.press("Escape")
    if page.locator(".gx-story").is_visible():
        problems.append(f"{tag}: Esc did not leave the walk-through")
    box = graph_box(page)
    x, y = page.evaluate(f"{GX}.positions()")[0]
    page.mouse.click(box["x"] + x, box["y"] + y)
    page.wait_for_timeout(700)
    panel = page.locator("#node-panel").inner_text()
    if "Account" not in panel and "device" not in panel.lower() and "funding" not in panel.lower():
        problems.append(f"{tag}: side panel stayed empty after a click")
    summary = page.locator("details.gx-list summary").inner_text()
    rows = page.locator("details.gx-list tbody tr").count()
    if f"({n_accounts} accounts)" not in summary or rows != n_accounts:
        problems.append(f"{tag}: list view has {rows} rows for {n_accounts} accounts")
    z = page.evaluate(f"{GX}.zoom()")
    page.click("#zoom-in-btn")
    page.wait_for_timeout(300)
    if page.evaluate(f"{GX}.zoom()") <= z:
        problems.append(f"{tag}: + did not zoom in")
    page.click("#zoom-out-btn")
    page.click("#fit-btn")
    page.wait_for_timeout(300)


def run(base: str, shots: str = "", api_base: str = "") -> list[str]:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for case, n in CASES.items():
            for w, h in SIZES:
                page = browser.new_page(viewport={"width": w, "height": h})
                page.set_default_timeout(30000)
                if api_base:
                    proxy(page, base, api_base)
                page.goto(f"{base}/#/case/{case}")
                page.wait_for_selector("#graph canvas")
                page.wait_for_selector(".gx-legend")
                page.wait_for_timeout(2500)
                page.evaluate(f"void ({P} = {PROBE})")
                tag = f"{case} {w}x{h}"
                box = graph_box(page)
                sweep(page, tag, box, page.evaluate(f"{GX}.positions()"), problems)
                sweep(page, f"{tag} lines", box, page.evaluate(f"{GX}.edgePoints()"), problems)
                wheel_checks(page, tag, problems)
                if (w, h) == SIZES[0]:
                    behaviour(page, tag, n, problems)
                    # a resize keeps every node in view
                    page.set_viewport_size({"width": 1000, "height": 700})
                    page.wait_for_timeout(700)
                    if not inside(page, graph_box(page), 5):
                        problems.append(f"{tag}: a node is outside #graph after a resize")
                if shots:
                    page.locator("section[aria-label='Ring graph']").screenshot(
                        path=str(Path(shots) / f"{case}_{w}x{h}.png")
                    )
                page.close()
        browser.close()
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--shots", default="")
    ap.add_argument("--api-base", default="")
    a = ap.parse_args()
    problems = run(a.base.rstrip("/"), a.shots, a.api_base.rstrip("/"))
    for m in problems:
        print("PROBLEM:", m)
    print(f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
