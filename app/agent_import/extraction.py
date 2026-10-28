"""Deterministic extraction from listing pages (§11, §14, §29-30).

The order is fixed and matters::

    JSON-LD  ->  embedded JSON state  ->  microdata  ->  semantic HTML
             ->  metadata / OpenGraph  ->  visible text

This is the deterministic part of the pipeline. An LLM or a vision model is only
ever a *last* fallback (see :mod:`app.agent_import.browser`), because using one
for every field of every page multiplies cost, latency, error and variance.

Every produced value is a :class:`~app.core.fields.FieldValue`: it knows its
method and confidence, and a missing fact stays missing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from app.core.fields import ExtractionBundle
from app.processors import text_extract

#: schema.org types that describe a property listing.
LISTING_TYPES = {
    "realestatelisting",
    "offer",
    "product",
    "apartment",
    "house",
    "residence",
    "singlefamilyresidence",
    "place",
    "accommodation",
}

_JSON_LD_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
_EMBEDDED_JSON_RE = re.compile(
    r"(?:__NEXT_DATA__|__INITIAL_STATE__|__PRELOADED_STATE__|window\.__DATA__)\s*[=:]\s*(\{.*?\})\s*[;<]",
    re.DOTALL,
)
_SCRIPT_JSON_RE = re.compile(
    r"<script[^>]*type=[\"']application/json[\"'][^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
_IMAGE_URL_RE = re.compile(r"https?://[^\s\"'<>]+\.(?:jpe?g|png|webp|avif)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"'<>\)\]]+")


@dataclass
class PageExtraction:
    """Everything deterministic extraction produced for one page."""

    url: str
    bundle: ExtractionBundle = field(default_factory=ExtractionBundle)
    #: Dominant method used (best-ranked method that produced a field).
    method: str = ""
    title: str = ""
    raw_text_chars: int = 0
    #: Listing links found on the page (search pages).
    links: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_listing_data(self) -> bool:
        return bool(self.bundle.value("price")) and bool(
            self.bundle.value("surface_m2") or self.bundle.value("address") or self.bundle.value("municipality")
        )

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "method": self.method,
            "title": self.title,
            "raw_text_chars": self.raw_text_chars,
            "fields": self.bundle.to_dict(),
            "links": self.links,
            "notes": self.notes,
        }


# --- JSON-LD ----------------------------------------------------------------

def json_ld_nodes(html: str) -> list[dict]:
    """Return every JSON-LD node (``@graph`` flattened)."""
    nodes: list[dict] = []
    for match in _JSON_LD_RE.finditer(html or ""):
        try:
            data = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        nodes.extend(_flatten_json(data))
    return nodes


def _flatten_json(data: Any) -> list[dict]:
    out: list[dict] = []
    if isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                out.extend(_flatten_json(item))
        out.append(data)
    elif isinstance(data, list):
        for item in data:
            out.extend(_flatten_json(item))
    return out


def _types_of(node: dict) -> set[str]:
    raw = node.get("@type") or node.get("type") or ""
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).split("/")[-1].lower() for v in values}


def _best_listing_node(nodes: list[dict]) -> dict | None:
    scored: list[tuple[int, dict]] = []
    for node in nodes:
        types = _types_of(node)
        score = 0
        if "realestatelisting" in types:
            score += 3
        if types & LISTING_TYPES:
            score += 2
        if any(k in node for k in ("offers", "price", "floorSize", "numberOfRooms")):
            score += 2
        if score:
            scored.append((score, node))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


# --- schema.org mapping -----------------------------------------------------

def _to_number(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        from app.processors.normalize import parse_number

        return parse_number(raw)
    if isinstance(raw, dict):
        for key in ("value", "@value", "amount", "price"):
            if key in raw:
                return _to_number(raw[key])
    return None


def _to_int(raw: Any) -> int | None:
    value = _to_number(raw)
    return None if value is None else int(round(value))


def _apply_schema_org(node: dict, bundle: ExtractionBundle, *, method: str = "json_ld") -> None:
    """Map one schema.org node onto our field vocabulary."""
    confidence = 0.99 if method == "json_ld" else 0.8

    name = node.get("name") or node.get("headline")
    description = node.get("description")
    if description:
        bundle.set("description_raw", str(description), method=method, confidence=confidence, evidence="schema.org description")
        bundle.set(
            "description_normalized",
            text_extract.normalize_text(str(description)),
            method="derived",
            confidence=confidence - 0.05,
            evidence="normalized copy",
        )
    if name and not bundle.value("title"):
        bundle.set("title", str(name), method=method, confidence=confidence, evidence="schema.org name")

    offers = node.get("offers")
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if isinstance(offer, dict):
        price = _to_number(offer.get("price") or offer.get("lowPrice"))
        if price is not None:
            bundle.set("price", price, method=method, confidence=confidence, evidence="schema.org offers.price")
        url = offer.get("url")
        if url:
            bundle.set("url", str(url), method=method, confidence=0.9, evidence="schema.org offers.url")
    price = _to_number(node.get("price"))
    if price is not None:
        bundle.set("price", price, method=method, confidence=confidence - 0.05, evidence="schema.org price")

    floor_size = node.get("floorSize")
    surface = _to_number(floor_size)
    if surface is not None:
        bundle.set("surface_m2", surface, method=method, confidence=confidence - 0.02, evidence="schema.org floorSize")

    for field_name, keys in (
        ("rooms", ("numberOfRooms", "numberOfTotalRooms")),
        ("bedrooms", ("numberOfBedrooms", "numberOfRooms")),
        ("bathrooms", ("numberOfBathroomsTotal", "numberOfBathrooms")),
    ):
        for key in keys:
            value = _to_int(node.get(key))
            if value is not None:
                bundle.set(field_name, value, method=method, confidence=confidence - 0.05, evidence=f"schema.org {key}")
                break

    if node.get("petsAllowed") is not None:  # pragma: no cover - rarely present
        pass
    floor = node.get("floorLevel") or node.get("floor")
    if floor is not None:
        bundle.set("floor", str(floor), method=method, confidence=confidence - 0.1, evidence="schema.org floorLevel")

    address = node.get("address")
    if isinstance(address, dict):
        street = address.get("streetAddress")
        if street:
            bundle.set("address", str(street), method=method, confidence=confidence - 0.02, evidence="schema.org streetAddress")
        locality = address.get("addressLocality")
        if locality:
            bundle.set("municipality_raw", str(locality), method=method, confidence=confidence - 0.02, evidence="schema.org addressLocality")
        region = address.get("addressRegion")
        if region:
            bundle.set("province", str(region), method=method, confidence=confidence - 0.02, evidence="schema.org addressRegion")
        cap = address.get("postalCode")
        if cap:
            bundle.set("postal_code", str(cap), method=method, confidence=confidence - 0.02, evidence="schema.org postalCode")

    geo = node.get("geo")
    if isinstance(geo, dict):
        lat, lon = _to_number(geo.get("latitude")), _to_number(geo.get("longitude"))
        if lat is not None and lon is not None:
            bundle.set("lat", lat, method=method, confidence=confidence - 0.05, evidence="schema.org geo")
            bundle.set("lon", lon, method=method, confidence=confidence - 0.05, evidence="schema.org geo")

    images = node.get("image")
    urls: list[str] = []
    for item in images if isinstance(images, list) else [images]:
        if isinstance(item, str) and item.startswith("http"):
            urls.append(item)
        elif isinstance(item, dict) and isinstance(item.get("url"), str):
            urls.append(item["url"])
    if urls:
        bundle.set("images", urls, method=method, confidence=confidence - 0.05, evidence="schema.org image")

    identifier = node.get("identifier") or node.get("sku") or node.get("@id")
    if isinstance(identifier, dict):
        identifier = identifier.get("value") or identifier.get("@id")
    if identifier:
        bundle.set("source_listing_id", str(identifier), method=method, confidence=confidence - 0.05, evidence="schema.org identifier")

    posted = node.get("datePosted") or node.get("datePublished")
    if posted:
        bundle.set("published_at", str(posted), method=method, confidence=confidence - 0.1, evidence="schema.org datePosted")


# --- embedded JSON / microdata / metadata -----------------------------------

def embedded_json_objects(html: str) -> list[dict]:
    """Find embedded application state (Next.js, generic hydration blobs)."""
    out: list[dict] = []
    for pattern in (_EMBEDDED_JSON_RE, _SCRIPT_JSON_RE):
        for match in pattern.finditer(html or ""):
            try:
                data = json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue
            out.extend(_flatten_json(data))
    return out


def _walk_search(data: Any, keys: set[str], depth: int = 0) -> list[dict]:
    """Depth-limited search for dicts containing any of ``keys``."""
    found: list[dict] = []
    if depth > 6:
        return found
    if isinstance(data, dict):
        if keys & set(data):
            found.append(data)
        for value in data.values():
            found.extend(_walk_search(value, keys, depth + 1))
    elif isinstance(data, list):
        for item in data:
            found.extend(_walk_search(item, keys, depth + 1))
    return found


_PRICE_KEYS = {"price", "prezzo", "askingPrice", "priceValue"}
_SURFACE_KEYS = {"surface", "superficie", "surfaceM2", "mq", "size"}
_ID_KEYS = {"id", "listingId", "annuncioId", "adId"}

_EMBEDDED_MAP = {
    "price": ("price", "prezzo", "askingPrice", "priceValue"),
    "surface_m2": ("surface", "superficie", "surfaceM2", "mq", "size"),
    "rooms": ("rooms", "locali", "vani"),
    "bathrooms": ("bathrooms", "bagni"),
    "address": ("address", "indirizzo", "streetAddress"),
    "municipality_raw": ("city", "comune", "municipality", "town"),
    "postal_code": ("postalCode", "cap", "zip"),
    "description_raw": ("description", "descrizione"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "longitude"),
    "source_listing_id": ("id", "listingId", "annuncioId", "adId", "sku"),
}


def extract_embedded_state(html: str, bundle: ExtractionBundle) -> bool:
    """Populate ``bundle`` from the page's embedded JSON. Returns True on success."""
    objects = embedded_json_objects(html)
    candidates = _walk_search(objects, _PRICE_KEYS | _SURFACE_KEYS)
    if not candidates:
        return False
    # Prefer the object with the most listing-like keys.
    candidates.sort(
        key=lambda obj: len(_PRICE_KEYS & set(obj)) + len(_SURFACE_KEYS & set(obj)),
        reverse=True,
    )
    node = candidates[0]
    for field_name, keys in _EMBEDDED_MAP.items():
        for key in keys:
            if key in node and node[key] not in (None, "", []):
                bundle.set(
                    field_name,
                    node[key],
                    method="embedded_json",
                    confidence=0.85,
                    evidence=f"embedded JSON key {key}",
                )
                break
    return True


