"""Base source adapter and fetch policy (sections 4, 45-47).

Adapters are thin: they know how to fetch a URL politely and how to parse a
payload into raw records. All policy (robots, tier enforcement, rate limiting,
health accounting) lives here so it cannot be forgotten in an individual
adapter.
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.enums import SourceStatus
from app.core.lineage import make_lineage
from app.fetchers.registry import SourceConfig
from app.fetchers.robots import RobotsCache, RobotsDisallowed


class SourceRestricted(Exception):
    """Raised when policy forbids automated acquisition from a source."""


class SourceDegraded(Exception):
    """Raised when a parser fails, so the source can be marked DEGRADED."""


@dataclass
class FetchResult:
    source: str
    records: list[dict] = field(default_factory=list)
    raw_object: bytes | None = None
    content_hash: str = ""
    source_url: str = ""
    status: SourceStatus = SourceStatus.OK
    error: str | None = None


@dataclass
class SourceHealth:
    """Per-adapter health counters (section 46)."""

    success_rate: float = 0.0
    latency_ms: float = 0.0
    items_found: int = 0
    items_changed: int = 0
    items_removed: int = 0
    parser_errors: int = 0
    http_errors: int = 0
    blocked_requests: int = 0
    schema_changes: int = 0
    last_success: str | None = None
    requests: int = 0
    successes: int = 0

    def record_success(self, latency_ms: float, items: int) -> None:
        self.requests += 1
        self.successes += 1
        self.latency_ms = latency_ms
        self.items_found = items
        self.success_rate = self.successes / self.requests
        self.last_success = datetime.now(UTC).replace(microsecond=0).isoformat()

    def record_http_error(self, latency_ms: float) -> None:
        self.requests += 1
        self.http_errors += 1
        self.latency_ms = latency_ms
        self.success_rate = self.successes / self.requests

    def record_parser_error(self) -> None:
        self.parser_errors += 1

    def record_blocked(self) -> None:
        self.blocked_requests += 1

    def to_dict(self) -> dict:
        return {
            "success_rate": round(self.success_rate, 3),
            "latency_ms": round(self.latency_ms, 1),
            "items_found": self.items_found,
            "items_changed": self.items_changed,
            "items_removed": self.items_removed,
            "parser_errors": self.parser_errors,
            "http_errors": self.http_errors,
            "blocked_requests": self.blocked_requests,
            "schema_changes": self.schema_changes,
            "last_success": self.last_success,
        }


def content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class SourceAdapter(ABC):
    """Abstract base for all adapters.

    Subclasses implement :meth:`fetch_urls` and :meth:`parse`. The base class
    enforces policy in :meth:`run`.
    """

    #: registry name, must match config/source_registry.yaml
    name: str = "unnamed"
    #: default base URL (used for robots checks)
    base_url: str = ""

    def __init__(self, config: SourceConfig | None = None, client: httpx.Client | None = None) -> None:
        self.settings = get_settings()
        self.config = config
        self.client = client or httpx.Client(
            timeout=self.settings.request_timeout_s,
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
        )
        self.health = SourceHealth()
        self.status = SourceStatus.UNKNOWN
        self._robots = RobotsCache(self.settings.user_agent, timeout=self.settings.request_timeout_s)
        self._last_request_ts = 0.0
        self._min_delay = self.settings.default_min_delay_s
        if config and config.extra.get("min_delay_s"):
            self._min_delay = float(config.extra["min_delay_s"])

    # --- policy -------------------------------------------------------------
    def assert_allowed(self) -> None:
        """Refuse to run if the registry marks the source as restricted."""
        if self.config is not None and not self.config.auto_acquisition_allowed:
            self.status = (
                SourceStatus.LICENSE_REQUIRED
                if self.config.status.lower() == "license_required"
                else SourceStatus.RESTRICTED
            )
            raise SourceRestricted(
                f"{self.name} cannot be acquired automatically "
                f"(status={self.config.status}, mode={self.config.recommended_mode}). "
                "Use the licensed API/feed or manual import."
            )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_delay:
            time.sleep(self._min_delay - elapsed)
        self._last_request_ts = time.monotonic()

    def get(self, url: str) -> httpx.Response:
        """Polite GET that respects robots.txt and the per-domain delay."""
        if self.settings.respect_robots:
            decision = self._robots.check(url)
            if not decision.allowed:
                self.health.record_blocked()
                raise RobotsDisallowed(f"robots.txt disallows {url}")
            if decision.crawl_delay:
                self._min_delay = max(self._min_delay, decision.crawl_delay)
        self._throttle()
        return self.client.get(url)

    # --- to implement -------------------------------------------------------
    @abstractmethod
    def fetch_urls(self) -> list[str]:
        """Return the list of URLs to fetch (search/list pages)."""

    @abstractmethod
    def parse(self, url: str, payload: bytes) -> list[dict]:
        """Parse a raw payload into raw record dicts.

        Implementations should prefer JSON-LD / schema.org / embedded JSON over
        brittle XPath (section 47). Raise SourceDegraded on structural change.
        """

    # --- orchestration ------------------------------------------------------
    def run(self) -> FetchResult:
        self.assert_allowed()
        collected: list[dict] = []
        last_url = ""
        last_hash = ""
        raw_all = bytearray()
        for url in self.fetch_urls():
            t0 = time.monotonic()
            try:
                resp = self.get(url)
            except RobotsDisallowed:
                raise
            except httpx.HTTPError as exc:
                self.health.record_http_error((time.monotonic() - t0) * 1000)
                self.status = SourceStatus.WARNING
                return FetchResult(
                    source=self.name,
                    status=self.status,
                    error=f"http error on {url}: {exc}",
                    source_url=url,
                )
            latency = (time.monotonic() - t0) * 1000
            if resp.status_code >= 400:
                self.health.record_http_error(latency)
                self.status = SourceStatus.WARNING
                return FetchResult(
                    source=self.name,
                    status=self.status,
                    error=f"HTTP {resp.status_code} on {url}",
                    source_url=url,
                )
            payload = resp.content
            raw_all.extend(payload)
            try:
                records = self.parse(url, payload)
            except SourceDegraded as exc:
                self.health.record_parser_error()
                self.status = SourceStatus.DEGRADED
                return FetchResult(
                    source=self.name,
                    status=self.status,
                    error=f"parser degraded: {exc}",
                    source_url=url,
                )
            for rec in records:
                rec.setdefault("source", self.name)
                rec["lineage"] = make_lineage(
                    self.name, source_url=url, content_hash=content_hash(payload)
                ).to_dict()
            collected.extend(records)
            last_url, last_hash = url, content_hash(payload)
            self.health.record_success(latency, len(records))

        self.status = SourceStatus.OK
        raw = bytes(raw_all) if raw_all else None
        return FetchResult(
            source=self.name,
            records=collected,
            raw_object=raw,
            content_hash=last_hash,
            source_url=last_url,
            status=self.status,
        )

