"""Auction acquisition objects (§33-35).

Auctions are a *separate* pipeline with their own identity rules: a lot is
identified by (tribunale, numero procedura, lotto), and the official PVP record
wins over any re-listing. Prices are auction base/minimum-offer prices, a
different measure from asking prices, and never mixed with them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.core.fields import ExtractionBundle

#: The auction fields we try to acquire (§34).
AUCTION_FIELDS = (
    "tribunal",
    "procedure_number",
    "procedure_year",
    "lot_number",
    "auction_date",
    "property_type",
    "municipality",
    "address",
    "surface_m2",
    "starting_price",
    "minimum_offer",
    "deposit",
    "appraisal_value",
    "occupancy",
    "auction_url",
    "documents",
)

#: Richer fields, present only when the source publishes them (§34, second list).
AUCTION_OPTIONAL_FIELDS = (
    "cadastral_data",
    "appraisal_date",
    "previous_auction_dates",
    "previous_prices",
    "building_abuses",
    "regularization_costs",
    "condominium_arrears",
)


@dataclass
class AuctionFeedItem:
    """One item from an RSS/Atom feed or a licensed export."""

    source: str
    title: str = ""
    link: str = ""
    guid: str = ""
    description: str = ""
    published_at: str = ""
    updated_at: str = ""
    categories: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    raw_object_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuctionRecord:
    """One normalized auction lot, with its extraction provenance."""

    source: str
    master_auction_id: str = ""
    pvp_id: str = ""
    fields: ExtractionBundle = field(default_factory=ExtractionBundle)
    auction_url: str = ""
    sources: list[str] = field(default_factory=list)
    published_at: str = ""
    raw_object_id: str = ""
    revision: int = 1

    def value(self, name: str, default=None):
        return self.fields.value(name, default)

    def to_payload(self) -> dict:
        """Flat dict in the canonical auction shape."""
        flat = self.fields.flatten()
        payload: dict = {name: None for name in AUCTION_FIELDS}
        for name in AUCTION_FIELDS + AUCTION_OPTIONAL_FIELDS:
            if name in flat:
                payload[name] = flat[name]
        payload["source"] = self.source
        payload["pvp_id"] = self.pvp_id or payload.get("pvp_id")
        payload["auction_url"] = self.auction_url or payload.get("auction_url")
        payload["master_auction_id"] = self.master_auction_id
        payload["published_at"] = self.published_at
        payload["sources"] = sorted(self.sources or [self.source])
        payload["_extraction"] = self.fields.to_dict()
        return payload

    def to_dict(self) -> dict:
        data = self.to_payload()
        data["fields"] = self.fields.to_dict()
        return data


@dataclass
class AuctionIngestReport:
    source: str
    items_seen: int = 0
    records: int = 0
    out_of_territory: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auctions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