def extract_microdata(html: str, bundle: ExtractionBundle) -> bool:
    """Read schema.org microdata (``itemprop``) from the HTML."""
    soup = BeautifulSoup(html or "", "lxml")
    found = False
    itemprop_map = {
        "price": "price",
        "streetaddress": "address",
        "addresslocality": "municipality_raw",
        "postalcode": "postal_code",
        "description": "description_raw",
        "floorSize": "surface_m2",
        "numberofrooms": "rooms",
        "name": "title",
    }
    for element in soup.find_all(attrs={"itemprop": True}):
        prop = str(element.get("itemprop", "")).strip()
        key = itemprop_map.get(prop) or itemprop_map.get(prop.lower())
        if not key:
            continue
        value = element.get("content") or element.get_text(" ", strip=True)
        if value:
            bundle.set(key, value, method="microdata", confidence=0.8, evidence=f"itemprop={prop}")
            found = True
    return found


# --- metadata / OpenGraph ---------------------------------------------------

def extract_metadata(html: str, bundle: ExtractionBundle) -> None:
    soup = BeautifulSoup(html or "", "lxml")
    if soup.title and soup.title.string:
        bundle.set("title", soup.title.string.strip(), method="metadata", confidence=0.9, evidence="<title>")
    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or "").lower()
        content = meta.get("content")
        if not content:
            continue
        if key in {"og:title", "twitter:title"}:
            bundle.set("title", content, method="metadata", confidence=0.9, evidence=key)
        elif key in {"og:description", "description", "twitter:description"}:
            bundle.set("description_raw", content, method="metadata", confidence=0.8, evidence=key)
        elif key in {"og:url"}:
            bundle.set("url", content, method="metadata", confidence=0.8, evidence=key)
        elif key in {"og:image", "twitter:image"}:
            bundle.set("images", [content], method="metadata", confidence=0.7, evidence=key)
        elif key in {"og:latitude", "place:location:latitude"}:
            bundle.set("lat", _to_number(content), method="metadata", confidence=0.7, evidence=key)
        elif key in {"og:longitude", "place:location:longitude"}:
            bundle.set("lon", _to_number(content), method="metadata", confidence=0.7, evidence=key)
        elif key in {"geo.placename", "og:locality"}:
            bundle.set("municipality_raw", content, method="metadata", confidence=0.7, evidence=key)


