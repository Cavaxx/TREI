"""Listing acquisition: contract, store and the Tecnocasa/Gabetti connectors.

Tecnocasa and Gabetti are permitted, deterministic HTTP sources (robots.txt
allows crawling and publishes sitemaps). Tecnocasa embeds a structured JSON in an
``<estate-show-v1 :estate="{...}">`` Web Component; Gabetti exposes the core fields
in its meta tags (og:title / description).

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

    def signature(self) -> tuple:
        """Stable tuple of the observable fields, used for deduplication.

        The raw HTML hash is NOT stable across runs (dynamic page content), so
        dedup must compare the parsed fields instead."""
        return (
            self.price,
            self.surface_m2,
            self.rooms,
            self.bedrooms,
            self.bathrooms,
            self.title,
            self.comune,
            self.typology,
            self.contract,
            self.energy_class,
            self.floor,
            self.elevator,
            self.latitude,
            self.longitude,
            self.price_qualifier,
            self.previous_price,
            self.discount,
        )

    def to_dict(self) -> dict:
        return asdict(self)


class ListingStore:
    """JSONL store of listing observations; current state = latest per key.

    Idempotent: re-observing the same (source, native_id) with the same price,
    content and parser version is a no-op except refreshing ``last_seen_at``; a
    price change appends a new observation instead of overwriting history."""

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

    def _content_records(self, key: str) -> list[dict]:
        """Content/state records for a key (a visit is NOT a content record)."""
        return [r for r in self._records if r.get("key") == key and r.get("content_changed", True)]

    def observe(self, listing: Listing) -> dict:
        """Record one visit.

        * a visit with unchanged content appends a ``content_changed=False`` record
          (the rilevazione is kept even though the state did not change);
        * a visit with new/changed content appends a ``content_changed=True`` record;
        * re-running the same run (same ``run_id``) is idempotent.
        """
        rec = listing.to_dict()
        key = listing.key()
        sig = list(listing.signature())
        visit_id = f"{listing.run_id}:{key}"
        rec["key"] = key
        rec["signature"] = sig
        rec["visit_id"] = visit_id

        if any(r.get("visit_id") == visit_id for r in self._records):
            return {"stored": False, "new": False, "price_changed": False, "revisit": False, "key": key}

        content = self._content_records(key)
        latest = content[-1] if content else None
        same_content = bool(
            latest
            and latest.get("signature") == sig
            and latest.get("parser_version") == rec.get("parser_version")
        )

        if same_content:
            rec["content_changed"] = False
            rec["first_seen_at"] = latest.get("first_seen_at") or latest.get("observed_at")
            rec["last_seen_at"] = rec["observed_at"]
        else:
            rec["content_changed"] = True
            rec["first_seen_at"] = (content[0].get("first_seen_at") if content else rec["observed_at"])
            rec["last_seen_at"] = rec["observed_at"]

        self._records.append(rec)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        price_changed = bool(latest and latest.get("price") != rec.get("price"))
        return {
            "stored": True,
            "new": not content,
            "price_changed": price_changed,
            "revisit": same_content,
            "key": key,
        }

    def _rewrite(self) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            for rec in self._records:
                handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def all(self) -> list[dict]:
        return list(self._records)

    def current(self) -> list[dict]:
        """Latest content/state per key; ``last_seen_at`` = latest visit timestamp."""
        latest: dict[str, dict] = {}
        last_seen: dict[str, str] = {}
        for rec in self._records:
            key = rec.get("key", "")
            last_seen[key] = max(last_seen.get(key, ""), rec.get("observed_at") or "")
            if rec.get("content_changed", True):
                latest[key] = rec
        out = []
        for key, rec in latest.items():
            rec = dict(rec)
            rec["last_seen_at"] = last_seen.get(key) or rec.get("last_seen_at") or rec.get("observed_at")
            out.append(rec)
        return out

    def visits(self) -> int:
        """Number of visit (rilevazione) records with unchanged content."""
        return sum(1 for r in self._records if r.get("content_changed") is False)

    def snapshots(self) -> int:
        """Number of content/state records (distinct content versions)."""
        return sum(1 for r in self._records if r.get("content_changed", True))

    def renormalize_comuni(self) -> int:
        """Normalize the ``comune`` field of existing records and recompute their
        signature (one-off migration). Returns the number of changed records."""
        changed = 0
        for rec in self._records:
            cur = rec.get("comune") or ""
            norm = normalize_comune(cur)
            if norm and norm != cur:
                rec["comune"] = norm
                changed += 1
            # recompute signature from the stored fields (best effort)
            sig = (
                rec.get("price"), rec.get("surface_m2"), rec.get("rooms"), rec.get("bedrooms"),
                rec.get("bathrooms"), rec.get("title"), rec.get("comune"), rec.get("typology"),
                rec.get("contract"), rec.get("energy_class"), rec.get("floor"), rec.get("elevator"),
                rec.get("latitude"), rec.get("longitude"), rec.get("price_qualifier"),
                rec.get("previous_price"), rec.get("discount"),
            )
            rec["signature"] = list(sig)
        if changed:
            self._rewrite()
        return changed

    def history(self, native_id: str) -> list[dict]:
        """Content/state changes for a listing (NOT raw visits)."""
        return [r for r in self._records if r.get("native_id") == native_id and r.get("content_changed", True)]

    def compact(self) -> int:
        """Collapse duplicate *content* records with the same (key, signature);
        visit records are always preserved. Returns removed count."""
        seen: dict[tuple, dict] = {}
        order: list[dict] = []
        removed = 0
        for rec in self._records:
            if not rec.get("content_changed", True):
                order.append(rec)
                continue
            gkey = (rec.get("key", ""), tuple(rec.get("signature") or ()))
            if gkey in seen:
                prev = seen[gkey]
                prev["first_seen_at"] = min(prev.get("first_seen_at") or "", rec.get("first_seen_at") or "")
                removed += 1
            else:
                seen[gkey] = rec
                order.append(rec)
        if removed:
            self._records = order
            self._rewrite()
        return removed


#: Lowercase particles kept lowercase in Italian title case.
_IT_PARTICLES = {"del", "della", "di", "de", "d", "e", "ed", "la", "le", "il", "lo", "in", "sul", "sui", "sugli", "sulla", "sulle", "a", "al", "ai", "da", "dal", "dai", "con", "per", "tra", "fra", "nel", "nella", "nei", "nelle", "dello", "degli", "delle"}


def normalize_comune(name: str) -> str:
    """Canonical Italian title-case for a municipality name.

    Turns "RIVA DEL GARDA" or "riva del garda" into "Riva del Garda" so the same
    comune from different sources aggregates together.
    """
    if not name:
        return ""
    words = name.strip().lower().split()
    out = []
    for i, w in enumerate(words):
        if w in _IT_PARTICLES and i != 0:
            out.append(w)
        elif w.startswith("d'"):
            out.append("d'" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


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
        comune=normalize_comune((data.get("city") or {}).get("title") or ""),
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


def _classify_http(status: int) -> str:
    if status in (404, 410):
        return "not_available"
    if status in (401, 403):
        return "blocked"
    if status >= 500:
        return "error"
    return f"http_{status}"


def run_listings_pilot(*, store_path=None, max_listings: int | None = None, source: str = "tecnocasa_it") -> dict:
    """Discover and ingest Tecnocasa Trento listings, producing a manifest.

    Fetches every discovered URL (up to ``max_listings``; ``None`` = all) and
    classifies each URL: valid_new, revisit, not_available, blocked, error,
    no_data. URLs beyond ``max_listings`` are reported as ``backlog``.
    """
    import time
    import uuid

    from app.core.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    store = ListingStore(store_path or PROJECT_ROOT / "data" / "listings.jsonl")
    run_id = "tc-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = time.monotonic()

    urls = discover_tecnocasa_urls(province="trento", max_urls=100_000)
    limit = max_listings if max_listings is not None else len(urls)
    report = {
        "run_id": run_id,
        "source": source,
        "method": "http",
        "status": "completed",
        "discovered": len(urls),
        "processed": 0,
        "backlog": max(0, len(urls) - limit),
        "records": 0,
        "stored": 0,
        "revisits": 0,
        "price_changes": 0,
        "errors": 0,
        "manifest": [],
    }

    headers = {"User-Agent": settings.user_agent}
    with httpx.Client(timeout=settings.request_timeout_s, follow_redirects=True, headers=headers) as client:
        for url in urls[:limit]:
            outcome: dict = {"url": url, "outcome": ""}
            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                outcome["outcome"] = "error"
                outcome["detail"] = str(exc)[:120]
                report["errors"] += 1
                report["manifest"].append(outcome)
                continue
            if resp.status_code != 200:
                outcome["outcome"] = _classify_http(resp.status_code)
                report["errors"] += 1
                report["manifest"].append(outcome)
                continue
            listing = parse_tecnocasa_detail(resp.text, url=url, source=source, run_id=run_id)
            if listing is None:
                outcome["outcome"] = "no_data"
                report["errors"] += 1
                report["manifest"].append(outcome)
                continue
            result = store.observe(listing)
            report["records"] += 1
            report["processed"] += 1
            report["stored"] += 1 if result.get("stored") else 0
            report["revisits"] += 1 if result.get("revisit") else 0
            report["price_changes"] += 1 if result.get("price_changed") else 0
            outcome["outcome"] = "revisit" if result.get("revisit") else ("valid_new" if result.get("new") else "valid_updated")
            report["manifest"].append(outcome)
            time.sleep(1.0)

    report["current"] = len(store.current())
    report["duration_seconds"] = round(time.monotonic() - started, 2)
    return report


#: Major Trento-province municipalities used to scope the Gabetti sitemap filter.
#: This is an explicit, documented scope, not a claim of full province coverage.
TRENTO_COMUNI = [
    "trento", "rovereto", "riva-del-garda", "arco", "pergine-valsugana",
    "mezzolombardo", "levico-terme", "borgo-valsugana", "ala", "mori",
    "lavis", "civezzano", "aldeno", "besenello", "calliano", "mattarello",
    "nago-torbole", "dro", "vallelaghi", "pinzolo", "andalo", "molveno",
    "folgaria", "predazzo", "cavalese", "clès", "malè", "tione",
]


def parse_gabetti_detail(html_text: str, *, url: str, source: str, run_id: str) -> Listing | None:
    """Parse a Gabetti detail page from its meta tags.

    The RSC payload is hard to parse; the meta description carries the verified
    core fields deterministically (typology, address, comune (province), price,
    surface, rooms, bathrooms). Fields not exposed there stay ``None``/empty.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    text = ""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        text = og_title.get("content") or ""
    if not text:
        og_desc = soup.find("meta", attrs={"name": "description"})
        if og_desc:
            text = og_desc.get("content") or ""
    if not text:
        return None

    native_id = url.rstrip("/").rsplit("/", 1)[-1]

    def _num(pattern: str) -> int | None:
        m = re.search(pattern, text)
        return int(m.group(1).replace(".", "")) if m else None

    typology_m = re.search(r"Immobile (.+?) in ", text)
    comune_m = re.search(r", ([^,()]+) \((\w{2})\)", text)
    address_m = re.search(r" in (.+?), ", text)

    price = _num(r"(\d[\d.]*)\s*€")
    surface = _num(r"(\d+)\s*m²")
    rooms = _num(r"(\d+)\s*locali")
    bathrooms = _num(r"(\d+)\s*bagni")

    return Listing(
        source=source,
        native_id=native_id,
        url=url,
        title=(typology_m.group(1) if typology_m else "") + (", " + comune_m.group(1) if comune_m else ""),
        typology=(typology_m.group(1) or "").lower(),
        contract="vendita",
        price=float(price) if price else None,
        currency="EUR",
        surface_m2=float(surface) if surface else None,
        surface_definition="non_specificata",
        comune=normalize_comune(comune_m.group(1)) if comune_m else "",
        province=comune_m.group(2) if comune_m else "",
        address=address_m.group(1) if address_m else "",
        rooms=rooms,
        bathrooms=bathrooms,
        published_at="",
        content_hash=sha256(html_text.encode()),
        run_id=run_id,
        parser_version="gabetti-0.1.0",
    )


