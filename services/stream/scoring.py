"""Score the stream tables with the FROZEN detector. Nothing is copied or tuned here.

- SQL: the detector's own files (sql/views/det_10..det_40), rendered by the detector's own
  ``render_step`` with the stream tables as ``__TRADES__`` / ``__ACCOUNTS__`` and the live
  ``risk_curated.markets``. Their output tables become script-local TEMP tables (a mechanical
  rewrite of ``CREATE OR REPLACE TABLE`` and of the output-dataset prefix, nothing else), so the
  whole run is ONE BigQuery job and no dataset or permanent table is needed.
- Python: ``services.detector.scoring.score_rows`` / ``build_alerts`` with the weights fixed in
  ``scoring.py`` and the frozen threshold and review margin read from ``threshold.json``.

``tests/unit/test_stream_scoring.py`` checks that the rewrite changes nothing but those two things.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from services.detector import scoring
from services.detector.pipeline import VIEWS, Target, render_step
from services.stream import config as C

THRESHOLD_FILE = Path(scoring.__file__).with_name("threshold.json")
STEPS = ("det_10_fills.sql", "det_20_links.sql", "det_30_components.sql", "det_40_features.sql")
SCOPE = "_stream_scope"  # placeholder dataset, removed again below
ALERT_KEY = "stream:burst_01"  # fixed, so the same ring keeps the same alert_id on every replay
LIVE_MARKETS = "risk_curated.markets"


def frozen() -> dict:
    return json.loads(THRESHOLD_FILE.read_text(encoding="utf-8"))


def target(project: str, trades: str | None = None, accounts: str | None = None) -> Target:
    return Target(
        "stream",
        "stream",
        0,
        trades or f"{project}.{C.T_TRADES}",
        accounts or f"{project}.{C.T_ACCOUNTS}",
        f"{project}.{LIVE_MARKETS}",
        SCOPE,
        "",
    )


def temp_table_script(sql: str) -> str:
    """The detector SQL with its output tables as TEMP tables (mechanical rewrite only)."""
    return sql.replace(f"`{SCOPE}.", "`").replace(
        "CREATE OR REPLACE TABLE", "CREATE OR REPLACE TEMP TABLE"
    )


def build_script(project: str, trades: str | None = None, accounts: str | None = None) -> str:
    """One script: det_10..det_40 over the stream tables, then SELECT the features."""
    tgt = target(project, trades, accounts)
    parts = [
        temp_table_script(render_step((VIEWS / name).read_text(encoding="utf-8"), project, tgt))
        for name in STEPS
    ]
    return "\n".join(parts) + "\nSELECT * FROM `det_features`;\n"


def alerts_from_features(feature_rows: list[dict], run_id: str, now: dt.datetime | None = None):
    """-> (alert rows, per-account score dicts). Frozen threshold, frozen weights."""
    cfg = frozen()
    scored = scoring.score_rows(feature_rows, cfg["threshold"], cfg.get("review_margin", 0.0))
    created = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0).isoformat()
    alerts = scoring.build_alerts(scored, ALERT_KEY, created)
    by_account = {
        s["account_id"]: {
            "score": s["score"],
            "flagged": s["flagged"],
            "in_band": s["in_band"],
            "threshold": s["threshold"],
            "ring_id": s["ring_id"],
            "features": {k: round(v, 4) for k, v in s["terms"].items()},
            "contributions": {k: round(v, 4) for k, v in s["contributions"].items()},
        }
        for s in scored
    }
    out = []
    for a in alerts:
        members = {m: by_account[m] for m in a["member_accounts"]}
        out.append(
            {
                **a,
                "run_id": run_id,  # the replay; alert_id stays stable across replays
                "source": "streamed",
                "streamed": True,
                "member_scores": json.dumps(members),
            }
        )
    return out, by_account