# --- semantic HTML ----------------------------------------------------------

_LABELS: dict[str, tuple[str, ...]] = {
    "price": ("prezzo", "prezzo richiesto", "price"),
    "price_m2": ("prezzo al mq", "prezzo/mq", "prezzo al m²", "price/m2"),
    "surface_m2": ("superficie", "superficie commerciale", "mq", "m²"),
    "rooms": ("locali", "vani", "stanze"),
    "bedrooms": ("camere", "camere da letto"),
    "bathrooms": ("bagni", "bagno"),
    "floor": ("piano", "piano di calpestio"),
    "energy_class": ("classe energetica", "energia"),
    "year_built": ("anno di costruzione", "anno costruzione"),
    "condition": ("stato", "condizione"),
    "address": ("indirizzo", "via", "viale", "piazza"),
    "municipality_raw": ("comune", "città", "city"),
    "postal_code": ("cap", "codice postale"),
    "agency": ("agenzia", "riferimento", "inserzionista"),
}


def extract_semantic_html(html: str, bundle: ExtractionBundle) -> bool:
    """Read ``label: value`` pairs from definition lists and key/value blocks."""
    soup = BeautifulSoup(html or "", "lxml")
    found = False
    for container in soup.find_all(["dl", "ul", "table", "section", "div"]):
        texts = container.find_all(["dt", "dd", "th", "td", "li", "span"], recursive=True)
        if len(texts) > 40 or not texts:
            continue
        values = [t.get_text(" ", strip=True) for t in texts]
        for index, label in enumerate(values):
            normalized = label.rstrip(":").lower()
            field_name = next(
                (f for f, labels in _LABELS.items() if normalized in labels), None
            )
            if field_name and index + 1 < len(values):
                candidate = values[index + 1]
                if candidate and candidate != label:
                    bundle.set(
                        field_name,
                        candidate,
                        method="semantic_html",
                        confidence=0.75,
                        evidence=f"label '{label}'",
                    )
                    found = True
    return found


