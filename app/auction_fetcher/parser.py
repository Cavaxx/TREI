"""Auction parser: feed item / notice text -> structured auction fields (§33-34).

Auction notices are highly templated, so the deterministic patterns do most of
the work. What the notice does not state stays absent: an auction without a
declared minimum offer has ``minimum_offer = None``, not a guessed 75% of base.
"""

from __future__ import annotations

import re

from app.auction_fetcher.models import AuctionFeedItem, AuctionRecord
from app.core.enums import ObservationSource
from app.core.fields import ExtractionBundle
from app.processors import text_extract
from app.processors.normalize import parse_number

_RE_TRIBUNAL = re.compile(r"tribunale\s+(?:di\s+)?([A-ZÀ-ÖØ-Þ][\w'’\- ]{2,30})", re.IGNORECASE)
_RE_PROCEDURE = re.compile(
    r"(?:proc(?:edura)?\.?\s*(?:n\.?|numero|num\.?)?\s*)(\d{1,6})\s*[/\-]\s*(\d{2,4})",
    re.IGNORECASE,
)
_RE_PROCEDURE_BARE = re.compile(r"(?:rge|rg\.?\s*es\.?|esecuzione)\s*(\d{1,6})\s*[/\-]\s*(\d{2,4})", re.IGNORECASE)
_RE_LOT = re.compile(r"lott[oi]\s*(?:n\.?|numero)?\s*([A-Za-z0-9]{1,6})", re.IGNORECASE)
_RE_DATE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")
_RE_BASE = re.compile(
    r"(?:prezzo\s+base|base\s+d['’]?\s*asta|valore\s+base)\s*[:\-]?\s*(?:€|EUR|euro)?\s*([\d][\d.,]*)",
    re.IGNORECASE,
)
_RE_MIN_OFFER = re.compile(
    r"(?:offerta\s+minima|minimo\s+offerta|prezzo\s+minimo)\s*[:\-]?\s*(?:€|EUR|euro)?\s*([\d][\d.,]*)",
    re.IGNORECASE,
)
_RE_DEPOSIT = re.compile(r"cauzion[ei]\s*[:\-]?\s*(?:€|EUR|euro)?\s*([\d][\d.,]*)", re.IGNORECASE)
_RE_APPRAISAL = re.compile(
    r"(?:valore\s+di\s+stima|stima|perizia)\s*[:\-]?\s*(?:€|EUR|euro)?\s*([\d][\d.,]*)",
    re.IGNORECASE,
)
_RE_OCCUPANCY = re.compile(r"\b(libero|occupato|locato|non\s+libero)\b", re.IGNORECASE)
_RE_CADASTRAL = re.compile(r"(foglio\s*\d+[^.\n]{0,60}|particella\s*\d+[^.\n]{0,40})", re.IGNORECASE)
_RE_ABUSE = re.compile(r"\b(abusi?\s+edilizi?|difformit[àa]|irregolarit[àa]\s+edilizia)\b", re.IGNORECASE)
_RE_ARREARS = re.compile(r"(oneri\s+condominiali[^.\n]{0,60}|spese\s+condominiali[^.\n]{0,60})", re.IGNORECASE)


def to_iso_date(raw: str | None) -> str | None:
    """dd/mm/yyyy or dd.mm.yyyy -> YYYY-MM-DD."""
    if not raw:
        return None
    match = _RE_DATE.search(raw)
    if not match:
        return None
    day, month, year = match.group(1), match.group(2), match.group(3)
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return None


def _money(pattern: re.Pattern, text: str) -> tuple[float | None, str]:
    match = pattern.search(text or "")
    if not match:
        return None, ""
    value = parse_number(match.group(1))
    return (float(value), match.group(0).strip()) if value else (None, "")


class AuctionParser:
    """Deterministic extraction of auction facts from a notice."""

    def parse_text(self, text: str, *, url: str = "", source: str = "") -> ExtractionBundle:
        bundle = ExtractionBundle()
        method = "regex_text"

        match = _RE_TRIBUNAL.search(text or "")
        if match:
            bundle.set(
                "tribunal",
                match.group(1).strip(),
                method=method,
                confidence=0.85,
                evidence=match.group(0),
            )

        for pattern in (_RE_PROCEDURE, _RE_PROCEDURE_BARE):
            match = pattern.search(text or "")
            if match:
                number, year = match.group(1), match.group(2)
                year = f"20{year}" if len(year) == 2 else year
                bundle.set(
                    "procedure_number",
                    number,
                    method=method,
                    confidence=0.85,
                    evidence=match.group(0),
                )
                bundle.set(
                    "procedure_year",
                    int(year),
                    method=method,
                    confidence=0.8,
                    evidence=match.group(0),
                )
                break

        match = _RE_LOT.search(text or "")
        if match:
            bundle.set(
                "lot_number",
                match.group(1),
                method=method,
                confidence=0.75,
                evidence=match.group(0),
            )

        iso = to_iso_date(text)
        if iso:
            bundle.set(
                "auction_date", iso, method=method, confidence=0.8, evidence="date in the notice"
            )

        for field_name, pattern, confidence in (
            ("starting_price", _RE_BASE, 0.88),
            ("minimum_offer", _RE_MIN_OFFER, 0.88),
            ("deposit", _RE_DEPOSIT, 0.8),
            ("appraisal_value", _RE_APPRAISAL, 0.8),
        ):
            value, evidence = _money(pattern, text)
            if value is not None:
                bundle.set(field_name, value, method=method, confidence=confidence, evidence=evidence)

        match = _RE_OCCUPANCY.search(text or "")
        if match:
            label = match.group(1).lower()
            occupancy = "free" if label == "libero" else "occupied"
            bundle.set("occupancy", occupancy, method=method, confidence=0.8, evidence=match.group(0))

        for field_name, extractor in (
            ("municipality", text_extract.extract_municipality),
            ("address", text_extract.extract_address),
            ("surface_m2", text_extract.extract_surface),
            ("property_type", text_extract.extract_typology),
        ):
            value, confidence, evidence = extractor(text)
            if value is not None:
                bundle.set(field_name, value, method=method, confidence=confidence, evidence=evidence)

        match = _RE_CADASTRAL.search(text or "")
        if match:
            bundle.set(
                "cadastral_data",
                match.group(1).strip(),
                method=method,
                confidence=0.6,
                evidence=match.group(0),
            )
        match = _RE_ABUSE.search(text or "")
        if match:
            bundle.set("building_abuses", True, method=method, confidence=0.6, evidence=match.group(0))
        match = _RE_ARREARS.search(text or "")
        if match:
            bundle.set(
                "condominium_arrears",
                match.group(0).strip(),
                method=method,
                confidence=0.5,
                evidence=match.group(0),
            )

        if url:
            bundle.set("auction_url", url, method="metadata", confidence=0.95, evidence="feed link")
        return bundle

    def parse_feed_item(self, item: AuctionFeedItem) -> AuctionRecord:
        """Parse one feed item into an :class:`AuctionRecord`."""
        text = "\n".join(part for part in (item.title, item.description, item.link) if part)
        bundle = self.parse_text(text, url=item.link, source=item.source)
        url = bundle.value("auction_url") or item.link
        return AuctionRecord(
            source=item.source,
            fields=bundle,
            auction_url=url,
            published_at=item.published_at or item.updated_at,
            raw_object_id=item.raw_object_id,
            pvp_id=str(item.raw.get("pvp_id") or "") if isinstance(item.raw, dict) else "",
        )


def auction_observation_channel() -> str:
    """The observation channel auctions are recorded under."""
    return ObservationSource.AUCTION.value
