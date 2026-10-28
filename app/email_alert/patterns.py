"""Portal-specific binding of the shared text patterns (§6).

The patterns themselves live in :mod:`app.processors.text_extract` because they
are shared with the Agent Import channel. This module only binds them to a
:class:`PortalProfile` (which knows the portal's URL id conventions).
"""

from __future__ import annotations

from app.core.fields import ExtractionBundle
from app.email_alert.profiles import PortalProfile
from app.processors import text_extract
from app.processors.text_extract import (
    extract_address,
    extract_amenities,
    extract_condition,
    extract_count,
    extract_energy_class,
    extract_floor,
    extract_municipality,
    extract_postal_code,
    extract_price,
    extract_surface,
    extract_typology,
    extract_year_built,
)

__all__ = [
    "extract_address",
    "extract_amenities",
    "extract_condition",
    "extract_count",
    "extract_energy_class",
    "extract_floor",
    "extract_listing_id",
    "extract_municipality",
    "extract_postal_code",
    "extract_price",
    "extract_surface",
    "extract_typology",
    "extract_year_built",
    "scrape_fields",
]


def extract_listing_id(url: str, profile: PortalProfile) -> tuple[str | None, float, str]:
    """Listing id from a URL, using the profile's id patterns."""
    return text_extract.extract_listing_id(url, profile.listing_id_patterns)


def scrape_fields(text: str, *, url: str = "", profile: PortalProfile | None = None) -> ExtractionBundle:
    """Field extraction for an alert-email block (method = ``email_body``)."""
    return text_extract.scrape_fields(text, method="email_body")
