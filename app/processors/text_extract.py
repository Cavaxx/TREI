"""Deterministic text field extraction, shared by every text-carrying channel.

Alert emails (§6), rendered listing pages (§11) and auction notices all describe
the same facts with the same Italian vocabulary. Keeping the patterns in one
place means a fix benefits every channel, and it keeps the extraction
deterministic-first ordering (§29) honest: a regex is tried before a browser, and
a browser before a model.

Rules:
  * one pattern per fact, each reporting *why* it matched;
  * a pattern that does not match leaves the field **absent** - never guessed;
  * the module knows nothing about HTTP, sources or policies.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from html.parser import HTMLParser

from app.core.fields import ExtractionBundle
from app.processors.normalize import (
    COMUNE_ALIASES,
    normalize_energy_class,
    normalize_text,
    parse_number,
    parse_rooms,
)

# --- numbers & money --------------------------------------------------------

_MONEY = r"(?:€|EUR|Euro|euro)\s*([\d][\d.,]{2,})"
_MONEY_AFTER = r"([\d][\d.,]{4,})\s*(?:€|EUR|Euro|euro)"
_SURFACE = r"(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:m²|m2|mq|metri\s*quadrat[io])"

_RE_PRICE = re.compile(rf"{_MONEY}|{_MONEY_AFTER}", re.IGNORECASE)
_RE_SURFACE = re.compile(_SURFACE, re.IGNORECASE)
_RE_ROOMS = re.compile(r"(\d{1,2})\s*(?:locali|vani|ambienti)", re.IGNORECASE)
_RE_BEDROOMS = re.compile(r"(\d{1,2})\s*camer[ae]\b", re.IGNORECASE)
_RE_BATHROOMS = re.compile(r"(\d{1,2})\s*bagn[io]\b", re.IGNORECASE)
_RE_FLOOR_NUM = re.compile(r"(?:piano\s*(\d{1,2})|(\d{1,2})\s*°\s*piano)", re.IGNORECASE)
_RE_FLOOR_GROUND = re.compile(r"piano\s*(terra|rialzato|seminterrato|interrato)", re.IGNORECASE)
_RE_ENERGY = re.compile(r"classe\s+energetica\s*[:\-]?\s*([A-Ga-g][1-4]?)", re.IGNORECASE)
_RE_ADDRESS = re.compile(
    r"\b((?:via|viale|piazza|piazzetta|vicolo|corso|largo|strada|contrada|frazione|località)\s+"
    r"[A-ZÀ-ÖØ-Þ][\w'’.\- ]{2,40}?)(?=[,;()\n]|\s+(?:Trento|TN|a\s)|$)",
    re.IGNORECASE,
)
_RE_CAP = re.compile(r"\b(\d{5})\b")
_RE_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

#: Words that map to boolean amenity flags.
_AMENITIES = {
    "elevator": (r"\bascensore\b", 0.88),
    "garage": (r"\b(?:garage|box\s*auto|box\b)", 0.85),
    "parking": (r"\b(?:posto\s*auto|parcheggio|posti\s*auto)\b", 0.85),
    "terrace": (r"\bterrazz[oaie]\b|\bterrazzi\b", 0.86),
    "balcony": (r"\bbalcon[ei]\b|\bbalcone\b", 0.84),
    "garden": (r"\bgiardino\b", 0.86),
    "cellar": (r"\b(?:cantina|taverna|solaio)\b", 0.80),
    "air_conditioning": (r"\b(?:aria\s*condizionata|climatizzazione|climatizzato)\b", 0.82),
}

_TYPOLOGY = {
    "appartamento": r"\bappartament[oi]\b",
    "attico": r"\battic[oi]\b",
    "villa": r"\bvill[ae]\b",
    "rustico": r"\brustic[oi]\b",
    "bilocale": r"\bbilocal[ei]\b",
    "trilocale": r"\btrilocal[ei]\b",
    "quadrilocale": r"\bquadrilocal[ei]\b",
    "monolocale": r"\bmonolocal[ei]\b",
    "terreno": r"\bterren[oi]\b",
}


def _first_group(match: re.Match) -> str | None:
    for group in match.groups():
        if group:
            return group
    return None


def extract_price(text: str) -> tuple[float | None, float, str]:
    match = _RE_PRICE.search(text or "")
    if not match:
        return None, 0.0, ""
    value = parse_number(_first_group(match))
    if value is None or value < 1000:  # below 1 000 EUR this is not an asking price
        return None, 0.0, ""
    confidence = 0.92 if "€" in match.group(0) else 0.85
    return float(value), confidence, match.group(0).strip()


def extract_surface(text: str) -> tuple[float | None, float, str]:
    match = _RE_SURFACE.search(text or "")
    if not match:
        return None, 0.0, ""
    value = parse_number(match.group(1))
    if value is None or value <= 0:
        return None, 0.0, ""
    return float(value), 0.9, match.group(0).strip()


def extract_count(text: str, pattern: re.Pattern, confidence: float) -> tuple[int | None, float, str]:
    match = pattern.search(text or "")
    if not match:
        return None, 0.0, ""
    value = parse_rooms(match.group(1))
    if value is None:
        return None, 0.0, ""
    return value, confidence, match.group(0).strip()


def extract_floor(text: str) -> tuple[int | None, str | None, float, str]:
    match = _RE_FLOOR_NUM.search(text or "")
    if match:
        value = parse_rooms(_first_group(match))
        if value is not None:
            return value, None, 0.88, match.group(0).strip()
    match = _RE_FLOOR_GROUND.search(text or "")
    if match:
        label = match.group(1).lower()
        mapping = {"terra": 0, "rialzato": 1, "seminterrato": -1, "interrato": -2}
        return mapping.get(label), label, 0.82, match.group(0).strip()
    return None, None, 0.0, ""


def extract_municipality(text: str) -> tuple[str | None, float, str]:
    """Find a known Trentino comune (the alias table is data, not logic)."""
    normalized = normalize_text(text)
    if not normalized:
        return None, 0.0, ""
    # Longest alias first so "riva del garda" wins over "riva".
    for alias in sorted(COMUNE_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", normalized):
            return COMUNE_ALIASES[alias], 0.93, alias
    return None, 0.0, ""


def extract_address(text: str) -> tuple[str | None, float, str]:
    match = _RE_ADDRESS.search(text or "")
    if not match:
        return None, 0.0, ""
    return match.group(1).strip(" ,;."), 0.72, match.group(0).strip()


def extract_energy_class(text: str) -> tuple[str | None, float, str]:
    match = _RE_ENERGY.search(text or "")
    if not match:
        return None, 0.0, ""
    value = normalize_energy_class(match.group(1))
    return (value, 0.9, match.group(0).strip()) if value else (None, 0.0, "")


def extract_typology(text: str) -> tuple[str | None, float, str]:
    lowered = (text or "").lower()
    for label, pattern in _TYPOLOGY.items():
        if re.search(pattern, lowered):
            return label, 0.8, label
    return None, 0.0, ""


def extract_postal_code(text: str) -> tuple[str | None, float, str]:
    for match in _RE_CAP.finditer(text or ""):
        cap = match.group(1)
        # Trentino CAPs start with 38; anything else is probably a phone number.
        if cap.startswith("38"):
            return cap, 0.7, cap
    return None, 0.0, ""


def extract_year_built(text: str) -> tuple[int | None, float, str]:
    match = _RE_YEAR.search(text or "")
    if not match:
        return None, 0.0, ""
    year = int(match.group(1))
    if 1800 <= year <= 2035:
        return year, 0.6, match.group(0)
    return None, 0.0, ""


def extract_listing_id(url: str, id_patterns: Sequence[str] = ()) -> tuple[str | None, float, str]:
    """First id captured by any of the source's URL id patterns."""
    for pattern in id_patterns:
        match = re.search(pattern, url or "")
        if match:
            return _first_group(match), 0.97, pattern
    return None, 0.0, ""


