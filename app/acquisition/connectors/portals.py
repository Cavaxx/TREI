"""Kernel browser connectors for the three authorized portals.

Authorization: ``config/portal_authorization_2026-10-31.txt`` — written
consent of idealista.it, subito.it and immobiliare.it for automated access
with Kernel stealth mode, scoped to this non-commercial project.

Rules applied here regardless of the authorization:
* preset filters only: provincia di Trento, vendita, residenziale;
* public pages only — never login, never user areas, no CAPTCHA hand-solving;
* stealth is passed explicitly per-session (the provider default stays off);
* every session is released in ``finally`` and accounted against the run
  budget (``TREI_KERNEL_MAX_*``); a per-source cap
  (``TREI_PORTAL_MAX_LISTINGS``) bounds the records;
* extraction is evidence-based (JSON-LD > meta > text); a page without any
  usable field is reported as ``no_data`` and never fabricated.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime

from app.acquisition.ledger import RETRYABLE_OUTCOMES, TERMINAL_OUTCOMES, RunLedger, StatusLedger
from app.acquisition.listings import Listing, ListingStore, normalize_comune
from app.browser.errors import BrowserFailure, BrowserProviderError
from app.browser.provider import KernelBrowserProvider
from app.core.config import PROJECT_ROOT, get_settings

#: Preset filters: provincia di Trento, vendita, residenziale.
PORTAL_SEARCH_URLS = {
    "idealista_it": [
        "https://www.idealista.it/vendita-case/trento-provincia/",
    ],
    "subito_it": [
        "https://www.subito.it/annunci-italia/vendita/immobili/trentino/",
    ],
    "immobiliare_it": [
        "https://www.immobiliare.it/vendita-case/trento-provincia/",
    ],
}

#: Anchor patterns that identify detail pages on each portal.
PORTAL_DETAIL_HINTS = {
    "idealista_it": ["/immobile/"],
    "subito_it": ["/vendita/", "/annunci/"],
    "immobiliare_it": ["/annunci/"],
}

SEARCH_JS = """
await page.goto(SEARCH_URL, {waitUntil: 'networkidle', timeout: 45000}).catch(() => {});
await page.waitForTimeout(4000);
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => {});
await page.waitForTimeout(1500);
const info = await page.evaluate((hints) => {
  const out = [];
  const all = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.getAttribute('href') || '';
    if (all.length < 80) all.push(h);
    if (hints.some(x => h.includes(x))) out.push(h);
  }
  return {links: [...new Set(out)], samples: all, title: document.title,
          textlen: document.body ? document.body.innerText.length : 0};
}, HINTS);
return {url: page.url(), ...info};
"""

DETAIL_JS = """
await page.goto(DETAIL_URL, {waitUntil: 'networkidle', timeout: 45000}).catch(() => {});
await page.waitForTimeout(3000);
return await page.evaluate(() => {
  const jl = [];
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { jl.push(JSON.parse(s.textContent)); } catch (e) {}
  }
  const meta = {};
  for (const n of document.querySelectorAll('meta')) {
    const k = n.getAttribute('property') || n.getAttribute('name');
    if (k) meta[k] = n.getAttribute('content') || '';
  }
  return {jsonld: jl, meta, title: document.title,
          text: (document.body ? document.body.innerText : '').slice(0, 6000)};
});
"""


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _first_num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None



def parse_portal_detail(payload: dict, *, url: str, source: str, run_id: str) -> Listing | None:
    """Parse a portal detail payload (JSON-LD + meta + text) into a Listing.

    Returns ``None`` when no usable field was found (never fabricates).
    """
    meta = payload.get("meta") or {}
    text = payload.get("text") or ""
    title = payload.get("title") or ""

    price = None
    currency = "EUR"
    comune = ""
    address = ""
    surface = None
    rooms = None
    bathrooms = None
    bedrooms = None
    typology = ""
    for node in payload.get("jsonld") or []:
        if not isinstance(node, dict):
            continue
        gtype = str(node.get("@type") or "")
        if gtype in {"Product", "RealEstateListing", "Residence", "Apartment", "House", "Offer"}:
            title = str(node.get("name") or title)
            typology = str(node.get("category") or node.get("additionalType") or typology)
            addr = node.get("address") or node.get("location") or {}
            if isinstance(addr, dict):
                comune = str(addr.get("addressLocality") or addr.get("addressRegion") or comune)
                address = str(addr.get("streetAddress") or "")
            offers = node.get("offers")
            if isinstance(offers, dict):
                price = _first_num(r"([\d.,]+)", str(offers.get("price") or ""))
                currency = str(offers.get("priceCurrency") or "EUR")
            floorsize = node.get("floorSize") or {}
            if isinstance(floorsize, dict):
                surface = _first_num(r"([\d.,]+)", str(floorsize.get("value") or ""))
            rooms = _first_num(r"(\d+)", str(node.get("numberOfRooms") or ""))
            if rooms is not None:
                rooms = int(rooms)

    og = meta.get("og:description") or meta.get("description") or ""
    title = title or meta.get("og:title") or ""
    if not price:
        price = _first_num(r"(\d[\d.,]*)\s*€", og or text)
    if surface is None:
        m = re.search(r"(\d+)\s*m[²2]", text or og)
        surface = float(m.group(1)) if m else None
    if rooms is None:
        m = re.search(r"(\d+)\s*(?:locali|vani)", text or og)
        rooms = int(m.group(1)) if m else None
    if bedrooms is None:
        m = re.search(r"(\d+)\s*(?:camere|stanze da letto)", text or og)
        bedrooms = int(m.group(1)) if m else None
    if bathrooms is None:
        m = re.search(r"(\d+)\s*(?:bagni|servizi)", text or og)
        bathrooms = int(m.group(1)) if m else None
    if not comune:
        m = re.search(r"vendita a ([A-ZÀ-Ù][^\d€,()\n]{2,60})", text or og)
        comune = m.group(1).strip() if m else ""
    native_id = re.search(r"(\d{6,})", url)
    #: a title alone is not evidence of a usable listing
    if not (price or surface or comune):
        return None
    return Listing(
        source=source,
        native_id=native_id.group(1) if native_id else url.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        title=title[:300],
        typology=typology.lower() or "",
        contract="vendita",
        unit_class="residenziale",
        price=price,
        currency=currency,
        surface_m2=surface,
        surface_definition="non_specificata",
        comune=normalize_comune(comune) if comune else "",
        province="TN" if comune else "",
        address=address[:200],
        rooms=rooms,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        content_hash="portal-" + url,
        run_id=run_id,
        parser_version="portal-0.1.0",
    )



def run_portal_pilot(*, source: str, max_listings: int | None = None,
                     store_path=None, stealth: bool = True, headless: bool = False) -> dict:
    """Run one authorized portal with Kernel (stealth per authorization).

    One session per run, released in ``finally``; the per-source cap and the
    run budget bound the work. Preset filters: Trento, vendita, residenziale.
    """
    from app.core.envfile import load_env_file

    load_env_file()
    settings = get_settings()
    cap = max_listings if max_listings is not None else settings.portal_max_listings
    store = ListingStore(store_path or PROJECT_ROOT / "data" / "listings.jsonl")
    runs = RunLedger()
    status = StatusLedger()
    run_id = f"{source}-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = time.monotonic()
    report = {
        "run_id": run_id, "source": source, "method": "kernel_browser", "status": "partial",
        "stealth": stealth, "headless": headless,
        "authorization": "config/portal_authorization_2026-10-31.txt",
        "discovered": 0, "attempted": 0, "records": 0, "stored": 0, "revisits": 0,
        "price_changes": 0, "errors": 0, "outcomes": {},
        "queue": {"pending": 0, "retryable_errors": 0, "budget_deferred": 0, "terminal": 0},
        "complete": False, "discovered_urls": [], "manifest": [],
    }

    def _note(outcome: str, url: str, detail: str = "") -> None:
        report["outcomes"][outcome] = report["outcomes"].get(outcome, 0) + 1
        status.record(run_id=run_id, source=source, url=url, outcome=outcome, detail=detail)
        report["manifest"].append({"url": url, "outcome": outcome})

    provider = KernelBrowserProvider()
    session = None
    try:
        session = provider.open_session(name=f"portal-{source}", stealth=stealth, headless=headless)
        urls: list[str] = []
        for search_url in PORTAL_SEARCH_URLS.get(source, []):
            code = SEARCH_JS.replace("SEARCH_URL", json.dumps(search_url)).replace(
                "HINTS", json.dumps(PORTAL_DETAIL_HINTS.get(source, [])))
            res = provider.execute(session, code)
            if not res.success:
                raise BrowserProviderError(BrowserFailure.FORMAT_CHANGED,
                                           f"{source} search failed: {res.error or res.stderr}")
            data = res.result or {}
            report.setdefault("search_debug", []).append({
                "search_url": search_url,
                "final_url": data.get("url"),
                "title": data.get("title"),
                "textlen": data.get("textlen"),
                "samples": (data.get("samples") or [])[:12],
            })
            for link in data.get("links") or []:
                if link.startswith("http"):
                    urls.append(link)
        urls = list(dict.fromkeys(urls))
        report["discovered"] = len(urls)
        report["discovered_urls"] = urls[:cap]
        for url in urls[:cap]:
            report["attempted"] += 1
            code = DETAIL_JS.replace("DETAIL_URL", json.dumps(url))
            res = provider.execute(session, code)
            time.sleep(settings.browser_min_navigation_interval_seconds)
            if not res.success:
                report["errors"] += 1
                _note("error", url, (res.error or res.stderr)[:200])
                continue
            listing = parse_portal_detail(res.result or {}, url=url, source=source, run_id=run_id)
            if listing is None:
                report["errors"] += 1
                _note("no_data", url, "no usable field (JSON-LD/meta/text)")
                continue
            result = store.observe(listing)
            report["records"] += 1
            report["stored"] += 1 if result.get("stored") else 0
            report["revisits"] += 1 if result.get("revisit") else 0
            report["price_changes"] += 1 if result.get("price_changed") else 0
            outcome = "revisit" if result.get("revisit") else ("valid_new" if result.get("new") else "valid_updated")
            _note(outcome, url)
        deferred = max(0, len(urls) - cap)
        retryable = sum(1 for m in report["manifest"] if m["outcome"] in RETRYABLE_OUTCOMES)
        terminal = sum(1 for m in report["manifest"] if m["outcome"] in TERMINAL_OUTCOMES)
        report["queue"] = {
            "pending": max(0, min(cap, len(urls)) - report["attempted"]),
            "retryable_errors": retryable,
            "budget_deferred": deferred,
            "terminal": terminal,
        }
        report["complete"] = bool(deferred == 0)
        report["status"] = "completed" if report["complete"] else "partial"
    except BrowserProviderError as exc:
        report["status"] = "failed"
        report["errors"] += 1
        report["error"] = f"{exc.failure}: {exc}"
        if getattr(exc, "detail", ""):
            report["session_recovery"] = str(exc.detail)
    except Exception as exc:  # noqa: BLE001
        report["status"] = "failed"
        report["error"] = str(exc)
    finally:
        if session is not None:
            try:
                session.close()
            except BrowserProviderError as exc:
                report["error"] = (report.get("error") or "") + f" cleanup: {exc}"
        report["current"] = len(store.current())
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        report.pop("manifest")
        runs.record(report)
    return report
