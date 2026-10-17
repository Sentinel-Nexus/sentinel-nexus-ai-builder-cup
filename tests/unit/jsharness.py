"""Run the workbench's ES modules in headless Chromium (no Node here). Tests skip if unavailable."""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "services" / "app" / "static"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D102
        pass


@pytest.fixture(scope="session")
def js_page():
    """A blank page on a local origin that serves /static; call page.evaluate with imports."""
    sync_api = pytest.importorskip("playwright.sync_api")
    handler = functools.partial(_Quiet, directory=str(STATIC.parent))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    pw = sync_api.sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception as e:  # noqa: BLE001
        pw.stop()
        srv.shutdown()
        pytest.skip(f"chromium not available: {e}")
    page = browser.new_page()
    page.goto(f"{base}/static/data/benchmark.json")  # same origin, no app shell
    page.base = base
    yield page
    browser.close()
    pw.stop()
    srv.shutdown()