# --- visible text & link discovery ------------------------------------------

def visible_text(html: str) -> str:
    """Strip markup and return human-visible text (scripts/styles removed)."""
    return text_extract.html_to_text(html)


def discover_listing_urls(
    html: str,
    *,
    base_url: str = "",
    url_patterns: tuple[str, ...] | list[str] = (),
    limit: int = 200,
) -> list[str]:
    """Collect unique listing-detail URLs from a search page (§10).

    Only URLs matching the source's declared patterns (when given) are returned.
    Nothing is fetched here; discovery and navigation stay separate so the
    catalogue can be sized before any detail request is made.
    """
    from urllib.parse import urljoin, urlsplit, urlunsplit

    soup = BeautifulSoup(html or "", "lxml")
    found: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(base_url, href) if base_url else href
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"}:
            continue
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        if url_patterns and not any(re.search(p, clean) for p in url_patterns):
            continue
        if clean in seen:
            continue
        seen.add(clean)
        found.append(clean)
        if len(found) >= limit:
            break
    return found


# --- finalization & top-level entry point -----------------------------------

#: Deterministic-first ordering (§11/§29). Lower index = more trusted.
METHOD_ORDER = (
    "structured_feed",
    "json_ld",
    "embedded_json",
    "microdata",
    "semantic_html",
    "metadata",
    "regex_text",
    "browser_dom",
    "vision",
    "email_body",
    "manual",
    "derived",
)


def method_rank(method: str) -> int:
    return METHOD_ORDER.index(method) if method in METHOD_ORDER else len(METHOD_ORDER)


_NUMERIC_FIELDS = ("price", "surface_m2", "price_m2", "lat", "lon")
_INTEGER_FIELDS = ("rooms", "bedrooms", "bathrooms", "floor")


