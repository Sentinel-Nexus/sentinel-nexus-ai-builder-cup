"""Two visits in one browser context: no stale JS, current screens, build id shown.

    python scripts/cache_check.py --base https://<service-url> [--build abc1234]

Visit 1 loads the app; visit 2 (same context, so the HTTP cache is warm) must revalidate /static
files (Cache-Control: no-cache) and still show the current screens: the Overview route opens, the
Command centre has "Replay a recorded investigation" and "Open detector alerts", and the
footer shows the build id. Exit 1 on any failure.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--build", default="", help="expected build id (default: any non-empty)")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    from playwright.sync_api import sync_playwright

    fails: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        for visit in (1, 2):
            page = ctx.new_page()
            page.set_default_timeout(60000)
            seen: dict[str, dict] = {}
            page.on(
                "response",
                lambda r, seen=seen: seen.update(
                    {r.url: {"status": r.status, "cc": r.headers.get("cache-control", "")}}
                ),
            )
            page.goto(base + "/")
            page.wait_for_selector(".ov-kpis")
            if page.evaluate("location.hash") != "#/overview":
                fails.append(f"visit {visit}: empty hash did not become #/overview")
            if "That page does not exist" in page.inner_text("#view"):
                fails.append(f"visit {visit}: Overview showed 'page does not exist'")
            build = page.inner_text("#build-id").strip()
            if not build or build == "__BUILD__" or (a.build and build != a.build):
                fails.append(f"visit {visit}: build id is {build!r}")
            for url, r in seen.items():
                if "/static/" in url and r["cc"] != "no-cache":
                    fails.append(f"visit {visit}: {url} Cache-Control {r['cc']!r}")
                if url == base + "/" and r["cc"] != "no-store":
                    fails.append(f"visit {visit}: / Cache-Control {r['cc']!r}")
            if visit == 2:
                static = [r for u, r in seen.items() if "/static/" in u]
                n304 = sum(r["status"] == 304 for r in static)
                print(f"visit 2: {n304}/{len(static)} static files revalidated (304)")
            page.goto(base + "/#/")
            page.wait_for_selector("#replay-btn")
            page.wait_for_selector(".kpis")
            text = page.inner_text("#view")
            for want in ("Replay a recorded investigation", "Open detector alerts"):
                if want not in text:
                    fails.append(f"visit {visit}: Command centre lacks {want!r}")
            if "streaming ships" in text:
                fails.append(f"visit {visit}: old disabled Replay text is still shown")
            print(
                f"visit {visit}: build {build}, ok"
                if not fails
                else f"visit {visit}: build {build}"
            )
            page.close()
        browser.close()
    for f in fails:
        print("FAIL:", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
