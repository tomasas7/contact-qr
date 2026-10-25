"""Per-IP sliding-window rate limiting for write operations.

State is in process memory, which is correct for this deployment (a single app
container). Scaling out to several replicas would need a shared store such as Redis;
see NOTES.md.
"""

import math
import threading
import time
from collections import deque

from fastapi import Request


class RateLimitExceededError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Rate limit exceeded; retry after {retry_after}s")
        self.retry_after = retry_after


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        """Record one request for `key`, raising RateLimitExceededError if over the limit."""
        if self.limit <= 0:
            return
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                raise RateLimitExceededError(max(1, math.ceil(hits[0] + self.window - now)))
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory: forget idle clients
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                    del self._hits[k]


def limit_writes(request: Request) -> None:
    """FastAPI dependency applied to every create/update/delete route (UI and API)."""
    client_ip = request.client.host if request.client else "unknown"
    request.app.state.rate_limiter.hit(client_ip)
