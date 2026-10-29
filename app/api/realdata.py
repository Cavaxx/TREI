"""Read-only API over the real data stores (listings + statistics).

The web app reads the JSONL stores directly: each request opens a fresh store
instance, so concurrent writers are always reflected and there is no divergent
second archive. Everything here is real data, never ``synthetic_demo``.
"""

from __future__ import annotations

import csv
import io
import statistics as _stats

from fastapi import APIRouter, HTTPException, Query

from app.acquisition.listings import ListingStore
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT

router = APIRouter(prefix="/api/v1/real", tags=["real-data"])

LISTINGS_PATH = PROJECT_ROOT / "data" / "listings.jsonl"
OBSERVATIONS_PATH = PROJECT_ROOT / "data" / "observations.jsonl"


def _listings() -> list[dict]:
    if not LISTINGS_PATH.exists():
        return []
    return ListingStore(LISTINGS_PATH).current()


def _observations() -> list[dict]:
    if not OBSERVATIONS_PATH.exists():
        return []
    return ObservationStore(OBSERVATIONS_PATH).all()


def _price_m2(rec: dict) -> float | None:
    price = rec.get("price")
    surface = rec.get("surface_m2")
    if isinstance(price, (int, float)) and isinstance(surface, (int, float)) and price > 0 and surface > 0:
        return round(price / surface, 2)
    return None


def _csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection for untrusted cells."""
    text = str(value if value is not None else "")
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    return text


@router.get("/listings")
def list_listings(
    comune: str | None = None,
    source: str | None = None,
    typology: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    surface_min: float | None = None,
    surface_max: float | None = None,
    sort: str = Query("observed_at", pattern="^(observed_at|price|price_m2|comune)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    out = []
    for rec in _listings():
        if comune and (rec.get("comune") or "").lower() != comune.lower():
            continue
        if source and rec.get("source") != source:
            continue
        if typology and (rec.get("typology") or "").lower() != typology.lower():
            continue
        p = rec.get("price")
        if price_min is not None and (p is None or p < price_min):
            continue
        if price_max is not None and (p is None or p > price_max):
            continue
        s = rec.get("surface_m2")
        if surface_min is not None and (s is None or s < surface_min):
            continue
        if surface_max is not None and (s is None or s > surface_max):
            continue
        item = dict(rec)
        item["price_m2"] = _price_m2(rec)
        out.append(item)

    reverse = sort != "comune"
    out.sort(key=lambda r: (r.get(sort) is None, r.get(sort) or 0), reverse=reverse)
    total = len(out)
    return {"count": total, "limit": limit, "offset": offset, "items": out[offset : offset + limit], "demo_data": False}


@router.get("/listings/{native_id}")
def listing_detail(native_id: str) -> dict:
    store = ListingStore(LISTINGS_PATH)
    history = store.history(native_id)
    if not history:
        raise HTTPException(status_code=404, detail="listing not found")
    current = history[-1]
    return {
        "native_id": native_id,
        "current": {**current, "price_m2": _price_m2(current)},
        "snapshots": len(history),
        "history": [{k: h.get(k) for k in ("observed_at", "price", "previous_price", "discount", "content_hash")} for h in history],
        "demo_data": False,
    }


@router.get("/statistics")
def statistics(indicator: str | None = None) -> dict:
    rows = [r for r in _observations() if not r.get("superseded")]
    if indicator:
        rows = [r for r in rows if r.get("indicator") == indicator]
    grouped: dict[str, dict] = {}
    for r in rows:
        key = r.get("indicator", "?")
        grouped.setdefault(key, {
            "indicator": key,
            "definition": r.get("definition", ""),
            "definition_version": r.get("definition_version", ""),
            "unit": r.get("unit", ""),
            "source": r.get("source", ""),
            "series": r.get("series", ""),
            "geo_level": r.get("geo_level", ""),
            "periods": [],
        })
        grouped[key]["periods"].append({"period": r.get("period"), "value": r.get("value"), "territory_code": r.get("territory_code", "")})
    for key in grouped:
        grouped[key]["periods"].sort(key=lambda p: p["period"])
    return {"indicators": list(grouped.values()), "count": len(rows), "demo_data": False}


@router.get("/coverage")
def coverage() -> dict:
    listings = _listings()
    obs = [r for r in _observations() if not r.get("superseded")]
    by_comune: dict[str, int] = {}
    by_source: dict[str, int] = {}
    missing: dict[str, int] = {}
    for r in listings:
        by_comune[r.get("comune") or "?"] = by_comune.get(r.get("comune") or "?", 0) + 1
        by_source[r.get("source") or "?"] = by_source.get(r.get("source") or "?", 0) + 1
        for field in ("price", "surface_m2", "typology", "published_at"):
            if r.get(field) in (None, ""):
                missing[field] = missing.get(field, 0) + 1
    return {
        "listings": len(listings),
        "listings_by_comune": by_comune,
        "listings_by_source": by_source,
        "missing_fields": missing,
        "statistical_observations": len(obs),
        "statistical_indicators": sorted({r.get("indicator") for r in obs}),
        "demo_data": False,
    }


@router.get("/prices")
def prices(comune: str | None = None, min_n: int = Query(1, ge=1)) -> dict:
    rows = _listings()
    if comune:
        rows = [r for r in rows if (r.get("comune") or "").lower() == comune.lower()]
    groups: dict[str, list[float]] = {}
    for r in rows:
        pm2 = _price_m2(r)
        if pm2 is None:
            continue
        groups.setdefault(r.get("comune") or "?", []).append(pm2)
    out = []
    for name, values in groups.items():
        if len(values) < min_n:
            continue
        out.append({"comune": name, "n": len(values), "median_eur_m2": round(_stats.median(values), 2), "min": round(min(values), 2), "max": round(max(values), 2)})
    out.sort(key=lambda x: x["comune"])
    return {"sample_observed": True, "note": "mediana dei rapporti prezzo/superficie sul campione osservato, non un prezzo di mercato", "zones": out, "demo_data": False}


@router.get("/export")
def export_data(
    comune: str | None = None,
    source: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    surface_min: float | None = None,
    surface_max: float | None = None,
    format: str = Query("csv", pattern="^(csv|json)$"),
) -> object:
    filtered = list_listings(comune=comune, source=source, price_min=price_min, price_max=price_max, surface_min=surface_min, surface_max=surface_max, limit=500, offset=0)
    items = filtered["items"]
    if format == "json":
        return {"count": filtered["count"], "items": items, "demo_data": False}

    fields = ["source", "native_id", "url", "title", "typology", "contract", "comune", "province", "price", "currency", "price_qualifier", "previous_price", "discount", "surface_m2", "surface_definition", "rooms", "bedrooms", "bathrooms", "floor", "elevator", "energy_class", "published_at", "observed_at", "first_seen_at", "last_seen_at", "price_m2"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for rec in items:
        writer.writerow({k: _csv_safe(rec.get(k)) for k in fields})

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trei_listings.csv"})