def discover_gabetti_urls(*, max_urls: int = 200, timeout: float = 30.0) -> list[str]:
    """Discover Gabetti residential-sale URLs for Trento municipalities."""
    urls: list[str] = []
    headers = {"User-Agent": "TREI-bot/0.1 (+https://example.org/trentino-rei; contact: data@example.org)"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        resp = client.get("https://www.gabetti.it/sitemap/properties.xml")
        if resp.status_code != 200:
            return urls
        for m in re.finditer(r"<loc>([^<]+)</loc>", resp.text):
            loc = m.group(1)
            if "/vendita/" not in loc:
                continue
            comune = loc.split("/vendita/", 1)[1].split("/", 1)[0].lower()
            if comune in TRENTO_COMUNI:
                urls.append(loc)
                if len(urls) >= max_urls:
                    return urls
    return urls


def run_gabetti_pilot(*, store_path=None, max_listings: int = 15, source: str = "gabetti_it") -> dict:
    """Discover and ingest a small, explicit sample of Gabetti Trento listings."""
    import time
    import uuid

    from app.core.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    store = ListingStore(store_path or PROJECT_ROOT / "data" / "listings.jsonl")
    run_id = "gb-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = time.monotonic()

    urls = discover_gabetti_urls(max_urls=max_listings * 2)
    report = {"run_id": run_id, "source": source, "method": "http", "status": "completed", "discovered": len(urls), "records": 0, "stored": 0, "price_changes": 0, "errors": []}

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
            listing = parse_gabetti_detail(resp.text, url=url, source=source, run_id=run_id)
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


