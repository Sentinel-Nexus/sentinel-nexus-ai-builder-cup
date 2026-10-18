"""Stream settings. Every limit is read from the environment so the deployed values are explicit."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

PROJECT_DEFAULT = "project-f8544066-3df8-4f99-8ed"
TOPIC = "trades"
SUBSCRIPTION = "trades-sub"
MAX_DELIVERY_ATTEMPTS = 5  # the dead-letter policy on trades-sub
BATCH_MAX_MESSAGES = 200
BATCH_WINDOW_S = 3.0
MAX_SCORING_QUERIES = 6  # per replay
MAX_BYTES_BILLED = 100 * 1024 * 1024  # every job except the scoring script
# The frozen detector script is one job of about 20 statements; BigQuery bills at least 10 MB per
# table per statement, so it bills about 350 MB although it processes under 5 MB (measured on the
# live tables, 2026-10-18). The cap on that one job is 500 MB, the cap the detector pipeline already
# uses per statement. See docs/STATUS.md.
SCORE_MAX_BYTES_BILLED = 500 * 1024 * 1024
JOB_TIMEOUT_S = 120.0

# the four tables sa-agent may write (dataset.table); nothing else is writable by this path
T_ACCOUNTS = "risk_raw.stream_accounts"
T_TRADES = "risk_raw.stream_trades"
T_ALERTS = "risk_curated.stream_alerts"
T_EVENTS = "audit.stream_events"
WRITABLE_TABLES = (T_ACCOUNTS, T_TRADES, T_ALERTS, T_EVENTS)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def flag_enabled() -> bool:
    return os.environ.get("STREAMING_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class StreamSettings:
    project: str = field(default_factory=lambda: os.environ.get("PROJECT_ID", PROJECT_DEFAULT))
    enabled: bool = field(default_factory=flag_enabled)
    # 1 per client per 10 minutes, 6 per hour and 30 per day for the service (per instance)
    per_client_10min: int = field(default_factory=lambda: _int("STREAM_PER_CLIENT_10MIN", 1))
    per_hour: int = field(default_factory=lambda: _int("STREAM_PER_HOUR", 6))
    per_day: int = field(default_factory=lambda: _int("STREAM_PER_DAY", 30))
    # a run that has not finished after this long is reported as stalled
    run_deadline_s: float = field(default_factory=lambda: float(_int("STREAM_RUN_DEADLINE_S", 180)))
