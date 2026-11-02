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
    unit_class: str = ""
    offer_kind: str = ""
    advertiser: str = ""
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
            self.unit_class,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _signature_from_record(rec: dict) -> list:
    """The extracted-fields signature, derived from a stored record."""
    return list((
        rec.get("price"), rec.get("surface_m2"), rec.get("rooms"), rec.get("bedrooms"),
        rec.get("bathrooms"), rec.get("title"), rec.get("comune"), rec.get("typology"),
        rec.get("contract"), rec.get("energy_class"), rec.get("floor"), rec.get("elevator"),
        rec.get("latitude"), rec.get("longitude"), rec.get("price_qualifier"),
        rec.get("previous_price"), rec.get("discount"), rec.get("unit_class"),
    ))


def _unit_class_from_typology(typology: str) -> str:
    t = (typology or "").strip().lower()
    if t in {"appartamento", "appartamenti", "attico", "attico e superattico",
             "villetta a schiera", "bifamiliare", "casa indipendente", "villetta",
             "villa", "rustico", "casale", "cascina", "mansarda", "loft",
             "quadrilocale", "trilocale", "bilocale", "monolocale",
             "multilocale", "appartamento di lusso"}:
        return "residenziale"
    if t in {"negozio", "ufficio", "capannone", "magazzino", "laboratorio",
             "locale commerciale", "esercizio commerciale", "deposito"}:
        return "commerciale"
    if t in {"hotel", "albergo", "b&b", "bed and breakfast", "affittacamere",
             "ristorante", "bar"}:
        return "ricettivo"
    if t in {"box", "posto auto", "garage", "autorimessa", "cantina", "soffitta"}:
        return "box"
    if t in {"terreno", "terreno edificabile", "terreno agricolo", "area edificabile"}:
        return "terreno"
    if t in {"cantiere", "nuova costruzione", "nuove costruzioni"}:
        return "progetto"
    return ""


