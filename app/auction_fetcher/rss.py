"""RSS/Atom auction feeds (§33).

RSS is the cheapest authorized channel: no HTML parsing, no rendering, and the
feed itself is published for consumption. The fetcher still refuses to run
without a policy decision, so an auction source marked UNKNOWN or RESTRICTED in
the registry is never touched automatically.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from app.agent_import.policy import AccessDecision, PolicyResolver
from app.auction_fetcher.models import AuctionFeedItem
from app.core.config import Settings, get_settings
from app.fetchers.audit import AccessEvent, AccessLog, AccessOutcome, scrub_url
from app.fetchers.registry import SourceConfig
from app.storage.datalake import DataLake


def _local(tag: str) -> str:
    return tag.split("}", 1)[1].lower() if "}" in tag else tag.lower()


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join((element.text or "").split())


@dataclass
class ParsedFeed:
    title: str
    link: str
    items: list[dict]

    def to_dict(self) -> dict:
        return {"title": self.title, "link": self.link, "items": self.items}


def parse_feed(xml_text: str) -> ParsedFeed:
    """Parse RSS 2.0 or Atom into a flat item mapping (namespace-insensitive)."""
    root = ET.fromstring(xml_text)
    is_rss = _local(root.tag) == "rss"
    channel = root.find("channel") if is_rss else root
    title = _text(channel.find("title") if channel is not None else None)
    link = _text(channel.find("link") if channel is not None else None)

    items: list[dict] = []
    for node in root.iter():
        if _local(node.tag) not in {"item", "entry"}:
            continue
        record: dict = {}
        for child in node:
            key = _local(child.tag)
            if key in {"link", "guid", "id"}:
                value = child.get("href") or _text(child) or (child.get("url") or "")
            elif key == "category":
                value = child.get("term") or _text(child)
                if value:
                    record.setdefault("categories", []).append(value)
                continue
            else:
                value = _text(child)
            if value and key not in record:
                record[key] = value
        if record:
            items.append(record)
    return ParsedFeed(title=title, link=link, items=items)


class RssAuctionFetcher:
    """Fetch and parse auction feeds, but only when policy allows it."""

    def __init__(
        self,
        config: SourceConfig,
        *,
        resolver: PolicyResolver,
        datalake: DataLake | None = None,
        settings: Settings | None = None,
        audit: AccessLog | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.resolver = resolver
        self.datalake = datalake or DataLake()
        self.settings = settings or get_settings()
        self.audit = audit or AccessLog()
        self._client = client

    def _httpx(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.request_timeout_s,
                headers={"User-Agent": self.settings.user_agent},
                follow_redirects=True,
            )
        return self._client

    def fetch(
        self, feed_urls: list[str], *, crawl_id: str = "auction-rss"
    ) -> tuple[list[AuctionFeedItem], AccessDecision]:
        """Return (items, decision). A refusal returns no items and makes no request."""
        decision = self.resolver.decide(self.config.name, urls=feed_urls)
        if not decision.allowed:
            if decision.robots_allowed is False:
                outcome = AccessOutcome.REFUSED_ROBOTS
            elif decision.requires_licence:
                outcome = AccessOutcome.REFUSED_NO_LICENCE
            else:
                outcome = AccessOutcome.REFUSED_OUT_OF_SCOPE
            self.audit.record(
                AccessEvent(
                    source=self.config.name,
                    outcome=outcome,
                    operation="read_auction_feed",
                    endpoint=scrub_url(feed_urls[0]) if feed_urls else "",
                    transport="http",
                    fmt="rss",
                    reason=decision.reason,
                    actor="auction_fetcher",
                )
            )
            return [], decision

        items: list[AuctionFeedItem] = []
        for url in feed_urls:
            items.extend(self._fetch_one(url, crawl_id=crawl_id))
        return items, decision

    def _fetch_one(self, url: str, *, crawl_id: str) -> list[AuctionFeedItem]:
        try:
            response = self._httpx().get(url)
        except httpx.HTTPError as exc:
            self._record_failure(url, AccessOutcome.HTTP_ERROR, str(exc))
            return []
        if response.status_code >= 400:
            self._record_failure(
                url, AccessOutcome.HTTP_ERROR, f"HTTP {response.status_code}", response.status_code
            )
            return []

        ref = self.datalake.store(
            self.config.name, "response", response.content, url=scrub_url(url), crawl_id=crawl_id
        )
        try:
            parsed = parse_feed(response.text)
        except ET.ParseError as exc:
            self._record_failure(url, AccessOutcome.PARSER_ERROR, str(exc))
            return []

        self.audit.record(
            AccessEvent(
                source=self.config.name,
                outcome=AccessOutcome.ALLOWED,
                operation="read_auction_feed",
                endpoint=scrub_url(url),
                transport="http",
                fmt="rss",
                http_status=response.status_code,
                records=len(parsed.items),
                content_hash=ref.content_hash,
                actor="auction_fetcher",
            )
        )
        return [
            AuctionFeedItem(
                source=self.config.name,
                title=str(raw.get("title", "")),
                link=str(raw.get("link", "")),
                guid=str(raw.get("guid", "") or raw.get("id", "")),
                description=str(raw.get("description", "") or raw.get("summary", "")),
                published_at=str(raw.get("pubdate", "") or raw.get("published", "")),
                updated_at=str(raw.get("updated", "")),
                categories=[str(c) for c in raw.get("categories", []) if c],
                raw=raw,
                raw_object_id=ref.raw_object_id,
            )
            for raw in parsed.items
        ]

    def _record_failure(
        self, url: str, outcome: AccessOutcome, reason: str, http_status: int | None = None
    ) -> None:
        self.audit.record(
            AccessEvent(
                source=self.config.name,
                outcome=outcome,
                operation="read_auction_feed",
                endpoint=scrub_url(url),
                transport="http",
                fmt="rss",
                http_status=http_status,
                reason=reason,
                actor="auction_fetcher",
            )
        )
