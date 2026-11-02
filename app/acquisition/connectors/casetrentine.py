"""CaseTrentine.it connector (pure HTTP): listing + detail HTML parsing.

List pages (``/case-in-vendita?offset=N``, 20 items each) link the detail
pages ``/case-in-vendita/view/{id}/{slug}``. Detail pages carry a ``<dl>``
with Codice (agency reference), Mq, Locali, Camere, Bagni, plus
``<p class="price">``, ``<p class="location">`` and the description block.

Only residential sale is in scope: magazzini/box/garage/negozi/terreni are
excluded by category keyword. Auction/quota/bare-ownership/time-share are
marked in ``offer_kind``. Missing values stay ``None`` (a "-" in Camere is
NOT a zero). Pagination stops when a page adds no new IDs (the navigation
bar may not list every page).
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime

import httpx

from app.acquisition.ledger import RETRYABLE_OUTCOMES, TERMINAL_OUTCOMES, RunLedger, StatusLedger
from app.acquisition.listings import Listing, ListingStore, normalize_comune
from app.core.config import PROJECT_ROOT, get_settings

CT_BASE = "https://www.casetrentine.it"
CT_LIST = f"{CT_BASE}/case-in-vendita"
#: Title prefixes that keep an ad inside the residential perimeter.
CT_RESIDENTIAL = ("appartamento", "casa", "villetta", "attico", "mansarda",
                  "porzione", "bilocale", "trilocale", "quadrilocale",
                  "monolocale", "loft", "rustico", "villa")
CT_EXCLUDED = ("magazzino", "box", "garage", "negozio", "ufficio", "terreno",
               "capannone", "laboratorio", "cantina", "soffitta")
OFFER_KIND_PATTERNS = (
    ("nuda_proprieta", r"nuda proprietà|nuda proprieta"),
    ("quota", r"\bquota\b|quota di"),
    ("multiproprieta", r"multiproprietà|multiproprieta"),
    ("asta", r"\basta\b|procedura giudiziaria|tribunale"),
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9",
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _dl_value(text: str, key: str) -> str:
    m = re.search(rf"<dt>\s*{re.escape(key)}\s*</dt>\s*<dd>\s*([^<]*)", text)
    return m.group(1).strip() if m else ""


def _it_price(raw: str) -> float | None:
    m = re.search(r"([\d.]+),?(\d{2})?", raw or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(".", "") + ("." + m.group(2) if m.group(2) else ""))
    except ValueError:
        return None


def _category_of(title: str) -> tuple[str, str]:
    low = title.lower()
    for kw in CT_EXCLUDED:
        if kw in low:
            return kw, ""
    for kw in CT_RESIDENTIAL:
        if kw in low:
            return kw, "residenziale"
    return "altro", ""


def _offer_kind(text: str) -> str:
    low = text.lower()
    for kind, pattern in OFFER_KIND_PATTERNS:
        if re.search(pattern, low):
            return kind
    return ""



def parse_casetrentine_detail(html: str, *, url: str, source: str, run_id: str) -> Listing | None:
    """Parse one CaseTrentine detail page. None when not a residential sale."""
    title_m = re.search(r"<title>([^<]+)</title>", html)
    title = title_m.group(1).strip() if title_m else ""
    if "in vendita" not in title.lower():
        return None
    id_m = re.search(r"ID\s+(\d+)", title)
    native_id = id_m.group(1) if id_m else (url.rstrip("/").rsplit("/", 1)[-1].rsplit("-", 1)[-1])
    price_m = re.search(r'<p class="price">\s*(?:&euro;|€)\s*([\d.,]+)', html)
    price_raw = price_m.group(1) if price_m else ""
    price = _it_price(price_raw) if price_raw else None
    location_m = re.search(r'<p class="location">([^<]+)</p>', html)
    comune = location_m.group(1).strip() if location_m else ""
    mq_raw = _dl_value(html, "Mq")
    mq = re.search(r"(\d+)", mq_raw)
    surface = float(mq.group(1)) if mq else None
    rooms_raw = _dl_value(html, "Locali")
    rm = re.search(r"(\d+)", rooms_raw)
    rooms = int(rm.group(1)) if rm else None
    bed_raw = _dl_value(html, "Camere")
    bm = re.search(r"(\d+)", bed_raw)
    bedrooms = int(bm.group(1)) if bm else None
    bath_raw = _dl_value(html, "Bagni")
    bhm = re.search(r"(\d+)", bath_raw)
    bathrooms = int(bhm.group(1)) if bhm else None
    ref = _dl_value(html, "Codice")
    desc_m = re.search(r'<div class="box_description">\s*<p>(.*?)</p>', html, re.S)
    description = re.sub(r"<[^>]+>", " ", desc_m.group(1) if desc_m else "")[:2000].strip()
    energy_m = re.search(r"Classe energetica:\s*([A-G]\d?)", description)
    category, unit_class = _category_of(title)
    agency_m = re.search(r"([A-Z][A-Z0-9 .]{2,40}?)\s+(?:propone|presenta|offre)", description)
    advertiser = (agency_m.group(1).strip() if agency_m else "") + (f" — rif. {ref}" if ref else "")
    return Listing(
        source=source,
        native_id=native_id,
        url=url,
        title=title[:300],
        typology=category.lower(),
        contract="vendita",
        unit_class=unit_class,
        offer_kind=_offer_kind(title + " " + description),
        advertiser=advertiser,
        price=price,
        currency="EUR",
        price_qualifier="su richiesta" if price is None and "su richiesta" in html.lower() else "",
        surface_m2=surface,
        surface_definition="non_specificata",
        comune=normalize_comune(comune) if comune else "",
        province="TN" if comune else "",
        rooms=rooms,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        energy_class=energy_m.group(1) if energy_m else "",
        description=re.sub(r"[\u2028\u2029\x0b\x0c\r]+", " ", description),
        content_hash="ct-" + native_id,
        run_id=run_id,
        parser_version="casetrentine-0.1.0",
    )


def list_page_urls(client: httpx.Client, offset: int) -> list[str]:
    url = CT_LIST if offset == 0 else f"{CT_LIST}?order=data_pubblicazione+desc&offset={offset}"
    resp = client.get(url)
    resp.raise_for_status()
    return sorted(set(re.findall(r'href="(https://www\.casetrentine\.it/case-in-vendita/view/\d+/[^"]+)"', resp.text)))



def run_casetrentine_pilot(*, store_path=None, max_listings: int | None = None,
                           source: str = "casetrentine_it") -> dict:
    """Discover and ingest CaseTrentine residential listings (pure HTTP)."""
    settings = get_settings()
    store = ListingStore(store_path or PROJECT_ROOT / "data" / "listings.jsonl")
    runs = RunLedger()
    status = StatusLedger()
    run_id = "ct-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = time.monotonic()
    max_seconds = float(settings.crawl_max_seconds)
    delay = max(1.5, float(settings.default_min_delay_s))
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

    urls: list[str] = []
    seen_ids: set[str] = set()
    with httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS) as client:
        offset = 0
        while offset <= 400:
            if (time.monotonic() - started) >= max_seconds * 0.5:
                break
            try:
                page_urls = list_page_urls(client, offset)
            except httpx.HTTPError as exc:
                report["errors"] += 1
                _note("error", f"{CT_LIST}?offset={offset}", str(exc)[:180])
                break
            new = []
            for u in page_urls:
                m = re.search(r"/view/(\d+)/", u)
                if m and m.group(1) not in seen_ids:
                    seen_ids.add(m.group(1))
                    new.append(u)
            if not new:
                break
            urls.extend(new)
            offset += 20
            time.sleep(delay)

    limit = max_listings if max_listings is not None else len(urls)
    report["discovered"] = len(urls)
    report["discovered_urls"] = urls[:limit]
    with httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS) as client:
        for url in urls[:limit]:
            if (time.monotonic() - started) >= max_seconds:
                break
            report["attempted"] += 1
            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                report["errors"] += 1
                _note("error", url, str(exc)[:180])
                time.sleep(delay)
                continue
            if resp.status_code != 200:
                report["errors"] += 1
                _note(f"http_{resp.status_code}", url)
                time.sleep(delay)
                continue
            listing = parse_casetrentine_detail(resp.text, url=url, source=source, run_id=run_id)
            if listing is None:
                _note("excluded_non_residential_or_rental", url)
                continue
            if listing.unit_class != "residenziale":
                key = listing.typology or "non_residenziale"
                report["excluded_offers"][key] = report["excluded_offers"].get(key, 0) + 1
                _note("excluded_non_residential", url, key)
                time.sleep(delay)
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
            time.sleep(delay)

    attempted = report["attempted"]
    deferred = max(0, min(limit, len(urls)) - attempted)
    retryable = sum(1 for m in report["manifest"] if m["outcome"] in RETRYABLE_OUTCOMES)
    terminal = sum(1 for m in report["manifest"] if m["outcome"] in TERMINAL_OUTCOMES)
    report["queue"] = {
        "pending": max(0, min(limit, len(urls)) - len(report["manifest"])),
        "retryable_errors": retryable,
        "budget_deferred": deferred,
        "terminal": terminal,
    }
    report["complete"] = bool(deferred == 0 and len(urls) <= limit)
    report["status"] = "completed" if report["complete"] else "partial"
    report["current"] = len(store.current())
    report["duration_seconds"] = round(time.monotonic() - started, 2)
    report.pop("manifest")
    runs.record(report)
    return report
