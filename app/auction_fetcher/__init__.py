"""Auction channel (§33-35): RSS / authorized feeds / verified official sources.

Pipeline::

    Astalegale/RSS -> AuctionFetcher -> AuctionParser -> AuctionNormalizer
                   -> AuctionDeduplicator -> AuctionEngine

PVP is authoritative for identity; a re-listing on another portal is the *same*
lot and must collapse to one ``auction_master_id``. No auction source is crawled
without a positive policy decision from the registry.
"""

from app.auction_fetcher.models import (
    AUCTION_FIELDS,
    AUCTION_OPTIONAL_FIELDS,
    AuctionFeedItem,
    AuctionIngestReport,
    AuctionRecord,
)
from app.auction_fetcher.parser import AuctionParser, to_iso_date
from app.auction_fetcher.pipeline import AUTHORITY_ALIASES, TRENTINO_COURTS, AuctionPipeline
from app.auction_fetcher.rss import ParsedFeed, RssAuctionFetcher, parse_feed

__all__ = [
    "AUCTION_FIELDS",
    "AUCTION_OPTIONAL_FIELDS",
    "AUTHORITY_ALIASES",
    "TRENTINO_COURTS",
    "AuctionFeedItem",
    "AuctionIngestReport",
    "AuctionParser",
    "AuctionPipeline",
    "AuctionRecord",
    "ParsedFeed",
    "RssAuctionFetcher",
    "parse_feed",
    "to_iso_date",
]
