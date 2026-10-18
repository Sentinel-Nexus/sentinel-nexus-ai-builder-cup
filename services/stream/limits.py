"""Rate limits for the stream endpoints: per client per 10 minutes, per hour and per day.

In memory and per service instance (the service runs one). Client ids are hashes, never logged.
Replay and poison use separate counters with the same numbers, so a visitor can stream a
scenario and then send one bad message.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

MESSAGES = {
    "client": "You have already streamed a burst in the last 10 minutes.",
    "hour": "The service-wide limit of stream runs per hour has been reached.",
    "day": "The service-wide daily limit of stream runs has been reached.",
    "disabled": "Streaming is switched off on this service.",
}


class StreamLimiter:
    def __init__(self, per_client_10min: int, per_hour: int, per_day: int, clock=time.time):
        self.per_client, self.per_hour, self.per_day, self._clock = (
            per_client_10min,
            per_hour,
            per_day,
            clock,
        )
        self._hits: deque[float] = deque()
        self._by_client: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client: str) -> tuple[bool, int, str]:
        """(allowed, retry_after_seconds, scope); counts the call when allowed."""
        now = self._clock()
        if min(self.per_client, self.per_hour, self.per_day) <= 0:
            return False, 3600, "disabled"
        with self._lock:
            while self._hits and now - self._hits[0] > 86400:
                self._hits.popleft()
            mine = self._by_client[client]
            while mine and now - mine[0] > 600:
                mine.popleft()
            if len(mine) >= self.per_client:
                return False, int(600 - (now - mine[0])) + 1, "client"
            hour = [t for t in self._hits if now - t <= 3600]
            if len(hour) >= self.per_hour:
                return False, int(3600 - (now - hour[0])) + 1, "hour"
            if len(self._hits) >= self.per_day:
                return False, int(86400 - (now - self._hits[0])) + 1, "day"
            self._hits.append(now)
            mine.append(now)
            if len(self._by_client) > 5000:
                for k in [k for k, v in self._by_client.items() if not v or now - v[-1] > 600]:
                    del self._by_client[k]
            return True, 0, ""

    def status(self) -> dict:
        now = self._clock()
        with self._lock:
            return {
                "per_client_10min": self.per_client,
                "per_hour": self.per_hour,
                "per_day": self.per_day,
                "hour_left": max(0, self.per_hour - sum(1 for t in self._hits if now - t <= 3600)),
                "day_left": max(0, self.per_day - sum(1 for t in self._hits if now - t <= 86400)),
            }
