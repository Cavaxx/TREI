"""Per-field extraction primitives (§30-31).

Every field value the platform extracts carries *how* it was obtained and *how
confident* the extractor is. This is what makes the deterministic-first pipeline
(§29) auditable and what drives the human-review queue (§31): a value is never
silently accepted, and a missing value is never invented.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: The canonical field set every listing payload carries (§6). All keys are
#: always present; a value the source did not state stays ``None``.
CANONICAL_FIELDS = (
    "source",
    "source_listing_id",
    "price",
    "surface_m2",
    "municipality",
    "address",
    "bedrooms",
    "rooms",
    "bathrooms",
    "floor",
    "elevator",
    "garage",
    "description",
    "url",
    "images",
    "published_at",
)

#: Optional, richer fields (§14). Present only when actually extracted.
OPTIONAL_FIELDS = (
    "property_type",
    "condition",
    "energy_class",
    "year_built",
    "terrace",
    "balcony",
    "garden",
    "cellar",
    "heating",
    "air_conditioning",
    "parking",
    "agency",
    "agency_phone",
    "price_m2",
    "province",
    "postal_code",
    "description_raw",
    "description_normalized",
    "lat",
    "lon",
)


@dataclass(frozen=True)
class FieldValue:
    """One extracted field, with its provenance and confidence."""

    value: Any
    method: str = "regex_text"
    confidence: float = 0.5
    #: Free-text pointer to where in the document the value came from.
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
        }


@dataclass
class ExtractionBundle:
    """A set of extracted fields for one listing, plus its overall confidence."""

    fields: dict[str, FieldValue] = field(default_factory=dict)

    def set(
        self,
        name: str,
        value: Any,
        *,
        method: str,
        confidence: float,
        evidence: str = "",
    ) -> None:
        """Record a field.

        An empty/None value is *ignored*: absence must remain absence (§6). A
        lower-confidence value never overwrites a higher-confidence one.
        """
        if value is None or value == "" or value == []:
            return
        current = self.fields.get(name)
        if current is not None and current.confidence >= confidence:
            return
        self.fields[name] = FieldValue(
            value=value, method=method, confidence=confidence, evidence=evidence
        )

    def value(self, name: str, default: Any = None) -> Any:
        field_value = self.fields.get(name)
        return default if field_value is None else field_value.value

    def confidence(self, name: str) -> float | None:
        field_value = self.fields.get(name)
        return None if field_value is None else field_value.confidence

    def method(self, name: str) -> str | None:
        field_value = self.fields.get(name)
        return None if field_value is None else field_value.method

    def low_confidence(self, threshold: float) -> list[str]:
        """Fields present but below ``threshold`` - the review queue input."""
        return sorted(n for n, f in self.fields.items() if f.confidence < threshold)

    def missing(self, names: list[str]) -> list[str]:
        return sorted(n for n in names if n not in self.fields)

    @property
    def overall_confidence(self) -> float:
        if not self.fields:
            return 0.0
        return sum(f.confidence for f in self.fields.values()) / len(self.fields)

    def flatten(self) -> dict:
        """``{name: value}`` - for writing to the property master."""
        return {name: f.value for name, f in self.fields.items()}

    def to_dict(self) -> dict:
        return {name: f.to_dict() for name, f in sorted(self.fields.items())}

    @classmethod
    def from_dict(cls, data: dict) -> ExtractionBundle:
        bundle = cls()
        for name, payload in (data or {}).items():
            if isinstance(payload, dict) and "value" in payload:
                bundle.fields[name] = FieldValue(
                    value=payload["value"],
                    method=payload.get("method", "unknown"),
                    confidence=float(payload.get("confidence", 0.0)),
                    evidence=payload.get("evidence", ""),
                )
        return bundle

    def asdict(self) -> dict:
        return asdict(self)


def bundle_to_payload(
    bundle: ExtractionBundle,
    *,
    source: str,
    source_listing_id: str,
    url: str = "",
    images: list[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """Turn an extraction bundle into the canonical listing payload (§6).

    Every canonical key is present; anything not extracted is ``None``. The
    ``_extraction`` block keeps per-field method and confidence (§30) so a
    downstream reviewer can see exactly where each number came from.
    """
    flat = bundle.flatten()
    payload: dict = {name: None for name in CANONICAL_FIELDS}
    for name in CANONICAL_FIELDS + OPTIONAL_FIELDS:
        if name in flat:
            payload[name] = flat[name]
    payload["source"] = source
    payload["source_listing_id"] = source_listing_id
    payload["url"] = url or flat.get("url")
    payload["images"] = list(images if images is not None else (flat.get("images") or []))
    # ``comune`` is the key the normalizer and the Property Master use.
    payload["comune"] = payload.get("municipality")
    payload["description"] = payload.get("description") or payload.get("description_raw")
    payload["_extraction"] = bundle.to_dict()
    if extra:
        payload.update(extra)
    return payload
