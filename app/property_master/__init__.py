"""Property Master: the platform's central asset (§20-22, §32-33, §42-43).

The Property Master is deliberately *not* a scraper output table. It is the
resolution layer where every channel — email alerts, agent import, agency feeds,
licences — deposits an observation, and where the identity questions the
specification keeps returning to are answered:

* ``property_master_id`` - one physical unit, however many portals advertise it;
* ``listing_source_id`` - one listing on one source;
* ``listing_episode_id`` - one *time segment* of a listing (remove -> relist does
  not reset days-on-market);
* ``listing_snapshot``   - what the listing looked like at one instant.

Nothing here invents data. A missing field stays ``None`` and is reported as
``unavailable``; a removal is never called a sale.
"""

from app.property_master.coverage import SourceCoverage, coverage_report
from app.property_master.history import (
    ListingIntelligence,
    PriceHistoryEntry,
    cumulative_discount,
    listing_intelligence,
    observed_time_on_market,
    price_cut_count,
)
from app.property_master.master import PropertyMasterStore
from app.property_master.models import (
    ListingRecord,
    ListingSnapshot,
    TerritoryAssessment,
)
from app.property_master.pressure import SellerPressure, seller_pressure_score

__all__ = [
    "ListingIntelligence",
    "ListingRecord",
    "ListingSnapshot",
    "PriceHistoryEntry",
    "PropertyMasterStore",
    "SellerPressure",
    "SourceCoverage",
    "TerritoryAssessment",
    "coverage_report",
    "cumulative_discount",
    "listing_intelligence",
    "observed_time_on_market",
    "price_cut_count",
    "seller_pressure_score",
]
