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
        m = re.fullmatch(r"/cases/(\w+)/(detail|graph|timeline)", u.path)
        if m and (FIX / f"{m[1]}_{m[2]}.json").exists():
            return self._send((FIX / f"{m[1]}_{m[2]}.json").read_bytes(), "application/json")
        m = re.fullmatch(r"/cases/(\w+)/trades", u.path)
        if m:
            acct = parse_qs(u.query).get("account", [""])[0]
            f = FIX / f"{m[1]}_trades_{acct}.json"
            if f.exists():
                return self._send(f.read_bytes(), "application/json")
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
