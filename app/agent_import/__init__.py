"""Channel D - Agent Import (§8-31).

A deterministic, policy-gated acquisition agent that walks *permitted* sources,
turns listing pages into structured records, validates territory, deduplicates
against the Property Master and queues anything uncertain for human review.

The import boundary is deliberate: :mod:`app.agent_import.crawler` never decides
whether it *may* crawl, and :mod:`app.agent_import.policy` never decides *what* to
crawl. Neither knows how to evade a protection.
"""

from app.agent_import.browser import (
    FallbackOutcome,
    FallbackUnavailable,
    NoVisionExtractor,
    PlaywrightRenderer,
    RenderedPage,
    render_if_needed,
)
from app.agent_import.crawler import (
    CrawlerManager,
    CrawlRequest,
    FetchOutcome,
    HttpTransport,
    classify_status,
)
from app.agent_import.extraction import (
    METHOD_ORDER,
    PageExtraction,
    discover_listing_urls,
    extract_listing,
    json_ld_nodes,
    method_rank,
    visible_text,
)
from app.agent_import.limiter import RateLimiter, RateLimitPause, parse_retry_after
from app.agent_import.models import CrawlJob, CrawlPage, SourceHealthRecord
from app.agent_import.policy import AccessDecision, PolicyResolver
from app.agent_import.state import CrawlStateStore

__all__ = [
    "METHOD_ORDER",
    "AccessDecision",
    "CrawlJob",
    "CrawlPage",
    "CrawlRequest",
    "CrawlerManager",
    "CrawlStateStore",
    "FallbackOutcome",
    "FallbackUnavailable",
    "FetchOutcome",
    "HttpTransport",
    "NoVisionExtractor",
    "PageExtraction",
    "PlaywrightRenderer",
    "PolicyResolver",
    "RateLimitPause",
    "RateLimiter",
    "RenderedPage",
    "SourceHealthRecord",
    "classify_status",
    "discover_listing_urls",
    "extract_listing",
    "json_ld_nodes",
    "method_rank",
    "parse_retry_after",
    "render_if_needed",
    "visible_text",
]
