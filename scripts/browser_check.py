"""Visit every screen in a real browser and audit every overlay. Rerunnable against any URL.

    python scripts/browser_check.py --base https://<service-url>

For each size (1280x800 and 390x844) it visits the Overview (all popovers), the Command centre
(evaluation toggle, replay, shortcuts dialog), the streaming controls and every state of the
Pipeline card (idle, running, finished, rate-limited, error; stubbed, nothing is published), a
case with its graph walk-through, tooltip and
dialogs, Lanes (evaluation toggle, Override dialog), Benchmark, Governance and an unknown case (the
one intentional 404). It fails on a console error, an uncaught exception, a failed request, a 4xx or
5xx response (except that probe) or a missing static file. Every popover, tooltip and dialog it can
open is audited: (a) its box is inside the viewport, (b) nothing covers it (elementFromPoint at its
centre and four corners, inset 4 px, returns it or a descendant), (c) Escape closes it.

It never submits an analyst action and never starts a live investigation. Exit 1 on any problem.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.browser_stream_check import run_stream_checks  # noqa: E402

SIZES = [(1280, 800), (390, 844)]
CASE = "AL_584807441a"
PROBE_404 = "/cases/AL_NOPE/detail"  # the intentional 404 (unknown alert page)

# runs in the page: is the element inside the viewport and uncovered?
AUDIT_JS = (Path(__file__).with_name("browser_audit.js")).read_text(encoding="utf-8")


class Page:
    """A page with every console error, exception and bad response recorded under a screen name."""

    def __init__(self, browser, size, base):
        self.base = base
        self.ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(45000)
        self.screen = "start"
        self.expected: set[int] = set()  # statuses a stubbed response is meant to return
        self.problems: dict[str, list[str]] = defaultdict(list)
        self.screens: list[str] = []
        self.page.on("console", self._console)
        self.page.on("pageerror", lambda e: self.bad(f"uncaught exception: {str(e)[:160]}"))
        self.page.on("requestfailed", lambda r: self.bad(f"request failed: {r.url[:120]}"))
        self.page.on("response", self._response)

    def bad(self, msg: str) -> None:
        self.problems[self.screen].append(msg)

    def _console(self, m) -> None:
        if m.type == "error":
            if self.screen.startswith("Unknown case") and "status of 404" in m.text:
                return  # the browser's own note about the intentional probe
            if "Failed to load resource" in m.text and any(
                f"status of {c}" in m.text for c in self.expected
            ):
                return  # the browser's note about a stubbed 429 or 500
            self.bad(f"console error: {m.text[:160]}")

    def _response(self, r) -> None:
        if r.status < 400:
            return
        if r.url.endswith(PROBE_404) and r.status == 404:
            return  # the intentional probe
        if r.status in self.expected:
            return  # a response this check stubbed on purpose
        what = "missing static file" if "/static/" in r.url else "bad response"
        self.bad(f"{what}: {r.status} {r.url[:120]}")

    def start(self, name: str) -> None:
        self.screen = name
        self.screens.append(name)

    def audit(self, locator, what: str, closes_with_escape: bool = True) -> None:
        for msg in locator.evaluate(AUDIT_JS):
            self.bad(f"{what}: {msg}")
        if closes_with_escape:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(150)
            if locator.count() and locator.first.is_visible():
                self.bad(f"{what}: did not close on Escape")


def overview(pg: Page) -> None:
    p = pg.page
    pg.start("Overview")
    p.goto(pg.base + "/")
    p.wait_for_selector(".ov-kpis")
    p.wait_for_selector(".ov-chart canvas")
    p.wait_for_timeout(1500)
    if p.evaluate("location.hash") != "#/overview":
        pg.bad("an empty hash did not become #/overview")
    triggers = p.locator(".ov-q, .ov-i")
    n = triggers.count()
    if n != 6:
        pg.bad(f"expected 6 popover triggers, found {n}")
    for i in range(n):
        btn = triggers.nth(i)
        btn.scroll_into_view_if_needed()
        btn.click()
        p.wait_for_timeout(120)
        panel = p.locator(".ov-pop:not([hidden])")
        label = btn.get_attribute("aria-label") or f"popover {i}"
        if panel.count() != 1:
            pg.bad(f"{label}: {panel.count()} popovers open (want exactly 1)")
            continue
        if i + 1 < n:  # opening the next one closes this one
            nxt = triggers.nth(i + 1)
            nxt.scroll_into_view_if_needed()
            pg.audit(panel.first, label, closes_with_escape=False)
            nxt.click()
            p.wait_for_timeout(120)
            if p.locator(".ov-pop:not([hidden])").count() != 1:
                pg.bad(f"{label}: opening another popover left more than one open")
            p.keyboard.press("Escape")
            p.wait_for_timeout(150)
            if p.locator(".ov-pop:not([hidden])").count():
                pg.bad(f"{label}: did not close on Escape")
            continue
        pg.audit(panel.first, label)
        if (
            p.evaluate(
                "document.activeElement && document.activeElement.getAttribute('aria-label')"
            )
            != label
        ):
            pg.bad(f"{label}: focus did not return to the trigger")
    secs = p.evaluate("fetch('/static/data/stream.json').then(r => r.json()).then(j => j.seconds)")
    line = p.locator("#ov-stream-line")
    if secs is not None and not (line.count() and f"about {secs:g} seconds" in line.inner_text()):
        pg.bad("the Overview is missing the measured streaming line")
    if secs is None and line.count():
        pg.bad("the Overview shows a streaming delay that was never measured")
    # click outside closes
    triggers.first.click()
    p.mouse.click(5, 5)
    p.wait_for_timeout(150)
    if p.locator(".ov-pop:not([hidden])").count():
        pg.bad("a click outside did not close the popover")


def shortcuts_dialog(pg: Page, via_key: bool) -> None:
    p = pg.page
    if via_key:
        p.locator("body").click(position={"x": 2, "y": 2})
        p.keyboard.press("?")
    else:
        p.click("#help-btn")
    p.wait_for_selector("#dialog[open]")
    pg.audit(p.locator("#dialog"), "shortcuts dialog")


def command(pg: Page) -> None:
    p = pg.page
    pg.start("Command centre")
    p.goto(pg.base + "/#/")
    p.wait_for_selector(".kpis")
    text = p.inner_text("#view")
    for want in ("Open detector alerts", "Replay a recorded investigation"):
        if want not in text:
            pg.bad(f"missing {want!r}")
    box = p.locator("#eval-toggle")
    if box.count():
        box.check()
        p.wait_for_selector(".eval-section")
        box.uncheck()
    p.click("#replay-btn")
    p.wait_for_selector("section[aria-label='Recorded run replay'] li")
    shortcuts_dialog(pg, via_key=False)


def case(pg: Page) -> None:
    p = pg.page
    pg.start("Case and graph")
    p.goto(f"{pg.base}/#/case/{CASE}")
    p.wait_for_selector("#graph canvas")
    p.wait_for_selector(".gx-legend")
    p.wait_for_timeout(2000)
    p.click("#gx-walk")
    for _ in range(3):
        p.click("#gx-next")
    p.keyboard.press("Escape")
    # graph tooltip on the first visible node
    p.locator("#graph").scroll_into_view_if_needed()
    box = p.locator("#graph").bounding_box()
    x, y = 0, 0
    for cx, cy in p.evaluate("document.querySelector('#graph').gxDebug.positions()"):
        # a node whose centre is on the canvas (not under the zoom buttons or the hint)
        on_canvas = p.evaluate(
            "([x, y]) => document.elementFromPoint(x, y)?.tagName === 'CANVAS'",
            [box["x"] + cx, box["y"] + cy],
        )
        if 6 < cx < box["width"] - 6 and 6 < cy < box["height"] - 6 and on_canvas:
            x, y = cx, cy
            break
    tip = p.locator(".gx-tip:not([hidden])")
    for dx in (4, 0, -4, 0):  # a small jiggle: hover fires on a move, and a late refit may hide it
        p.mouse.move(box["x"] + x + dx, box["y"] + y)
        p.wait_for_timeout(200)
        if tip.count():
            break
    if tip.count():
        pg.audit(tip.first, "graph tooltip")
    else:
        pg.bad("graph tooltip did not appear on hover")
    for label, name in (
        ("Approve", "approve dialog"),
        ("Investigate live", "live-run confirm dialog"),
    ):
        p.locator("#view").get_by_role("button", name=label, exact=True).first.click()
        p.wait_for_selector("#dialog[open]")
        pg.audit(p.locator("#dialog"), name)


def lanes(pg: Page) -> None:
    p = pg.page
    pg.start("Lanes")
    p.goto(pg.base + "/#/lanes")
    p.wait_for_selector(".lanes")
    box = p.locator("#eval-toggle")
    if box.count():
        box.check()
    btn = p.get_by_role("button", name="Override", exact=True).first
    if btn.count():
        btn.click()
        p.wait_for_selector("#dialog[open]")
        pg.audit(p.locator("#dialog"), "override dialog")


def simple(pg: Page, name: str, hash_: str, wait: str) -> None:
    pg.start(name)
    pg.page.goto(pg.base + "/" + hash_)
    pg.page.wait_for_selector(wait)
    pg.page.wait_for_timeout(800)
    if "could not be loaded" in pg.page.inner_text("#view"):
        pg.bad("the screen shows an error state")


def run(base: str, sizes=None, real_stream: bool = False) -> dict[str, list[str]]:
    """{'<screen> <size>': [problems]} for every screen visited."""
    from playwright.sync_api import sync_playwright

    out: dict[str, list[str]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size in sizes or SIZES:
            pg = Page(browser, size, base)
            tag = f"{size[0]}x{size[1]}"
            overview(pg)
            command(pg)
            run_stream_checks(pg, real_stream)
            case(pg)
            lanes(pg)
            simple(pg, "Benchmark", "#/benchmark", "canvas")
            simple(pg, "Governance", "#/governance", "table")
            pg.start("Shortcuts dialog")
            pg.page.locator("body").click(position={"x": 2, "y": 2})
            pg.page.keyboard.press("?")
            pg.page.wait_for_selector("#dialog[open]")
            pg.audit(pg.page.locator("#dialog"), "shortcuts dialog (key ?)")
            pg.page.wait_for_timeout(100)
            pg.start("Unknown case (404 probe)")
            pg.page.goto(f"{base}/#/case/AL_NOPE")
            pg.page.wait_for_selector("text=Unknown alert")
            for name in pg.screens:
                out[f"{name} {tag}"] = pg.problems.get(name, [])
            pg.ctx.close()
        browser.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--real-stream", action="store_true", help="also click the real button once")
    a = ap.parse_args()
    res = run(a.base.rstrip("/"), real_stream=a.real_stream)
    failed = 0
    for screen, problems in res.items():
        print(f"{'ok  ' if not problems else 'FAIL'} {screen}")
        for m in problems:
            print(f"       - {m}")
        failed += bool(problems)
    print(f"\n{len(res) - failed}/{len(res)} screens clean")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
