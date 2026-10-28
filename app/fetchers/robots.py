"""robots.txt enforcement.

Every adapter MUST consult this before fetching. The default is to respect
robots.txt strictly; if a URL is disallowed the fetch is refused (the source is
recorded as restricted, per section 4 Tier 5).
"""

from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass

import httpx


class RobotsDisallowed(Exception):
    """Raised when a URL is disallowed for our user agent."""


@dataclass
class RobotsDecision:
    allowed: bool
    crawl_delay: float | None
    reason: str


class RobotsCache:
    """Fetch and cache robots.txt per origin, using stdlib's robotparser."""

    def __init__(self, user_agent: str, timeout: float = 10.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _load(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        if origin in self._parsers:
            return self._parsers[origin]
        robots_url = origin.rstrip("/") + "/robots.txt"
        parser: urllib.robotparser.RobotFileParser | None = None
        try:
            resp = httpx.get(robots_url, timeout=self.timeout, headers={"User-Agent": self.user_agent})
            if resp.status_code == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(resp.text.splitlines())
            elif resp.status_code in (401, 403):
                # Explicitly blocked: treat as fully disallowed.
                parser = _DisallowAll()
            else:
                parser = None  # 404 => no rules => allowed
        except httpx.HTTPError:
            parser = None
        self._parsers[origin] = parser
        return parser

    def check(self, url: str) -> RobotsDecision:
        origin = _origin(url)
        parser = self._load(origin)
        if parser is None:
            return RobotsDecision(True, None, "no robots.txt (allowed)")
        allowed = parser.can_fetch(self.user_agent, url)
        delay = parser.crawl_delay(self.user_agent) if hasattr(parser, "crawl_delay") else None
        reason = "allowed" if allowed else "disallowed by robots.txt"
        return RobotsDecision(allowed, float(delay) if delay else None, reason)

    def ensure(self, url: str) -> None:
        if not self.check(url).allowed:
            raise RobotsDisallowed(f"robots.txt disallows {url}")


class _DisallowAll(urllib.robotparser.RobotFileParser):
    def can_fetch(self, useragent: str, url: str) -> bool:  # noqa: ARG002
        return False


def _origin(url: str) -> str:
    from urllib.parse import urlparse

    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"
