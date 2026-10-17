"""Rate limits for live investigations (each one spends Gemini tokens).

Three limits, all in memory and per service instance: per client per hour, whole service per hour
and whole service per day. Client ids are hashes and are never logged.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class ForceLimiter:
    def __init__(
        self,
        per_hour: int,
        per_day: int,
        clock=time.time,
        *,
        per_client_hour: int = 0,  # 0 = no per-client limit
    ):
        self.per_hour, self.per_day, self._clock = per_hour, per_day, clock
        self.per_client_hour = per_client_hour
        self._hits: deque[float] = deque()
        self._by_client: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client: str | None = None) -> tuple[bool, int, str]:
        """(allowed, retry_after_seconds, scope). Counts the call when allowed.
        ``scope`` is "client", "hour" or "day" for a refusal, "" when allowed."""
        now = self._clock()
        if self.per_hour <= 0 or self.per_day <= 0:  # live runs disabled
            return False, 3600, "disabled"
        with self._lock:
            while self._hits and now - self._hits[0] > 86400:
                self._hits.popleft()
            hour = [t for t in self._hits if now - t <= 3600]
            mine: deque[float] | None = None
            if self.per_client_hour > 0 and client is not None:
                mine = self._by_client[client]
                while mine and now - mine[0] > 3600:
                    mine.popleft()
                if len(mine) >= self.per_client_hour:
                    return False, int(3600 - (now - mine[0])) + 1, "client"
            if len(hour) >= self.per_hour:
                return False, int(3600 - (now - hour[0])) + 1, "hour"
            if len(self._hits) >= self.per_day:
                return False, int(86400 - (now - self._hits[0])) + 1, "day"
            self._hits.append(now)
            if mine is not None:
                mine.append(now)
            if len(self._by_client) > 5000:  # forget idle clients
                for k in [k for k, v in self._by_client.items() if not v or now - v[-1] > 3600]:
                    del self._by_client[k]
            return True, 0, ""

    def allow(self, client: str | None = None) -> tuple[bool, int]:
        ok, retry, _ = self.check(client)
        return ok, retry

    def status(self, client: str | None = None) -> dict:
        """What is left right now (does not count a call)."""
        now = self._clock()
        with self._lock:
            hour = sum(1 for t in self._hits if now - t <= 3600)
            day = sum(1 for t in self._hits if now - t <= 86400)
            mine = 0
            if client is not None and client in self._by_client:
                mine = sum(1 for t in self._by_client[client] if now - t <= 3600)
        return {
            "client_per_hour": self.per_client_hour,
            "client_left": max(0, self.per_client_hour - mine) if self.per_client_hour else None,
            "service_per_hour": self.per_hour,
            "service_hour_left": max(0, self.per_hour - hour),
            "service_per_day": self.per_day,
            "service_day_left": max(0, self.per_day - day),
        }


MESSAGES = {
    "client": "You have used your live investigations for this hour.",
    "hour": "The service-wide limit of live investigations per hour has been reached.",
    "day": "The service-wide daily limit of live investigations has been reached.",
    "disabled": "Live investigations are switched off on this service.",
}
