"""Territory filter for the Autonomous Province of Trento (§17-18).

The target territory is the Provincia Autonoma di Trento. We do **not** trust a
portal's own category: the municipality, province, postal code and (when present)
coordinates are validated independently, and an ambiguous listing is marked
``LOW`` confidence and kept out of the provincial dataset.

Address normalization funnels spellings such as ``Trento``, ``Trento (TN)``,
``38122 Trento`` and ``Trento, TN`` onto the same canonical municipality name,
which is what gives one ``property_master_id`` per physical unit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.enums import TerritoryConfidence
from app.processors.normalize import COMUNE_ALIASES, normalize_comune, normalize_text

PROVINCE = "Provincia Autonoma di Trento"
#: Trentino CAPs are 380xx-381xx. This is a *hint*, not proof of membership.
_CAP_RE = re.compile(r"\b(38\d{3})\b")
_STREET_TYPES = (
    "via",
    "viale",
    "piazza",
    "piazzetta",
    "vicolo",
    "corso",
    "largo",
    "strada",
    "contrada",
    "frazione",
    "localita",
    "località",
    "salita",
    "passaggio",
)


@dataclass
class TerritoryAssessment:
    """Where a listing is, and how sure we are (kept distinct from the record)."""

    municipality: str | None = None
    province: str | None = None
    postal_code: str | None = None
    address_normalized: str | None = None
    lat: float | None = None
    lon: float | None = None
    confidence: TerritoryConfidence = TerritoryConfidence.LOW
    in_territory: bool = False
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "municipality": self.municipality,
            "province": self.province,
            "postal_code": self.postal_code,
            "address_normalized": self.address_normalized,
            "lat": self.lat,
            "lon": self.lon,
            "confidence": self.confidence.value,
            "in_territory": self.in_territory,
            "evidence": self.evidence,
        }


def normalize_address(raw: str | None) -> str | None:
    """Canonical, comparable street address (keeps the street type)."""
    if not raw:
        return None
    text = normalize_text(raw)
    if not text:
        return None
    text = re.sub(r"\b(\d{5})\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    # "via roma 12 trento" -> keep "via roma 12"; the comune is a separate field.
    for comune in COMUNE_ALIASES:
        text = re.sub(rf"\s+{re.escape(comune)}$", "", text).strip()
    return text or None


def extract_street_type(raw: str | None) -> str | None:
    if not raw:
        return None
    text = normalize_text(raw)
    for street_type in _STREET_TYPES:
        if text.startswith(normalize_text(street_type)):
            return street_type
    return None


def assess_territory(
    *,
    municipality: str | None = None,
    province: str | None = None,
    postal_code: str | None = None,
    address: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    hints: str | None = None,
) -> TerritoryAssessment:
    """Validate provincial membership from independent signals.

    Returns an assessment; ``in_territory`` is True only when at least one
    signal *positively* places the listing inside the province.
    """
    evidence: list[str] = []
    raw_municipality = municipality or ""
    if not raw_municipality and hints:
        raw_municipality = hints
    canonical = normalize_comune(raw_municipality) if raw_municipality else None

    cap = postal_code
    if not cap:
        match = _CAP_RE.search(f"{raw_municipality} {address or ''} {postal_code or ''}")
        cap = match.group(1) if match else None

    province_norm = normalize_text(province or "")
    address_norm = normalize_address(address)

    score = 0
    if canonical:
        score += 2
        evidence.append(f"municipality recognised: {canonical}")
    elif raw_municipality:
        evidence.append(f"municipality '{raw_municipality}' is not in the Trentino alias table")
    if cap:
        score += 1
        evidence.append(f"postal code {cap}")
    if province_norm in {"tn", "trento", "trentino", "provincia autonoma di trento"}:
        score += 1
        evidence.append("province field indicates Trento")
    if lat is not None and lon is not None:
        # A coarse bounding box around the province (deliberately generous).
        inside = 45.6 <= lat <= 46.6 and 10.4 <= lon <= 12.0
        evidence.append(f"coordinates {lat:.4f},{lon:.4f} are {'inside' if inside else 'outside'} the provincial bounding box")
        if inside:
            score += 2
    if address_norm:
        evidence.append(f"normalized address: {address_norm}")

    if score >= 4:
        confidence = TerritoryConfidence.HIGH
    elif score >= 2:
        confidence = TerritoryConfidence.MEDIUM
    else:
        confidence = TerritoryConfidence.LOW

    return TerritoryAssessment(
        municipality=canonical or (raw_municipality or None),
        province=province or None,
        postal_code=cap,
        address_normalized=address_norm,
        lat=lat,
        lon=lon,
        confidence=confidence,
        in_territory=confidence is not TerritoryConfidence.LOW,
        evidence=evidence,
    )
