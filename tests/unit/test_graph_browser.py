"""The case graph in headless Chromium against recorded payloads (8 and 25 accounts).

Serves the real static files plus fixtures captured from the service, so no BigQuery is needed.
Needs the pinned CDN scripts (network); skips if they cannot be fetched or Chromium is missing.
"""

from __future__ import annotations

import http.server
import json
import re
import threading
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "services" / "app" / "static"
FIX = Path(__file__).parent / "fixtures" / "graph_cases"
CDN = "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D102
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/":
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__BUILD__", "test")
            return self._send(html.encode(), "text/html")
        if u.path.startswith("/static/"):
            f = STATIC / u.path[len("/static/") :]
            if f.is_file():
                ctype = {".js": "text/javascript", ".css": "text/css", ".json": "application/json"}
                return self._send(f.read_bytes(), ctype.get(f.suffix, "application/octet-stream"))
        m = re.fullmatch(r"/cases/AL_STREAMTEST/(detail|graph|trades)", u.path)
        if m:  # a streamed alert: the wash ring's graph and trades, no agent case
            src = "AL_584807441a"
            if m[1] == "trades":
                acct = parse_qs(u.query).get("account", [""])[0]
                return self._send(
                    (FIX / f"{src}_trades_{acct}.json").read_bytes(), "application/json"
                )
            body = json.loads((FIX / f"{src}_{m[1]}.json").read_text(encoding="utf-8"))
            if m[1] == "detail":
                body.update(cached=False, report=None)
                body["alert"].update(alert_id="AL_STREAMTEST", streamed=True)
            return self._send(json.dumps(body).encode(), "application/json")
        m = re.fullmatch(r"/cases/(\w+)/(detail|graph|timeline)", u.path)
        if m and (FIX / f"{m[1]}_{m[2]}.json").exists():
            return self._send((FIX / f"{m[1]}_{m[2]}.json").read_bytes(), "application/json")
        m = re.fullmatch(r"/cases/(\w+)/trades", u.path)
        if m:
            acct = parse_qs(u.query).get("account", [""])[0]
            f = FIX / f"{m[1]}_trades_{acct}.json"
            if f.exists():
                return self._send(f.read_bytes(), "application/json")
        if u.path == "/stream/config":
            cfg = {"enabled": True, "measured_seconds": 40, "latest_run": None, "active_run": None}
            return self._send(json.dumps(cfg).encode(), "application/json")
        if u.path in ("/summary", "/queue", "/lanes", "/governance"):
            return self._send((FIX / f"{u.path[1:]}.json").read_bytes(), "application/json")
        if u.path == "/benchmark":
            return self._send((STATIC / "data" / "benchmark.json").read_bytes(), "application/json")
        if u.path == "/audit":
            return self._send((FIX / "audit.json").read_bytes(), "application/json")
        self._send(json.dumps({"detail": "not in fixtures"}).encode(), "application/json", 404)


@pytest.fixture(scope="module")
def server():
    try:
        urllib.request.urlopen(CDN, timeout=8)  # noqa: S310
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"CDN not reachable: {e}")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_graph_stays_inside_its_box_and_controls_work(server):
    pytest.importorskip("playwright.sync_api")
    from scripts.graph_overlap_check import run

    try:
        problems = run(server)
    except Exception as e:  # noqa: BLE001
        if "Executable doesn't exist" in str(e):
            pytest.skip("chromium not installed")
        raise
    assert problems == []


def test_command_centre_and_the_other_screens_render_without_errors(server):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"chromium not available: {e}")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(20000)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server + "/")
        page.wait_for_selector(".ov-kpis")  # empty hash -> Overview
        assert page.evaluate("location.hash") == "#/overview"
        page.goto(server + "/#/")
        page.wait_for_selector(".kpis")
        text = page.inner_text("#view")
        assert "Open detector alerts" in text and "Replay a recorded investigation" in text
        assert "real-time" not in text.lower().replace("near-real-time", "")
        assert "live monitoring" not in text.lower()
        # injected cases are off by default and shown apart when asked
        n_real = page.locator("tbody tr").count()
        page.check("#eval-toggle")
        page.wait_for_selector(".eval-section")
        assert page.locator("tbody tr").count() > n_real
        page.uncheck("#eval-toggle")
        assert page.locator("tbody tr").count() == n_real
        # replay of a recorded run is client-side and labelled
        page.click("#replay-btn")
        page.wait_for_selector("section[aria-label='Recorded run replay'] li")
        assert "Recorded run" in page.inner_text("section[aria-label='Recorded run replay']")
        for hash_ in ("#/lanes", "#/benchmark"):
            page.goto(server + "/" + hash_)
            page.wait_for_timeout(800)
            assert "could not be loaded" not in page.inner_text("#view"), hash_
        browser.close()
    assert errors == []


def test_every_screen_and_overlay_is_clean_in_a_browser(server):
    """scripts/browser_check.py: all screens, all popovers, tooltips and dialogs, both sizes."""
    pytest.importorskip("playwright.sync_api")
    from scripts.browser_check import run

    try:
        res = run(server)
    except Exception as e:  # noqa: BLE001
        if "Executable doesn't exist" in str(e):
            pytest.skip("chromium not installed")
        raise
    bad = {k: v for k, v in res.items() if v}
    assert len(res) == 18 and not bad, bad
