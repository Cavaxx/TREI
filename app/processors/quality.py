"""Data-quality checks (section 48).

Outliers are *flagged*, never silently dropped. Each check returns issues with
a severity so downstream consumers can decide what to do (e.g. exclude from a
hedonic fit but still display in the listing explorer).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Plausible band for the province. Used only to flag, per the specification.
PRICE_M2_MIN = 200.0
PRICE_M2_MAX = 20000.0
SURFACE_MIN = 8.0
SURFACE_MAX = 2000.0

TRENTINO_BBOX = {  # (min_lon, min_lat, max_lon, max_lat), WGS84
    "min_lon": 10.40,
    "min_lat": 45.65,
    "max_lon": 12.05,
    "max_lat": 46.60,
}


@dataclass
class QualityIssue:
    code: str
    severity: str  # "error" | "warning" | "info"
    message: str


@dataclass
class QualityReport:
    listing_id: str
    issues: list[QualityIssue] = field(default_factory=list)
    flags: dict = field(default_factory=dict)

    @property
    def has_error(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def is_outlier(self) -> bool:
        return bool(self.flags.get("outlier"))

    def to_dict(self) -> dict:
        return {
            "listing_id": self.listing_id,
            "has_error": self.has_error,
            "is_outlier": self.is_outlier,
            "flags": self.flags,
            "issues": [{"code": i.code, "severity": i.severity, "message": i.message} for i in self.issues],
        }


def in_trentino_bbox(lat: float | None, lon: float | None) -> bool | None:
    if lat is None or lon is None:
        return None
    b = TRENTINO_BBOX
    return b["min_lon"] <= lon <= b["max_lon"] and b["min_lat"] <= lat <= b["max_lat"]


def check_listing(listing: dict) -> QualityReport:
    """Run all generic checks on a normalized listing dict."""
    lid = str(listing.get("listing_source_id") or listing.get("id") or "?")
    rep = QualityReport(listing_id=lid)

    price = listing.get("price")
    surface = listing.get("surface_m2")
    rooms = listing.get("rooms")
    lat, lon = listing.get("lat"), listing.get("lon")
    price_m2 = listing.get("price_m2")
    if price_m2 is None and price and surface:
        price_m2 = price / surface

    if price is None or price <= 0:
        rep.issues.append(QualityIssue("price_invalid", "error", "price missing or <= 0"))
    if surface is None or surface <= 0:
        rep.issues.append(QualityIssue("surface_invalid", "error", "surface missing or <= 0"))
    if rooms is not None and rooms < 0:
        rep.issues.append(QualityIssue("rooms_negative", "error", "rooms < 0"))

    if lat is None or lon is None:
        rep.issues.append(QualityIssue("coordinates_missing", "warning", "coordinates absent"))
    else:
        inside = in_trentino_bbox(lat, lon)
        if inside is False:
            rep.issues.append(
                QualityIssue("coordinates_outside_tn", "warning", "coordinates outside Trentino bbox")
            )

    if surface is not None and not (SURFACE_MIN <= surface <= SURFACE_MAX):
        rep.flags["outlier"] = True
        rep.issues.append(
            QualityIssue("surface_implausible", "warning", f"surface {surface} outside plausible range")
        )

    if price_m2 is not None and not (PRICE_M2_MIN <= price_m2 <= PRICE_M2_MAX):
        rep.flags["outlier"] = True
        rep.issues.append(
            QualityIssue("price_m2_implausible", "warning", f"price/m2 {price_m2:.0f} outside plausible range")
        )

    return rep
