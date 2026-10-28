"""Data lineage primitives (section 34).

Every observation stored anywhere in the platform must be able to answer:
"where did this number come from?".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class Lineage:
    """Provenance block attached to a raw or normalized record."""

    source: str
    source_url: str = ""
    source_listing_id: str = ""
    # Timestamp reported by the source (may be absent / unreliable).
    published_at: str | None = None
    # When our fetcher actually retrieved it.
    retrieved_at: str = ""
    # Last time the record was seen still present at the source.
    last_seen_at: str | None = None
    # Object-store key of the untouched raw payload.
    raw_object_id: str | None = None
    content_hash: str | None = None
    parser_version: str = ""
    schema_version: str = ""
    normalization_version: str | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        if not self.retrieved_at:
            self.retrieved_at = _now_iso()

    def to_dict(self) -> dict:
        return asdict(self)


def make_lineage(source: str, **kwargs) -> Lineage:
    """Convenience constructor that fills version defaults from core.versions."""
    from app.core.versions import (
        NORMALIZATION_VERSION,
        PARSER_VERSION,
        SCHEMA_VERSION,
    )

    kwargs.setdefault("parser_version", PARSER_VERSION)
    kwargs.setdefault("schema_version", SCHEMA_VERSION)
    kwargs.setdefault("normalization_version", NORMALIZATION_VERSION)
    return Lineage(source=source, **kwargs)
