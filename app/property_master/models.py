"""Core records of the Property Master (§20-22)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from app.core.enums import GeocodingPrecision
from app.processors.territory import TerritoryAssessment


def _hash_of(value: object) -> str:
    """Stable content hash of a JSON-serializable object."""
    if value is None:
        return ""
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class ListingSnapshot:
    """One immutable observation of a listing at a point in time (§22)."""

    captured_at: str
    listing_source_id: str
    source: str
    price: float | None = None
    surface_m2: float | None = None
    description_hash: str = ""
    images_hash: str = ""
    #: "active" | "removed" | "unknown" - never "sold" unless the source said so.
    availability: str = "active"
    source_url: str = ""
    raw_object_id: str = ""
    #: Which channel produced this observation (ObservationSource value).
    observed_via: str = ""
    #: 0 = ListingEvent-free snapshot; otherwise a ListingEvent value.
    event: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ListingRecord:
    """One listing on one source, with its resolution keys and history."""

    listing_source_id: str
    source: str
    url: str = ""
    #: All three identities are kept distinct on purpose.
    property_master_id: str | None = None
    listing_episode_id: str | None = None
    first_seen_at: str = ""
    last_seen_at: str = ""
    #: The channel that first observed this listing (ObservationSource value).
    first_seen_source: str = ""
    email_received_at: str | None = None
    source_published_at: str | None = None
    is_active: bool = True
    price: float | None = None
    surface_m2: float | None = None
    description_hash: str = ""
    images_hash: str = ""
    raw_object_id: str = ""
    territory: TerritoryAssessment = field(default_factory=TerritoryAssessment)
    geocoding_source: str = ""
    geocoding_precision: GeocodingPrecision = GeocodingPrecision.NONE
    geocoded_at: str | None = None
    #: true when at least one extracted field fell below the review threshold.
    review_required: bool = False
    review_fields: list[str] = field(default_factory=list)
    #: The normalized listing payload as stored (never mutated in place).
    payload: dict = field(default_factory=dict)
    #: Every source that has ever advertised this physical unit.
    sources: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return listing_key(self.source, self.listing_source_id)

    @property
    def days_on_market(self) -> int | None:
        from app.property_master.history import _parse_ts

        start, end = _parse_ts(self.first_seen_at), _parse_ts(self.last_seen_at)
        if start is None or end is None:
            return None
        return max(0, (end - start).days)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["territory"] = self.territory.to_dict()
        data["geocoding_precision"] = self.geocoding_precision.value
        data["key"] = self.key
        data["days_on_market"] = self.days_on_market
        return data


def listing_key(source: str, listing_source_id: str) -> str:
    """Stable key for one listing on one source."""
    return f"{source}::{listing_source_id}"


def content_hashes(payload: dict) -> tuple[str, str]:
    """(description_hash, images_hash) for a listing payload.

    The images hash is order-insensitive so the same gallery in a different order
    is recognised as unchanged.
    """
    description = payload.get("description") or payload.get("description_raw")
    images = payload.get("images") or []
    normalised_images = sorted(str(i) for i in images) if isinstance(images, list) else []
    return _hash_of(description), _hash_of(normalised_images)