def extract_amenities(text: str) -> dict[str, tuple[bool, float, str]]:
    lowered = (text or "").lower()
    out: dict[str, tuple[bool, float, str]] = {}
    for name, (pattern, confidence) in _AMENITIES.items():
        match = re.search(pattern, lowered)
        if match:
            out[name] = (True, confidence, match.group(0))
    return out


def extract_condition(text: str) -> tuple[str | None, float, str]:
    from app.processors.normalize import normalize_condition

    lowered = (text or "").lower()
    for label in (
        "da ristrutturare",
        "ristrutturato",
        "nuovo",
        "ottimo",
        "abitabile",
        "buono",
        "rustico",
    ):
        if label in lowered:
            value = normalize_condition(label)
            if value:
                return value, 0.75, label
    return None, 0.0, ""


def scrape_fields(text: str, *, method: str = "regex_text") -> ExtractionBundle:
    """Extract every property field we can from one block of text.

    ``method`` records *where* the text came from (an email body, a rendered DOM,
    visible page text) so confidence is never confused with provenance (§30).
    """
    bundle = ExtractionBundle()

    for name, extractor in (
        ("price", extract_price),
        ("surface_m2", extract_surface),
    ):
        value, conf, ev = extractor(text)
        if value is not None:
            bundle.set(name, value, method=method, confidence=conf, evidence=ev)

    for name, pattern, conf in (
        ("rooms", _RE_ROOMS, 0.85),
        ("bedrooms", _RE_BEDROOMS, 0.80),
        ("bathrooms", _RE_BATHROOMS, 0.85),
    ):
        value, c, ev = extract_count(text, pattern, conf)
        if value is not None:
            bundle.set(name, value, method=method, confidence=c, evidence=ev)

    floor, floor_label, conf, ev = extract_floor(text)
    if floor is not None:
        bundle.set("floor", floor, method=method, confidence=conf, evidence=ev)
        if floor_label:
            bundle.set("floor_label", floor_label, method=method, confidence=conf, evidence=ev)

    for name, extractor in (
        ("municipality", extract_municipality),
        ("address", extract_address),
        ("energy_class", extract_energy_class),
        ("property_type", extract_typology),
        ("condition", extract_condition),
        ("postal_code", extract_postal_code),
        ("year_built", extract_year_built),
    ):
        value, conf, ev = extractor(text)
        if value is not None:
            bundle.set(name, value, method=method, confidence=conf, evidence=ev)

    for name, (flag, conf, ev) in extract_amenities(text).items():
        bundle.set(name, flag, method=method, confidence=conf, evidence=ev)

    return bundle


# --- visible text from HTML -------------------------------------------------

class _HTMLToText(HTMLParser):
    """Minimal HTML -> text: block-level tags become newlines, nothing else.

    Scripts and styles are dropped: they are not visible content, and letting
    them through would let a regex match code instead of a fact.
    """

    _BLOCK = {"p", "div", "br", "tr", "li", "td", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self, include_links: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0
        #: Alert mails often carry the listing link only in the HTML part.
        self.include_links = include_links
        self._links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._links.append(href)
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.include_links and self._links:
            self._parts.append("\n" + self._links.pop() + "\n")
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str, *, include_links: bool = False) -> str:
    """Return the human-visible text of an HTML document.

    ``include_links`` appends each anchor's ``href`` right after its text, which
    is what keeps a listing's link next to the card it belongs to.
    """
    parser = _HTMLToText(include_links=include_links)
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - malformed markup must not lose the content
        return re.sub(r"<[^>]+>", " ", html or "")
    return parser.text()
