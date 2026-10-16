"""Rate limit for force=true re-investigations (each one spends Gemini tokens)."""

from __future__ import annotations

import threading
import time
from collections import deque


class ForceLimiter:
    def __init__(self, per_hour: int, per_day: int, clock=time.time):
        self.per_hour, self.per_day, self._clock = per_hour, per_day, clock
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). Counts the call when allowed."""
        now = self._clock()
        if self.per_hour <= 0 or self.per_day <= 0:  # force disabled
            return False, 3600
        with self._lock:
            while self._hits and now - self._hits[0] > 86400:
                self._hits.popleft()
            hour = [t for t in self._hits if now - t <= 3600]
            if len(hour) >= self.per_hour:
                return False, int(3600 - (now - hour[0])) + 1
            if len(self._hits) >= self.per_day:
                return False, int(86400 - (now - self._hits[0])) + 1
            self._hits.append(now)
            return True, 0
