"""Subito.it connector (pure HTTP): search pages embed the listing data.

Subito renders the ad list inside ``__NEXT_DATA__`` (Next.js hydration JSON):
``props.pageProps.initialState.items`` carries ``total``, ``totalPages`` and
``originalList`` (30 items per page) with urn, subject, body, features
(price/size/rooms/bathrooms/energy class), geo (region/city=province/town=
comune with ISTAT code) and advertiser. Pagination is ``?o=<page>`` (0-based
page number). Detail pages (``.htm``) embed JSON-LD as a fallback.

Province filter: only towns whose ISTAT code starts with ``022`` (Trento);
the region label "Trentino-Alto Adige" alone would wrongly include Bolzano
(021). Auction/quota/bare-ownership/time-share offers are marked in
``offer_kind`` and excluded from the standard €/m² statistics, never guessed
from a low price. A monthly instalment is never turned into the total price.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime

import httpx

from app.acquisition.ledger import RETRYABLE_OUTCOMES, TERMINAL_OUTCOMES, RunLedger, StatusLedger
from app.acquisition.listings import Listing, ListingStore, normalize_comune
from app.core.config import PROJECT_ROOT, get_settings

SUBTTO_BASE = "https://www.subito.it"
#: Categories in scope (residential sale); loft/mansarde needs content filtering.
SUBTTO_CATEGORIES = (
    ("appartamenti", "https://www.subito.it/annunci-trentino-alto-adige/vendita/appartamenti/trento/"),
    ("ville-singole-e-a-schiera", "https://www.subito.it/annunci-trentino-alto-adige/vendita/ville-singole-e-a-schiera/trento/"),
    ("loft-mansarde", "https://www.subito.it/annunci-trentino-alto-adige/vendita/loft-mansarde/trento/"),
)
#: Markers for offers outside standard full-ownership residential stats.
OFFER_KIND_PATTERNS = (
    ("nuda_proprieta", r"nuda proprietà|nuda proprieta"),
    ("quota", r"\bquota\b|quota di"),
    ("multiproprieta", r"multiproprietà|multiproprieta"),
    ("asta", r"\basta\b|procedura giudiziaria|tribunale"),
    ("leasing", r"\bleasing\b"),
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "it-IT,it;q=0.9",
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _feat_value(features: dict, uri: str) -> str:
    feat = (features or {}).get(uri) or {}
    values = feat.get("values") or []
    return str(values[0].get("value") or "") if values else ""


def _price_of(item: dict) -> float | None:
    raw = _feat_value(item.get("features"), "/price")
    text = f"{item.get('subject','')} {item.get('body','')}".lower()
    if "su richiesta" in text or "prezzo non indicato" in text:
        return None
    m = re.search(r"(\d[\d.]*)\s*€", raw)
    if not m:
        # body fallback: "PREZZO: 166.000" style
        m = re.search(r"prezzo[: ]+\s*(\d[\d.]*)", text)
        if not m:
            return None
    return float(m.group(1).replace(".", ""))


def _offer_kind(subject: str, body: str) -> str:
    text = f"{subject} {body}".lower()
    for kind, pattern in OFFER_KIND_PATTERNS:
        if re.search(pattern, text):
            return kind
    return ""


def _unit_class_for(label: str) -> str:
    lab = (label or "").lower()
    if lab in {"appartamenti", "ville singole e a schiera", "loft e mansarde", "mansarde", "case indipendenti"}:
        return "residenziale"
    if lab in {"box e posti auto", "garage"}:
        return "box"
    if lab in {"terreni"}:
        return "terreno"
    return ""



def parse_subito_item(item: dict, *, source: str, run_id: str) -> Listing | None:
    """Build a Listing from one __NEXT_DATA__ item. None when not usable."""
    urn = item.get("urn") or ""
    m = re.search(r"list:(\d+)$", urn)
    native_id = m.group(1) if m else urn.replace(":", "-")
    urls = item.get("urls") or {}
    url = urls.get("default") or urls.get("mobile") or ""
    category = (item.get("category") or {}).get("label") or ""
    geo = item.get("geo") or {}
    town = geo.get("town") or {}
    istat = str(town.get("istat") or "")
    if not istat.startswith("022"):
        return None  # outside Trento province (022), e.g. Bolzano 021
    comune = str(town.get("value") or "")
    features = item.get("features") or {}
    surface_raw = _feat_value(features, "/size")
    sm = re.search(r"(\d+)", surface_raw)
    surface = float(sm.group(1)) if sm else None
    rooms_raw = _feat_value(features, "/room")
    rm = re.search(r"(\d+)", rooms_raw)
    rooms = int(rm.group(1)) if rm else None
    bathrooms_raw = _feat_value(features, "/bathrooms")
    bm = re.search(r"(\d+)", bathrooms_raw)
    bathrooms = int(bm.group(1)) if bm else None
    advertiser = item.get("advertiser") or {}
    title = str(item.get("subject") or "")[:300]
    body = str(item.get("body") or "")
    price = _price_of(item)
    return Listing(
        source=source,
        native_id=native_id,
        url=url or f"{SUBTTO_BASE}/404-{native_id}.htm",
        title=title,
        typology=category.lower(),
        contract="vendita" if (item.get("type") or {}).get("key") == "s" else "vendita",
        unit_class=_unit_class_for(category),
        offer_kind=_offer_kind(title, body),
        advertiser=str(advertiser.get("shopName") or advertiser.get("name") or ""),
        price=price,
        currency="EUR",
        price_qualifier="su richiesta" if price is None and "su richiesta" in f"{title} {body}".lower() else "",
        surface_m2=surface,
        surface_definition="non_specificata",
        comune=normalize_comune(comune) if comune else "",
        province="TN" if comune else "",
        rooms=rooms,
        bathrooms=bathrooms,
        energy_class=_feat_value(features, "/energy_class"),
        published_at=str(item.get("date") or ""),
        description=re.sub(r"\s+", " ", body)[:2000].strip(),
        content_hash="subito-" + native_id,
        run_id=run_id,
        parser_version="subito-0.1.0",
    )


def fetch_list_page(client: httpx.Client, url: str) -> dict:
    resp = client.get(url)
    resp.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.S)
    if not m:
        return {"items": [], "total": 0, "totalPages": 0, "error": "no __NEXT_DATA__"}
    data = json.loads(m.group(1))
    try:
        items = data["props"]["pageProps"]["initialState"]["items"]
    except (KeyError, TypeError):
        return {"items": [], "total": 0, "totalPages": 0, "error": "unexpected structure"}
    raw = list(items.get("originalList") or [])
    raw += [x for x in (items.get("galleryList") or [])]
    seen: set[str] = set()
    out = []
    for it in raw:
        urn = it.get("urn") or ""
        if urn in seen:
            continue
        seen.add(urn)
        out.append(it)
    return {"items": out, "total": int(items.get("total") or 0),
            "totalPages": int(items.get("totalPages") or 0)}



def run_subito_pilot(*, store_path=None, max_listings: int | None = None,
                     source: str = "subito_it", categories: tuple | None = None) -> dict:
    """Discover and ingest Subito residential listings (pure HTTP).

    Listing data is embedded in each search page's ``__NEXT_DATA__``: the run
    walks the category pagination, collects the items and parses them locally
    (no per-listing HTTP request). Budget and ledger conventions match the
    other connectors.
    """
    settings = get_settings()
    store = ListingStore(store_path or PROJECT_ROOT / "data" / "listings.jsonl")
    runs = RunLedger()
    status = StatusLedger()
    run_id = "sub-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = time.monotonic()
    max_seconds = float(settings.crawl_max_seconds)
    delay = max(1.5, float(settings.default_min_delay_s))
    categories = categories or SUBTTO_CATEGORIES
    report = {
        "run_id": run_id, "source": source, "method": "http", "status": "partial",
        "discovered": 0, "attempted": 0, "records": 0, "stored": 0, "revisits": 0,
        "price_changes": 0, "errors": 0, "outcomes": {},
        "queue": {"pending": 0, "retryable_errors": 0, "budget_deferred": 0, "terminal": 0},
        "excluded_offers": {}, "complete": False, "discovered_urls": [], "manifest": [],
    }

    def _note(outcome: str, url: str, detail: str = "") -> None:
        report["outcomes"][outcome] = report["outcomes"].get(outcome, 0) + 1
        status.record(run_id=run_id, source=source, url=url, outcome=outcome, detail=detail[:180])
        report["manifest"].append({"url": url, "outcome": outcome})

    items: list[dict] = []
    seen_pages: set[str] = set()
    with httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS) as client:
        for _cat, base in categories:
            page = 0
            while page < 1000:
                if (time.monotonic() - started) >= max_seconds * 0.5:
                    break
                page_url = base if page == 0 else f"{base}?o={page}"
                if page_url in seen_pages:
                    break
                seen_pages.add(page_url)
                try:
                    data = fetch_list_page(client, page_url)
                except httpx.HTTPError as exc:
                    report["errors"] += 1
                    _note("error", page_url, str(exc)[:180])
                    break
                if data.get("error"):
                    report["errors"] += 1
                    _note("no_data", page_url, data["error"])
                    break
                page_items = data.get("items") or []
                if not page_items:
                    break
                items.extend(page_items)
                total_pages = data.get("totalPages") or 0
                if page >= total_pages - 1:
                    break
                page += 1
                time.sleep(delay)

    limit = max_listings if max_listings is not None else len(items)
    report["discovered"] = len(items)
    report["discovered_urls"] = [((i.get("urls") or {}).get("default") or "") for i in items[:limit]]
    for item in items[:limit]:
        report["attempted"] += 1
        url = (item.get("urls") or {}).get("default") or ""
        listing = parse_subito_item(item, source=source, run_id=run_id)
        if listing is None:
            _note("excluded_out_of_province", url, "istat non 022")
            continue
        if listing.unit_class != "residenziale":
            key = listing.unit_class or "non_residenziale"
            report["excluded_offers"][key] = report["excluded_offers"].get(key, 0) + 1
            _note("excluded_non_residential", url, key)
            continue
        if listing.offer_kind:
            report["excluded_offers"][listing.offer_kind] = report["excluded_offers"].get(listing.offer_kind, 0) + 1
        result = store.observe(listing)
        report["records"] += 1
        report["stored"] += 1 if result.get("stored") else 0
        report["revisits"] += 1 if result.get("revisit") else 0
        report["price_changes"] += 1 if result.get("price_changed") else 0
        outcome = "revisit" if result.get("revisit") else ("valid_new" if result.get("new") else "valid_updated")
        _note(outcome, url)

    attempted = report["attempted"]
    deferred = max(0, min(limit, len(items)) - attempted)
    retryable = sum(1 for m in report["manifest"] if m["outcome"] in RETRYABLE_OUTCOMES)
    terminal = sum(1 for m in report["manifest"] if m["outcome"] in TERMINAL_OUTCOMES)
    report["queue"] = {
        "pending": max(0, min(limit, len(items)) - len(report["manifest"])),
        "retryable_errors": retryable,
        "budget_deferred": deferred,
        "terminal": terminal,
    }
    report["complete"] = bool(deferred == 0 and len(items) <= limit)
    report["status"] = "completed" if report["complete"] else "partial"
    report["current"] = len(store.current())
    report["duration_seconds"] = round(time.monotonic() - started, 2)
    report.pop("manifest")
    runs.record(report)
    return report
