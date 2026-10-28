"""Per-source rate limiting and backoff (§27).

Rate limits are treated as *rules*, not obstacles:

* one request at a time per source (concurrency comes from the registry);
* a minimum interval derived from ``requests_per_minute`` and ``delay_s``;
* exponential backoff on transient failures;
* an explicit pause when the source answers ``429``/``503`` - we wait for
  ``Retry-After`` and never rotate identities or proxies to get around it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Statuses that mean "you are going too fast" rather than "this is broken".
THROTTLE_STATUSES = (429, 503)


class RateLimitPause(Exception):
    """Raised when the source has told us to slow down more than we will wait."""

    def __init__(self, source: str, retry_after_s: float, reason: str = "rate limited") -> None:
        super().__init__(f"{source}: {reason} (retry after {retry_after_s:.0f}s)")
        self.source = source
        self.retry_after_s = retry_after_s
        self.reason = reason


@dataclass
class RateLimiter:
    """Single-threaded, per-source limiter with backoff state."""

    source: str
    requests_per_minute: int = 12
    delay_s: float = 5.0
    max_retries: int = 3
    base_backoff_s: float = 2.0
    max_backoff_s: float = 300.0
    #: Injectable for tests: (seconds) -> None and () -> monotonic seconds.
    sleeper: object = time.sleep
    clock: object = time.monotonic
    _last_request_at: float | None = field(default=None, init=False, repr=False)
    _paused_until: float = field(default=0.0, init=False, repr=False)
    consecutive_failures: int = field(default=0, init=False)
    requests_made: int = field(default=0, init=False)

    @property
    def min_interval_s(self) -> float:
        by_rate = 60.0 / self.requests_per_minute if self.requests_per_minute > 0 else 0.0
        return max(by_rate, self.delay_s)

    def wait(self) -> float:
        """Block until the next request is allowed. Returns the seconds slept."""
        now = self.clock()
        target = max(
            self._paused_until,
            (self._last_request_at or 0.0) + self.min_interval_s,
        )
        slept = max(0.0, target - now)
        if slept > 0:
            self.sleeper(slept)
        self._last_request_at = self.clock()
        self.requests_made += 1
        return slept

    def note_response(self, status_code: int | None, retry_after_s: float | None = None) -> None:
        """Record the outcome of a request so backoff/pause state stays current."""
        if status_code in THROTTLE_STATUSES:
            pause = retry_after_s if retry_after_s is not None else self.backoff(self.consecutive_failures + 1)
            self._paused_until = self.clock() + pause
            self.consecutive_failures += 1
            return
        if status_code is not None and status_code < 400:
            self.consecutive_failures = 0

    def backoff(self, attempt: int) -> float:
        """Exponential backoff, capped."""
        return min(self.max_backoff_s, self.base_backoff_s * (2 ** max(0, attempt - 1)))

    @property
    def paused_s(self) -> float:
        return max(0.0, self._paused_until - self.clock())

    def guard(self, retry_after_s: float | None) -> None:
        """Raise when the requested pause exceeds what we are willing to wait."""
        if retry_after_s is not None and retry_after_s > self.max_backoff_s:
            raise RateLimitPause(self.source, retry_after_s)


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` may be seconds or an HTTP date; only seconds are used."""
    if not value:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None