class ListingStore:
    """JSONL store of listing observations; current state = latest per key.

    Idempotent: re-observing the same (source, native_id) with the same
    extracted fields is a no-op except refreshing ``last_seen_at``; a
    price change appends a new observation instead of overwriting history.
    Content identity never depends on the parser version."""

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
        #: Content identity = extracted fields ONLY. A parser-version bump that
        #: maps the same fields identically must NOT create a false new version.
        same_content = bool(
            latest
            and latest.get("signature") == sig
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
            rec["signature"] = _signature_from_record(rec)
        if changed:
            self._rewrite()
        return changed

    def migrate_contract_unit_class(self) -> int:
        """One-off, idempotent migration (2026-10-31).

        * ``tecnocasa_it``: the page's contract slug was ``annunci``; the URL
          path says ``/vendita/`` so the contract becomes ``vendita``.
        * ``unit_class`` is derived from the known typology/path.
        * Signatures are recomputed with the new fields so the next visit is a
          no-op (no false new content versions).
        """
        changed = 0
        for rec in self._records:
            src = rec.get("source")
            if src == "tecnocasa_it":
                if rec.get("contract") != "vendita":
                    rec["contract"] = "vendita"
                    changed += 1
                if not rec.get("unit_class"):
                    rec["unit_class"] = "residenziale"
                    changed += 1
            elif src == "gabetti_it" and not rec.get("unit_class"):
                unit = _unit_class_from_typology(rec.get("typology") or "")
                rec["unit_class"] = unit
                changed += 1
            sig = _signature_from_record(rec)
            if list(sig) != rec.get("signature"):
                rec["signature"] = list(sig)
                changed += 1
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
        contract="affitto" if "/affitto/" in url else ("vendita" if "/vendita/" in url else (data.get("contract") or {}).get("slug") or "vendita"),
        unit_class=_unit_class_from_typology((data.get("type") or {}).get("title") or "") or "residenziale",
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


#: Residential-sale paths included in the Tecnocasa scope (sitemap based).
TECNOCASA_RESIDENTIAL_PATHS = (
    "vendita/appartamenti", "vendita/ville", "vendita/rustici-cascine-case",
)
#: Other paths counted as *excluded* (never fetched).
TECNOCASA_EXCLUDED_PATHS = (
    "vendita/box-posti-auto", "vendita/terreno", "affitto/appartamenti",
    "affitto/box-posti-auto", "affitto/ville", "affitto/rustici-cascine-case",
    "affitto/terreno",
)


def _sitemap_urls(client: httpx.Client, url: str) -> list[str]:
    import gzip

    resp = client.get(url)
    if resp.status_code != 200:
        return []
    try:
        text = gzip.decompress(resp.content).decode("utf-8", "replace")
    except OSError:
        text = resp.text
    return re.findall(r"<loc>([^<]+)</loc>", text)


def discover_tecnocasa_urls(*, province: str = "trento", max_urls: int = 200, timeout: float = 20.0) -> list[str]:
    """Discover residential-sale URLs (appartamenti + ville + rustici) for the
    province from the Tecnocasa ``residenziali-*.xml.gz`` sitemaps."""
    urls: list[str] = []
    headers = {"User-Agent": "TREI-bot/0.1 (+https://example.org/trentino-rei; contact: data@example.org)"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for i in range(12):
            for loc in _sitemap_urls(client, f"https://www.tecnocasa.it/sitemap/residenziali-{i}.xml.gz"):
                if any(f"/{p}/{province}/" in loc for p in TECNOCASA_RESIDENTIAL_PATHS):
                    urls.append(loc)
                    if len(urls) >= max_urls:
                        return urls
    return urls


def discover_tecnocasa_excluded(*, province: str = "trento", timeout: float = 20.0) -> dict[str, int]:
    """Count out-of-scope URLs so the run manifest shows them as excluded.

    Also counts ``cantieri`` (new-construction *projects*, a separate sitemap,
    classified as ``progetto``, never counted as dwellings).
    """
    counts: dict[str, int] = {}
    headers = {"User-Agent": "TREI-bot/0.1 (+https://example.org/trentino-rei; contact: data@example.org)"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for i in range(12):
            for loc in _sitemap_urls(client, f"https://www.tecnocasa.it/sitemap/residenziali-{i}.xml.gz"):
                if f"/{province}/" not in loc:
                    continue
                for p in TECNOCASA_EXCLUDED_PATHS:
                    if f"/{p}/{province}/" in loc:
                        counts[p] = counts.get(p, 0) + 1
        for i in range(4):
            for loc in _sitemap_urls(client, f"https://www.tecnocasa.it/sitemap/commerciali-{i}.xml.gz"):
                if f"/{province}/" in loc:
                    counts["commerciali"] = counts.get("commerciali", 0) + 1
        for loc in _sitemap_urls(client, "https://www.tecnocasa.it/sitemap/cantieri.xml.gz"):
            if f"/{province}/" in loc:
                counts["cantieri_progetti"] = counts.get("cantieri_progetti", 0) + 1
    return counts


def _classify_http(status: int) -> str:
    if status in (404, 410):
        return "not_available"
    if status in (401, 403):
        return "blocked"
    if status >= 500:
        return "error"
    return f"http_{status}"


def run_listings_pilot(*, store_path=None, max_listings: int | None = None, source: str = "tecnocasa_it") -> dict:
    """Discover and ingest Tecnocasa Trento listings, producing a persisted manifest.

    Fetches every discovered URL (``max_listings=None`` = whole queue) under the
    per-source budget caps (``crawl_max_requests``, ``crawl_max_seconds``). The
    queue is ordered by the oldest last attempt, so an interrupted run resumes
    from the untouched tail instead of re-visiting the same head. Every URL
    outcome goes to the status ledger and one summary to the run ledger.
    """
    import time
    import uuid

    from app.acquisition.ledger import (
        RETRYABLE_OUTCOMES,
        TERMINAL_OUTCOMES,
        RunLedger,
        StatusLedger,
    )
    from app.core.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    store = ListingStore(store_path or PROJECT_ROOT / "data" / "listings.jsonl")
    runs = RunLedger()
    status = StatusLedger()
    run_id = "tc-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = time.monotonic()

    urls = discover_tecnocasa_urls(province="trento", max_urls=100_000)
    #: oldest last attempt first → the unvisited tail is processed first.
    urls.sort(key=lambda u: (status.last_attempt_at(u) or "0000", u))
    limit = max_listings if max_listings is not None else len(urls)
    max_requests = min(settings.crawl_max_requests, limit) if limit else settings.crawl_max_requests
    max_seconds = float(settings.crawl_max_seconds)
    excluded = discover_tecnocasa_excluded(province="trento")

    report = {
        "run_id": run_id,
        "source": source,
        "method": "http",
        "status": "completed",
        "discovered": len(urls),
        "attempted": 0,
        "backlog": max(0, len(urls) - limit),
        "records": 0,
        "stored": 0,
        "revisits": 0,
        "price_changes": 0,
        "errors": 0,
        "outcomes": {},
        "queue": {"pending": 0, "retryable_errors": 0, "budget_deferred": 0, "terminal": 0},
        "excluded_categories": excluded,
        "sitemap_scope": list(TECNOCASA_RESIDENTIAL_PATHS),
        "complete": False,
        "discovered_urls": urls,
        "manifest": [],
    }

    def _note(outcome: str, url: str, http_status: int | None, detail: str = "") -> None:
        report["outcomes"][outcome] = report["outcomes"].get(outcome, 0) + 1
        status.record(run_id=run_id, source=source, url=url, outcome=outcome,
                      http_status=http_status, detail=detail)
        report["manifest"].append({"url": url, "outcome": outcome})

    headers = {"User-Agent": settings.user_agent}
    with httpx.Client(timeout=settings.request_timeout_s, follow_redirects=True, headers=headers) as client:
        for url in urls[:limit]:
            if report["attempted"] >= max_requests or (time.monotonic() - started) >= max_seconds:
                break
            report["attempted"] += 1
            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                report["errors"] += 1
                _note("error", url, None, str(exc)[:120])
                continue
            if resp.status_code != 200:
                outcome = _classify_http(resp.status_code)
                report["errors"] += 1
                _note(outcome, url, resp.status_code)
                continue
            listing = parse_tecnocasa_detail(resp.text, url=url, source=source, run_id=run_id)
            if listing is None:
                report["errors"] += 1
                _note("no_data", url, 200)
                continue
            result = store.observe(listing)
            report["records"] += 1
            report["stored"] += 1 if result.get("stored") else 0
            report["revisits"] += 1 if result.get("revisit") else 0
            report["price_changes"] += 1 if result.get("price_changed") else 0
            outcome = "revisit" if result.get("revisit") else ("valid_new" if result.get("new") else "valid_updated")
            _note(outcome, url, 200)
            time.sleep(1.0)

    attempted = report["attempted"]
    deferred = max(0, min(limit, len(urls)) - attempted)
    retryable = sum(1 for u, o in ((m["url"], m["outcome"]) for m in report["manifest"]) if o in RETRYABLE_OUTCOMES)
    terminal = sum(1 for m in report["manifest"] if m["outcome"] in TERMINAL_OUTCOMES)
    report["queue"] = {
        "pending": max(0, min(limit, len(urls)) - attempted),
        "retryable_errors": retryable,
        "budget_deferred": deferred,
        "terminal": terminal,
    }
    #: complete = every discovered URL inside the cap was actually attempted.
    report["complete"] = bool(deferred == 0 and len(urls) <= limit)
    report["status"] = "completed" if report["complete"] else "partial"
    report["current"] = len(store.current())
    report["duration_seconds"] = round(time.monotonic() - started, 2)
    report.pop("manifest")
    runs.record(report)
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
        unit_class=_unit_class_from_typology(typology_m.group(1) or ""),
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


def run_gabetti_pilot(*, store_path=None, max_listings: int | None = None, source: str = "gabetti_it") -> dict:
    """Discover and ingest Gabetti Trento listings (all discovered by default).

    Same budget, ordering and ledger conventions as ``run_listings_pilot``.
    """
    import time
    import uuid

    from app.acquisition.ledger import (
        RETRYABLE_OUTCOMES,
        TERMINAL_OUTCOMES,
        RunLedger,
        StatusLedger,
    )
    from app.core.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    store = ListingStore(store_path or PROJECT_ROOT / "data" / "listings.jsonl")
    runs = RunLedger()
    status = StatusLedger()
    run_id = "gb-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = time.monotonic()

    urls = discover_gabetti_urls(max_urls=100_000 if max_listings is None else max_listings * 2)
    urls.sort(key=lambda u: (status.last_attempt_at(u) or "0000", u))
    limit = max_listings if max_listings is not None else len(urls)
    max_requests = min(settings.crawl_max_requests, limit) if limit else settings.crawl_max_requests
    max_seconds = float(settings.crawl_max_seconds)
    report = {
        "run_id": run_id, "source": source, "method": "http", "status": "partial",
        "discovered": len(urls), "attempted": 0, "backlog": max(0, len(urls) - limit),
        "records": 0, "stored": 0, "revisits": 0, "price_changes": 0, "errors": 0,
        "outcomes": {},
        "queue": {"pending": 0, "retryable_errors": 0, "budget_deferred": 0, "terminal": 0},
        "complete": False,
        "discovered_urls": urls,
        "manifest": [],
    }

    def _note(outcome: str, url: str, http_status: int | None, detail: str = "") -> None:
        report["outcomes"][outcome] = report["outcomes"].get(outcome, 0) + 1
        status.record(run_id=run_id, source=source, url=url, outcome=outcome,
                      http_status=http_status, detail=detail)
        report["manifest"].append({"url": url, "outcome": outcome})

    headers = {"User-Agent": settings.user_agent}
    with httpx.Client(timeout=settings.request_timeout_s, follow_redirects=True, headers=headers) as client:
        for url in urls[:limit]:
            if report["attempted"] >= max_requests or (time.monotonic() - started) >= max_seconds:
                break
            report["attempted"] += 1
            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                report["errors"] += 1
                _note("error", url, None, str(exc)[:120])
                continue
            if resp.status_code != 200:
                outcome = _classify_http(resp.status_code)
                report["errors"] += 1
                _note(outcome, url, resp.status_code)
                continue
            listing = parse_gabetti_detail(resp.text, url=url, source=source, run_id=run_id)
            if listing is None:
                report["errors"] += 1
                _note("no_data", url, 200)
                continue
            result = store.observe(listing)
            report["records"] += 1
            report["stored"] += 1 if result.get("stored") else 0
            report["revisits"] += 1 if result.get("revisit") else 0
            report["price_changes"] += 1 if result.get("price_changed") else 0
            outcome = "revisit" if result.get("revisit") else ("valid_new" if result.get("new") else "valid_updated")
            _note(outcome, url, 200)
            time.sleep(1.0)

    attempted = report["attempted"]
    deferred = max(0, min(limit, len(urls)) - attempted)
    retryable = sum(1 for m in report["manifest"] if m["outcome"] in RETRYABLE_OUTCOMES)
    terminal = sum(1 for m in report["manifest"] if m["outcome"] in TERMINAL_OUTCOMES)
    report["queue"] = {
        "pending": max(0, min(limit, len(urls)) - attempted),
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


