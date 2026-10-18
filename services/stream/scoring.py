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
import re
from pathlib import Path

from services.detector import scoring
from services.detector.pipeline import VIEWS, Target, render_step
from services.stream import config as C

THRESHOLD_FILE = Path(scoring.__file__).with_name("threshold.json")
STEPS = ("det_10_fills.sql", "det_20_links.sql", "det_30_components.sql", "det_40_features.sql")
SCOPE = "_stream_scope"  # placeholder dataset, removed again below
# per burst the key is "<ALERT_KEY>:<suffix>": a replay of the same burst keeps its alert_id,
# a fresh burst gets a new one
ALERT_KEY = "stream:burst_01"
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


SUFFIX = re.compile(r"^[0-9a-f]{8}$")


def in_scope(account_id: str, suffix: str | None) -> bool:
    """Whether an account id belongs to the run ``suffix`` (None: the legacy un-namespaced rows)."""
    return suffix is None or account_id.endswith(f"-{suffix}")


def scoped(accounts: list[dict], trades: list[dict], suffix: str | None):
    """The rows of one run (so a graph never links two runs); all rows when ``suffix`` is None."""
    return (
        [a for a in accounts if in_scope(a["account_id"], suffix)],
        [t for t in trades if in_scope(t["account_id"], suffix)],
    )


def suffix_of(account_id: str) -> str | None:
    """The burst suffix an id carries, or None for the legacy un-namespaced ids."""
    tail = account_id.rsplit("-", 1)[-1] if "-" in account_id else ""
    return tail if SUFFIX.match(tail) else None


def scope_sql(project: str, suffix: str) -> str:
    """Two TEMP tables holding only one run's accounts and trades (ids end in ``-<suffix>``)."""
    if not SUFFIX.match(suffix):
        raise ValueError("bad run suffix")
    return (
        f"CREATE TEMP TABLE run_accounts AS SELECT * FROM `{project}.{C.T_ACCOUNTS}` "
        f"WHERE ENDS_WITH(account_id, '-{suffix}');\n"
        f"CREATE TEMP TABLE run_trades AS SELECT * FROM `{project}.{C.T_TRADES}` "
        f"WHERE ENDS_WITH(account_id, '-{suffix}');\n"
    )


def build_script(project: str, suffix: str | None = None) -> str:
    """One script: det_10..det_40 over the stream tables (or, with ``suffix``, over one run's
    accounts and trades only), then SELECT the features."""
    prefix = ""
    if suffix is None:
        tgt = target(project)
    else:
        prefix = scope_sql(project, suffix)
        tgt = target(project, "run_trades", "run_accounts")
    parts = [
        temp_table_script(render_step((VIEWS / name).read_text(encoding="utf-8"), project, tgt))
        for name in STEPS
    ]
    return prefix + "\n".join(parts) + "\nSELECT * FROM `det_features`;\n"


def alerts_from_features(
    feature_rows: list[dict], run_id: str, now: dt.datetime | None = None, suffix: str | None = None
):
    """-> (alert rows, per-account score dicts). Frozen threshold, frozen weights.

    The alert id is keyed on the burst (``suffix``), so every fresh burst raises a new alert and a
    replay of the same burst re-raises the same one."""
    cfg = frozen()
    scored = scoring.score_rows(feature_rows, cfg["threshold"], cfg.get("review_margin", 0.0))
    created = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0).isoformat()
    alerts = scoring.build_alerts(
        scored, ALERT_KEY if suffix is None else f"{ALERT_KEY}:{suffix}", created
    )
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
