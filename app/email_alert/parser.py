"""Alert-email parser (§6-7).

The parser is deterministic and portal-profile driven. It splits a message into
listing blocks, and for each block extracts the fields the specification asks
for. Anything it cannot find is left absent - the resulting payload fills the
canonical shape with ``None`` rather than with a plausible-looking guess.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.core.fields import ExtractionBundle
from app.email_alert import patterns
from app.email_alert.models import EmailListingCandidate, EmailMessage
from app.email_alert.profiles import PortalProfile, profile_for
from app.processors.normalize import normalize_text
from app.processors.text_extract import html_to_text

_URL_RE = re.compile(r"https?://[^\s\"'<>\)\]]+")
_IMAGE_RE = re.compile(r"\.(?:jpe?g|png|webp|avif)(?:\?|$)", re.IGNORECASE)
_TRACKING_PARAMS = re.compile(r"[?&](utm_[^=]+|gclid|fbclid|mc_[^=]+)=[^&]*", re.IGNORECASE)
_AGENCY_RE = re.compile(r"\b(?:agenzia|immobiliare)\s+([A-ZÀ-ÖØ-Þ][\w'’&.\- ]{2,40})", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:tel\.?|telefono|cell\.?)?\s*(\+?39[\s.-]?)?(0\d{1,3}[\s.-]?\d{5,8}|3\d{2}[\s.-]?\d{6,7})\b")
_WINDOW = 700


def clean_url(url: str) -> str:
    """Drop tracking parameters; keep the canonical listing URL."""
    return _TRACKING_PARAMS.sub("", url).rstrip("?&")


def _listing_urls(text: str, profile: PortalProfile) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        url = clean_url(match.group(0))
        if url in seen or _IMAGE_RE.search(url):
            continue
        if profile.listing_url_patterns:
            if not any(re.search(p, url) for p in profile.listing_url_patterns):
                continue
        elif profile.link_hosts:
            if not any(host in url for host in profile.link_hosts):
                continue
        seen.add(url)
        out.append(url)
    return out


@dataclass
class ParsedEmail:
    """Parser output: the message plus everything it produced."""

    message: EmailMessage
    profile: PortalProfile
    candidates: list[EmailListingCandidate]
    warnings: list[str]


_IT_DATE = re.compile(
    r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b|\b(\d{4})-(\d{2})-(\d{2})\b"
)


def _date_from_match(match: re.Match) -> str:
    if match.group(4):
        return f"{match.group(4)}-{match.group(5)}-{match.group(6)}"
    day, month, year = match.group(1), match.group(2), match.group(3)
    if len(year) == 2:
        year = f"20{year}"
    return f"{year}-{int(month):02d}-{int(day):02d}"


class EmailListingParser:
    """Turns one alert message into zero or more listing candidates."""

    def __init__(self, *, min_price: float = 1000.0) -> None:
        self.min_price = min_price

    # --- public -------------------------------------------------------------
    def parse(self, message: EmailMessage) -> ParsedEmail:
        combined = self._combined_text(message)
        profile = profile_for(sender=message.sender, body=combined, subject=message.subject)
        urls = _listing_urls(combined, profile)
        warnings: list[str] = []
        candidates: list[EmailListingCandidate] = []

        if urls:
            for url, block in self._blocks(combined, urls):
                candidate = self._candidate(
                    message, profile, url=url, block=block, include_subject=len(urls) == 1
                )
                if candidate is not None:
                    candidates.append(candidate)
        else:
            candidate = self._candidate(
                message, profile, url="", block=combined, include_subject=True
            )
            if candidate is None:
                warnings.append("no listing URL and no listing-like content found")
            else:
                warnings.append("no listing URL found; identifier derived from the message")
                candidates.append(candidate)

        candidates = self._dedupe(candidates, warnings)
        if not candidates:
            warnings.append("message produced no listing candidate")
        return ParsedEmail(
            message=message, profile=profile, candidates=candidates, warnings=warnings
        )

    # --- internals ----------------------------------------------------------
    @staticmethod
    def _combined_text(message: EmailMessage) -> str:
        parts = [message.subject or ""]
        if message.body_text:
            parts.append(message.body_text)
        if message.body_html:
            # Links are kept: alert mails frequently carry them only in the HTML part.
            parts.append(html_to_text(message.body_html, include_links=True))
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _window(text: str, url: str, size: int = _WINDOW) -> str:
        index = text.find(url)
        if index < 0:
            return text[:size]
        start = max(0, index - size // 2)
        return text[start : index + size // 2]

    @staticmethod
    def _blocks(text: str, urls: list[str]) -> list[tuple[str, str]]:
        """Split the message into one segment per listing URL.

        Alert cards put the facts *before* the link, so a listing's segment runs
        from the end of the previous link to the end of its own. Without this,
        one card's municipality or price would leak into its neighbour.
        """
        ends: list[int | None] = []
        cursor = 0
        for url in urls:
            found = text.find(url, cursor)
            ends.append(found + len(url) if found >= 0 else None)
            if found >= 0:
                cursor = found + len(url)
        out: list[tuple[str, str]] = []
        for index, url in enumerate(urls):
            own_end = ends[index]
            if own_end is None:
                out.append((url, text[: _WINDOW]))
                continue
            start = 0
            for previous in range(index - 1, -1, -1):
                if ends[previous] is not None:
                    start = ends[previous]
                    break
            out.append((url, text[start:own_end]))
        return out

    def _candidate(
        self,
        message: EmailMessage,
        profile: PortalProfile,
        *,
        url: str,
        block: str,
        include_subject: bool,
    ) -> EmailListingCandidate | None:
        text = f"{block}\n{message.subject}" if include_subject else block
        bundle = patterns.scrape_fields(text, url=url, profile=profile)

        listing_id, _conf, id_ev = patterns.extract_listing_id(url, profile)
        if listing_id is None:
            derived = hashlib.sha1(f"{message.raw_message_hash}|{url}".encode()).hexdigest()[:12]
            listing_id = f"EMAIL-{derived}"
            bundle.set(
                "source_listing_id_derived",
                True,
                method="derived",
                confidence=0.4,
                evidence="identifier derived from the message hash (no id in the URL)",
            )
        if url:
            bundle.set("url", url, method="email_body", confidence=0.99, evidence=id_ev or "link")

        agency = _AGENCY_RE.search(text)
        if agency:
            bundle.set(
                "agency",
                agency.group(1).strip(),
                method="email_body",
                confidence=0.6,
                evidence=agency.group(0),
            )
        phone = _PHONE_RE.search(text)
        if phone:
            bundle.set(
                "agency_phone",
                re.sub(r"[\s.\-]", "", phone.group(0)).strip("tel.:;"),
                method="email_body",
                confidence=0.55,
                evidence=phone.group(0),
            )

        date_match = _IT_DATE.search(text)
        if date_match:
            try:
                bundle.set(
                    "published_at",
                    _date_from_match(date_match),
                    method="email_body",
                    confidence=0.5,
                    evidence=date_match.group(0),
                )
            except (TypeError, ValueError):
                pass

        price, surface = bundle.value("price"), bundle.value("surface_m2")
        if price and surface:
            bundle.set(
                "price_m2",
                round(price / surface, 2),
                method="derived",
                confidence=min(
                    bundle.confidence("price") or 0.5, bundle.confidence("surface_m2") or 0.5
                ),
                evidence="price / surface_m2",
            )

        description = self._description(block)
        if description:
            bundle.set(
                "description_raw",
                description,
                method="email_body",
                confidence=0.95,
                evidence="message block, verbatim",
            )
            bundle.set(
                "description_normalized",
                normalize_text(description),
                method="derived",
                confidence=0.9,
                evidence="normalized copy of the raw description",
            )

        images = self._images(message, block)
        if images:
            bundle.set("images", images, method="email_body", confidence=0.9, evidence="image URLs")

        if not self._is_listing(bundle):
            return None

        return EmailListingCandidate(
            source=profile.name,
            source_listing_id=str(listing_id),
            url=url,
            fields=bundle,
            images=images,
            email_received_at=message.received_at,
            email_message_hash=message.raw_message_hash,
            account_id=message.account_id,
            sender=message.sender,
            subject=message.subject,
            raw_object_id=message.raw_object_id,
        )

    def _is_listing(self, bundle: ExtractionBundle) -> bool:
        """A candidate must look like a property: a price, plus one other fact."""
        price = bundle.value("price")
        if price is None or price < self.min_price:
            return False
        return any(
            bundle.value(field) is not None
            for field in ("surface_m2", "municipality", "address", "rooms", "property_type")
        )

    @staticmethod
    def _description(block: str) -> str:
        lines = [line.strip() for line in (block or "").splitlines() if line.strip()]
        return " ".join(lines)[:1200]

    @staticmethod
    def _images(message: EmailMessage, block: str) -> list[str]:
        found: list[str] = []
        for source in (message.body_html or "", block or ""):
            for match in _URL_RE.finditer(source):
                url = match.group(0)
                if _IMAGE_RE.search(url) and url not in found:
                    found.append(url)
        return found[:20]

    @staticmethod
    def _dedupe(
        candidates: list[EmailListingCandidate], warnings: list[str]
    ) -> list[EmailListingCandidate]:
        out: dict[str, EmailListingCandidate] = {}
        for candidate in candidates:
            key = candidate.source_listing_id
            if key in out:
                warnings.append(f"duplicate listing in the same message: {key}")
                continue
            out[key] = candidate
        return list(out.values())