def finalize_bundle(bundle: ExtractionBundle, *, url: str = "", id_patterns: Sequence[str] = ()) -> None:
    """Coerce extracted strings into typed values and derive what is derivable.

    Anything that cannot be coerced is *dropped* rather than kept as a string:
    a price that is not a number is not a price.
    """
    from app.processors.normalize import normalize_comune, normalize_energy_class

    for name in _NUMERIC_FIELDS:
        field_value = bundle.fields.get(name)
        if field_value is None:
            continue
        value = _to_number(field_value.value)
        if value is None:
            bundle.fields.pop(name, None)
        elif not isinstance(field_value.value, (int, float)):
            bundle.fields[name] = type(field_value)(
                value=value,
                method=field_value.method,
                confidence=field_value.confidence,
                evidence=field_value.evidence,
            )

    for name in _INTEGER_FIELDS:
        field_value = bundle.fields.get(name)
        if field_value is None:
            continue
        value = _to_int(field_value.value)
        if value is None:
            bundle.fields.pop(name, None)
        elif not isinstance(field_value.value, int):
            bundle.fields[name] = type(field_value)(
                value=value,
                method=field_value.method,
                confidence=field_value.confidence,
                evidence=field_value.evidence,
            )

    raw_municipality = bundle.value("municipality_raw")
    if raw_municipality:
        canonical = normalize_comune(str(raw_municipality))
        origin = bundle.fields["municipality_raw"]
        if canonical:
            bundle.set(
                "municipality",
                canonical,
                method=origin.method,
                confidence=origin.confidence,
                evidence=f"normalized from '{raw_municipality}'",
            )

    energy = bundle.value("energy_class")
    if energy:
        canonical = normalize_energy_class(str(energy))
        if canonical:
            origin = bundle.fields["energy_class"]
            bundle.fields["energy_class"] = type(origin)(
                value=canonical,
                method=origin.method,
                confidence=origin.confidence,
                evidence=origin.evidence,
            )
        else:
            bundle.fields.pop("energy_class", None)

    if url:
        bundle.set("url", url, method="metadata", confidence=0.95, evidence="page URL")
        listing_id, conf, ev = text_extract.extract_listing_id(url, id_patterns)
        if listing_id:
            bundle.set("source_listing_id", listing_id, method="metadata", confidence=conf, evidence=ev)

    price, surface = bundle.value("price"), bundle.value("surface_m2")
    if price and surface and not bundle.value("price_m2"):
        bundle.set(
            "price_m2",
            round(float(price) / float(surface), 2),
            method="derived",
            confidence=min(bundle.confidence("price") or 0.5, bundle.confidence("surface_m2") or 0.5),
            evidence="price / surface_m2",
        )


def extract_listing(
    html: str,
    *,
    url: str = "",
    id_patterns: Sequence[str] = (),
    url_patterns: Sequence[str] = (),
    include_visible_text: bool = True,
    discover_links: bool = False,
) -> PageExtraction:
    """Run the deterministic ladder over one page and report what it produced.

    Visible-text patterns only fill fields the structured passes left **empty**,
    so a JSON-LD price is never overwritten by a regex that matched a related
    listing in a sidebar.
    """
    extraction = PageExtraction(url=url)
    bundle = extraction.bundle

    nodes = json_ld_nodes(html)
    best = _best_listing_node(nodes)
    if best is not None:
        _apply_schema_org(best, bundle)
    if bundle.value("price") is None:
        if extract_embedded_state(html, bundle):
            extraction.notes.append("embedded JSON state used")
    extract_microdata(html, bundle)
    extract_semantic_html(html, bundle)
    extract_metadata(html, bundle)

    text = visible_text(html) if include_visible_text else ""
    extraction.raw_text_chars = len(text)
    if text:
        text_bundle = text_extract.scrape_fields(text, method="regex_text")
        for name, field_value in text_bundle.fields.items():
            if name in bundle.fields:
                continue  # deterministic structured passes win
            bundle.set(
                name,
                field_value.value,
                method="regex_text",
                confidence=field_value.confidence,
                evidence=field_value.evidence,
            )

    if url or id_patterns:
        finalize_bundle(bundle, url=url, id_patterns=id_patterns)
    else:
        finalize_bundle(bundle)

    if discover_links:
        extraction.links = discover_listing_urls(html, base_url=url, url_patterns=url_patterns)

    extraction.method = _dominant_method(bundle)
    extraction.title = str(bundle.value("title") or "")
    if len(html or "") > 20_000 and extraction.raw_text_chars < 500:
        extraction.notes.append(
            "page looks client-rendered: little visible text but a large document"
        )
    return extraction


def _dominant_method(bundle: ExtractionBundle) -> str:
    """The most trusted method that produced actual listing content.

    Bookkeeping fields (the page URL and the derived listing id) are ignored:
    they are always present, and counting them would make an empty page look as
    though it had been extracted successfully.
    """
    bookkeeping = {"url", "source_listing_id"}
    methods = {
        field.method for name, field in bundle.fields.items() if name not in bookkeeping
    }
    if not methods:
        return ""
    return min(methods, key=method_rank)
