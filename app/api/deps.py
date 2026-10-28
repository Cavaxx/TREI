"""API dependencies: a process-wide repository and registry.

The repository is seeded with SYNTHETIC demo data only when it would otherwise
be empty, so the API is explorable out of the box. In production a Postgres
backed repository is injected here instead.
"""

from __future__ import annotations

from functools import lru_cache

from app.agent_import.state import CrawlStateStore
from app.core.enums import ObservationSource
from app.db.repository import MarketRepository
from app.db.seed import generate_demo_auctions, generate_demo_listings
from app.fetchers.licence import LicenceRegistry
from app.fetchers.registry import SourceRegistry
from app.property_master.master import PropertyMasterStore


@lru_cache
def get_repository() -> MarketRepository:
    repo = MarketRepository()
    repo.add_listings(generate_demo_listings())
    repo.add_auctions(generate_demo_auctions())
    return repo


@lru_cache
def get_property_master() -> PropertyMasterStore:
    """The Property Master, seeded with the same clearly-flagged synthetic data.

    The demo rows are recorded through the *same* observation path used by the
    real channels, so the listing-page and coverage endpoints exercise the real
    code rather than a mock.
    """
    store = PropertyMasterStore()
    for row in generate_demo_listings():
        store.observe(
            row,
            source="synthetic_demo",
            source_listing_id=str(row["listing_source_id"]),
            observed_at=row.get("first_seen_at"),
            channel=ObservationSource.SYNTHETIC_DEMO.value,
        )
    store.resolve()
    return store


@lru_cache
def get_crawl_state() -> CrawlStateStore:
    return CrawlStateStore()


@lru_cache
def get_registry() -> SourceRegistry:
    try:
        return SourceRegistry.load()
    except FileNotFoundError:
        return SourceRegistry([])


@lru_cache
def get_licences() -> LicenceRegistry:
    """The licence register. An unreadable/missing register means 'none'."""
    try:
        return LicenceRegistry.load()
    except FileNotFoundError:
        return LicenceRegistry([])
