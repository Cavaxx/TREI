"""Listing acquisition: contract, store and the Tecnocasa connector.

Tecnocasa is a permitted, deterministic HTTP source: robots.txt allows crawling
and publishes residential sitemaps; each detail page embeds a structured JSON
object in a ``<estate-show-v1 :estate="{...}">`` Web Component. No browser needed.

A *listing* is a snapshot observed at a point in time; repeated observations of
the same native id are kept (price/state history is never overwritten).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import httpx

LISTING_PARSER_VERSION = "tecnocasa-0.1.1"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass
class Listing:
    source: str
    native_id: str
    url: str = ""
    title: str = ""
    typology: str = ""
    contract: str = "vendita"
    price: float | None = None
    currency: str = "EUR"
    price_qualifier: str = ""
    previous_price: float | None = None
    discount: str = ""
    surface_m2: float | None = None
    surface_definition: str = "non_specificata"
    comune: str = ""
    province: str = ""
    zone: str = ""
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    floor: str = ""
    elevator: str = ""
    energy_class: str = ""
    published_at: str = ""
    observed_at: str = field(default_factory=_now_iso)
    first_seen_at: str = ""
    last_seen_at: str = ""
    description: str = ""
    raw_object_id: str = ""
    content_hash: str = ""
    parser_version: str = LISTING_PARSER_VERSION
    run_id: str = ""

    def key(self) -> str:
        return f"{self.source}:{self.native_id}"

    def to_dict(self) -> dict:
        return asdict(self)


class ListingStore:
    """JSONL store of listing observations; current state = latest per key.

    Idempotent: re-observing the same (source, native_id) with the same price and
    content is a no-op except refreshing ``last_seen_at``; a price change appends
    a new observation instead of overwriting history."""

    def __init__(self, path) -> None:
        from pathlib import Path

        self.path = Path(path)
        self._records: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                self._records.append(json.loads(line))

    def observe(self, listing: Listing) -> dict:
        rec = listing.to_dict()
        key = listing.key()
        prior = [r for r in self._records if r.get("key") == key]
        rec["key"] = key
        if prior:
            last = prior[-1]
            if last.get("price") == rec.get("price") and last.get("content_hash") == rec.get("content_hash") and last.get("parser_version") == rec.get("parser_version"):
                last["last_seen_at"] = rec["observed_at"]
                self._rewrite()
                return {"stored": False, "new": False, "price_changed": False, "key": key}
            rec["first_seen_at"] = prior[0].get("first_seen_at") or prior[0].get("observed_at")
        else:
            rec["first_seen_at"] = rec["observed_at"]
        rec["last_seen_at"] = rec["observed_at"]
        self._records.append(rec)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        price_changed = bool(prior and prior[-1].get("price") != rec.get("price"))
        return {"stored": True, "new": not prior, "price_changed": price_changed, "key": key}

    def _rewrite(self) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            for rec in self._records:
                handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def all(self) -> list[dict]:
        return list(self._records)

    def current(self) -> list[dict]:
        """Latest observation per listing key."""
        latest: dict[str, dict] = {}
        for rec in self._records:
            latest[rec.get("key", "")] = rec
        return list(latest.values())

    def history(self, native_id: str) -> list[dict]:
        return [r for r in self._records if r.get("native_id") == native_id]


def _int_of(text: str) -> int | None:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _euro_of(text: str) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d[\d.]*)", (text or "").replace(".", "").replace(",", "."))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _characteristic(data: list | None, name: str) -> str:
    for item in data or []:
        if isinstance(item, dict) and (item.get("label") or item.get("nome") or "").lower().startswith(name.lower()):
            return str(item.get("valore") or "")
    return ""


def parse_tecnocasa_detail(html_text: str, *, url: str, source: str, run_id: str) -> Listing | None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    tag = soup.find("estate-show-v1")
    if tag is None:
        return None
    raw = tag.get(":estate")
    if not raw:
        return None
    try:
        data = json.loads(html.unescape(raw))
    except (json.JSONDecodeError, TypeError):
        return None

    surface = None
    if data.get("numeric_surface") not in (None, ""):
        try:
            surface = float(str(data["numeric_surface"]).replace(",", "."))
        except ValueError:
            surface = None

    lat = data.get("latitude")
    lon = data.get("longitude")
    energy = ""
    ed = data.get("energy_data") or {}
    if isinstance(ed, dict):
        energy = ed.get("label") or ed.get("class") or ed.get("value") or ""
    feats = data.get("features") or {}
    if not isinstance(feats, dict):
        feats = {}
    floor = str(feats.get("floor") or _characteristic(data.get("data"), "Piano"))
    elevator = str(feats.get("elevator_immo") or _characteristic(data.get("data"), "Ascensore"))

    return Listing(
        source=source,
        native_id=str(data.get("id") or ""),
        url=data.get("detail_url") or url,
        title=data.get("title") or "",
        typology=(data.get("type") or {}).get("title") or "",
        contract=(data.get("contract") or {}).get("slug") or "vendita",
        price=(data.get("numeric_price") if isinstance(data.get("numeric_price"), (int, float)) else _euro_of(data.get("price"))),
        currency="EUR",
        price_qualifier="su richiesta" if data.get("private_negotiation") else "",
        previous_price=_euro_of(data.get("previous_price")),
        discount=str(data.get("discount") or ""),
        surface_m2=surface,
        surface_definition="non_specificata",
        comune=(data.get("city") or {}).get("title") or "",
        province=(data.get("province") or {}).get("id") or "",
        zone=(data.get("district") or {}).get("title") or "",
        address=data.get("address") or "",
        latitude=float(lat) if lat not in (None, "") else None,
        longitude=float(lon) if lon not in (None, "") else None,
        rooms=_int_of(data.get("rooms") or ""),
        bedrooms=_int_of(str(feats.get("bedrooms") or "")) or _int_of(_characteristic(data.get("data"), "Camere")),
        bathrooms=_int_of(data.get("bathrooms") or ""),
        floor=floor,
        elevator=elevator,
        energy_class=str(energy) if energy else "",
        published_at=data.get("last_published_at") or "",
        description=re.sub(r"<[^>]+>", " ", data.get("description") or "")[:2000].strip(),
        content_hash=sha256(html_text.encode()),
        run_id=run_id,
    )


def discover_tecnocasa_urls(*, province: str = "trento", max_urls: int = 200, timeout: float = 20.0) -> list[str]:
    """Discover residential-sale URLs from the Tecnocasa sitemaps."""
    import gzip

    urls: list[str] = []
    headers = {"User-Agent": "TREI-bot/0.1 (+https://example.org/trentino-rei; contact: data@example.org)"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for i in range(12):
            resp = client.get(f"https://www.tecnocasa.it/sitemap/residenziali-{i}.xml.gz")
            if resp.status_code != 200:
                continue
            try:
                text = gzip.decompress(resp.content).decode("utf-8", "replace")
            except OSError:
                text = resp.text
            for m in re.finditer(r"<loc>([^<]+)</loc>", text):
                loc = m.group(1)
                if f"/vendita/appartamenti/{province}/" in loc:
                    urls.append(loc)
                    if len(urls) >= max_urls:
                        return urls
    return urls

def run_listings_pilot(*, store_path=None, max_listings: int = 15, source: str = "tecnocasa_it") -> dict:
    """Discover and ingest a small, explicit sample of Tecnocasa Trento listings."""
    import time
    import uuid

    from app.core.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    store = ListingStore(store_path or PROJECT_ROOT / "data" / "listings.jsonl")
    run_id = "tc-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = time.monotonic()

    urls = discover_tecnocasa_urls(province="trento", max_urls=max_listings * 2)
    report = {
        "run_id": run_id,
        "source": source,
        "method": "http",
        "status": "completed",
        "discovered": len(urls),
        "records": 0,
        "stored": 0,
        "price_changes": 0,
        "errors": [],
    }

    headers = {"User-Agent": settings.user_agent}
    with httpx.Client(timeout=settings.request_timeout_s, follow_redirects=True, headers=headers) as client:
        for url in urls[:max_listings]:
            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                report["errors"].append({"url": url, "error": str(exc)})
                continue
            if resp.status_code != 200:
                report["errors"].append({"url": url, "error": f"HTTP {resp.status_code}"})
                continue
            listing = parse_tecnocasa_detail(resp.text, url=url, source=source, run_id=run_id)
            if listing is None:
                report["errors"].append({"url": url, "error": "no structured data"})
                continue
            result = store.observe(listing)
            report["records"] += 1
            report["stored"] += 1 if result.get("stored") else 0
            report["price_changes"] += 1 if result.get("price_changed") else 0
            time.sleep(1.0)

    report["current"] = len(store.current())
    report["duration_seconds"] = round(time.monotonic() - started, 2)
    return report


