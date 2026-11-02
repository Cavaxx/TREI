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

from app.acquisition.ledger import RunLedger, availability
from app.acquisition.listings import ListingStore
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT

router = APIRouter(prefix="/api/v1/real", tags=["real-data"])

LISTINGS_PATH = PROJECT_ROOT / "data" / "listings.jsonl"
OBSERVATIONS_PATH = PROJECT_ROOT / "data" / "observations.jsonl"

#: The single availability rule shared by store counts, API, UI and export.
STATUS_LABELS = {
    "available": "disponibile",
    "to_reconfirm": "da riconfermare (404 o assente dalla sitemap; NON una vendita)",
    "confirmed_unavailable": "non più disponibile (confermato; NON una vendita)",
    "uncertain": "incerto (errore o non visitato nell'ultimo run)",
}


def _listings() -> list[dict]:
    if not LISTINGS_PATH.exists():
        return []
    return ListingStore(LISTINGS_PATH).current()


def _status_map() -> dict[str, str]:
    return availability(store_records=_listings())


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
    unit_class: str | None = None,
    availability: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    surface_min: float | None = None,
    surface_max: float | None = None,
    sort: str = Query("observed_at", pattern="^(observed_at|price|price_m2|comune)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    out = []
    status = _status_map()
    for rec in _listings():
        if comune and (rec.get("comune") or "").lower() != comune.lower():
            continue
        if source and rec.get("source") != source:
            continue
        if typology and (rec.get("typology") or "").lower() != typology.lower():
            continue
        if unit_class and (rec.get("unit_class") or "") != unit_class:
            continue
        if availability and status.get(rec.get("key") or "") != availability:
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
        item["status"] = status.get(rec.get("key") or "", "uncertain")
        out.append(item)

    reverse = sort != "comune"
    out.sort(key=lambda r: (r.get(sort) is None, r.get(sort) or 0), reverse=reverse)
    total = len(out)
    summary = {}
    for st in ("available", "to_reconfirm", "confirmed_unavailable", "uncertain"):
        summary[st] = sum(1 for r in out if r.get("status") == st)
    return {"count": total, "limit": limit, "offset": offset, "items": out[offset : offset + limit],
            "status_breakdown": summary, "status_labels": STATUS_LABELS, "demo_data": False}


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


@router.get("/portals")
def portals_status() -> dict:
    """Concrete status of the three major portals (channel/access/connector/live)."""
    from app.fetchers.registry import SourceRegistry

    registry = SourceRegistry.load()
    out = []
    for name in ("idealista_it", "subito_it", "immobiliare_it"):
        config = registry.get(name)
        if config is None:
            continue
        acq = config.extra.get("acquisition") if isinstance(config.extra, dict) else None
        acq = acq if isinstance(acq, dict) else {}
        out.append(
            {
                "name": name,
                "domain": config.domain,
                "status": config.status,
                "tier": config.tier,
                "channel": acq.get("channel", config.recommended_mode),
                "access": acq.get("access", "unknown"),
                "access_request_url": acq.get("access_request_url", ""),
                "connector": acq.get("connector", "none"),
                "live_tested": acq.get("live_tested", False),
                "records": acq.get("records", 0),
                "last_verified": config.last_verified,
            }
        )
    return {"portals": out, "demo_data": False}


@router.get("/macro")
def macro_series() -> dict:
    """The two macro series with metadata, exact coverage and last update."""
    obs = [r for r in _observations() if not r.get("superseded")]
    out = {}
    for ind in ("mortgage_rate_new_loans", "cpi_nic_index", "hicp_index"):
        rows = sorted([r for r in obs if r.get("indicator") == ind], key=lambda r: r.get("period") or "")
        if not rows:
            out[ind] = {"status": "missing", "count": 0}
            continue
        first = rows[0]
        out[ind] = {
            "indicator": ind,
            "source": first.get("source"),
            "definition": first.get("definition"),
            "definition_version": first.get("definition_version"),
            "series": first.get("series"),
            "unit": first.get("unit"),
            "geo_level": first.get("geo_level"),
            "territory_code": first.get("territory_code"),
            "count": len(rows),
            "first_period": rows[0].get("period"),
            "last_period": rows[-1].get("period"),
            "last_retrieved_at": max(r.get("retrieved_at") or "" for r in rows),
            "provisional": sum(1 for r in rows if r.get("quality") == "provisional"),
            "periods": [{"period": r.get("period"), "value": r.get("value")} for r in rows],
        }
    return {"series": out, "demo_data": False}


@router.get("/deflation")
def deflation(base_period: str | None = None, index: str = "hicp") -> dict:
    """Deflate observed asking prices: P_real(t;b) = P_nominal(t) × I(b) / I(t).

    ``index=hicp`` uses the Eurostat HICP I25 levels (index_base2025, complete
    monthly series since 2015); ``index=nic`` uses the ISTAT NIC levels from
    the official communiqué attachment (base per observation). Only listings
    with a nominal price actually observed in a month whose index is published
    are deflated; a month without a published index is reported explicitly as
    ``indice del mese non ancora disponibile`` and is never approximated with
    a neighbouring month. Listing counts and V.07 counts are NEVER touched.
    """
    obs = [r for r in _observations() if not r.get("superseded")]
    indicator = "hicp_index" if index == "hicp" else "cpi_nic_index"
    by_period: dict[str, float] = {}
    unit = ""
    for r in obs:
        if r.get("indicator") != indicator:
            continue
        period = r.get("period")
        if not period or not isinstance(r.get("value"), (int, float)):
            continue
        #: keep the latest non-superseded value per period (revisions)
        by_period[period] = float(r.get("value"))
        unit = unit or r.get("unit") or ""
    if not by_period:
        return {"status": "blocked", "reason": f"{indicator} non presente nello store", "demo_data": False}
    base = base_period or max(by_period)
    if base not in by_period:
        return {"status": "invalid_base", "reason": f"{base} non pubblicato", "demo_data": False}
    i_base = by_period[base]
    status = _status_map()
    out = []
    months_missing: dict[str, int] = {}
    for rec in _listings():
        if status.get(rec.get("key") or "") != "available":
            continue
        if (rec.get("unit_class") or "") != "residenziale":
            continue
        price = rec.get("price")
        observed = (rec.get("observed_at") or "")[:7]
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        if observed not in by_period:
            months_missing[observed] = months_missing.get(observed, 0) + 1
            continue
        out.append({
            "native_id": rec.get("native_id"),
            "source": rec.get("source"),
            "comune": rec.get("comune"),
            "nominal_price": price,
            "observed_month": observed,
            "real_price_base": base,
            "real_price": round(price * i_base / by_period[observed], 2),
        })
    return {
        "index": indicator,
        "index_unit": unit,
        "index_definition": "IPCA/HICP Italia (Eurostat, base 2025=100)" if index == "hicp" else "NIC Italia (ISTAT, base per osservazione)",
        "base_period": base,
        "formula": "P_reale(t;b) = P_nominale(t) × I(b) / I(t)",
        "n_deflated": len(out),
        "months_without_index": [{"month": k, "listings_skipped": v,
                                  "reason": "indice del mese non ancora disponibile"} for k, v in sorted(months_missing.items())],
        "note": "solo prezzi chiesti osservati in mesi con indice pubblicato; nessun mese adiacente usato come sostituto; i conteggi V.07 non sono mai deflazionati",
        "items": out,
        "demo_data": False,
    }


@router.get("/runs")
def runs_status() -> dict:
    """Last run per source from the persisted run ledger (outcome + queue)."""
    runs = RunLedger().all()
    latest: dict[str, dict] = {}
    for r in runs:
        key = r.get("source") or "?"
        if key not in latest or (r.get("started_at") or "") >= (latest[key].get("started_at") or ""):
            latest[key] = r
    out = []
    for source, r in sorted(latest.items()):
        out.append({
            "source": source,
            "run_id": r.get("run_id"),
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
            "status": r.get("status"),
            "complete": r.get("complete"),
            "inferred": r.get("inferred", False),
            "discovered": r.get("discovered"),
            "attempted": r.get("attempted"),
            "records": r.get("records"),
            "outcomes": r.get("outcomes"),
            "queue": r.get("queue"),
            "errors": r.get("errors"),
            "excluded_categories": r.get("excluded_categories"),
            "note": r.get("note", ""),
        })
    return {"runs": out, "demo_data": False}


def group_candidates(listings: list[dict]) -> list[dict]:
    """Conservative cross-source candidate groups.

    Two listings from *different* sources with the same normalized comune, the
    same typology and a surface within ±10% form a **provisional** candidate group
    (never a confirmed unique property). Price is never used as merge evidence.
    """
    groups: list[dict] = []
    used: set[int] = set()
    for i, a in enumerate(listings):
        if i in used:
            continue
        members = [a]
        for j in range(i + 1, len(listings)):
            b = listings[j]
            if j in used or a.get("source") == b.get("source"):
                continue
            if (a.get("comune") or "").lower() != (b.get("comune") or "").lower():
                continue
            if (a.get("typology") or "").lower() != (b.get("typology") or "").lower():
                continue
            sa, sb = a.get("surface_m2"), b.get("surface_m2")
            if not (isinstance(sa, (int, float)) and isinstance(sb, (int, float)) and sa > 0 and sb > 0):
                continue
            if abs(sa - sb) / max(sa, sb) <= 0.10:
                members.append(b)
                used.add(j)
        if len(members) >= 2:
            used.add(i)
            groups.append(
                {
                    "comune": a.get("comune"),
                    "typology": a.get("typology"),
                    "surface_m2": a.get("surface_m2"),
                    "confidence": "provisional",
                    "evidence": ["comune", "tipologia", "superficie (±10%)"],
                    "listings": [{"source": m.get("source"), "native_id": m.get("native_id"), "url": m.get("url")} for m in members],
                }
            )
    return groups


@router.get("/groups")
def property_groups() -> dict:
    """Provisional cross-source candidate property groups (dedup NOT confirmed)."""
    listings = _listings()
    groups = group_candidates(listings)
    grouped_ids = {lid for g in groups for lid in (m["native_id"] for m in g["listings"])}
    exclusive = {}
    for r in listings:
        if r.get("native_id") not in grouped_ids:
            exclusive[r.get("source")] = exclusive.get(r.get("source"), 0) + 1
    return {
        "listings": len(listings),
        "candidate_groups": len(groups),
        "listings_in_groups": len(grouped_ids),
        "groups": groups,
        "exclusive_by_source": exclusive,
        "note": "gruppi candidati provvisori, NON immobili unici certificati",
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
    status = _status_map()
    obs = [r for r in _observations() if not r.get("superseded")]
    by_comune: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_unit_class: dict[str, int] = {}
    by_offer_kind: dict[str, int] = {}
    missing: dict[str, int] = {}
    for r in listings:
        st = status.get(r.get("key") or "", "uncertain")
        by_status[st] = by_status.get(st, 0) + 1
        if st != "available":
            continue
        by_comune[r.get("comune") or "?"] = by_comune.get(r.get("comune") or "?", 0) + 1
        by_source[r.get("source") or "?"] = by_source.get(r.get("source") or "?", 0) + 1
        by_unit_class[r.get("unit_class") or "?"] = by_unit_class.get(r.get("unit_class") or "?", 0) + 1
        if r.get("offer_kind"):
            by_offer_kind[r.get("offer_kind")] = by_offer_kind.get(r.get("offer_kind"), 0) + 1
        for field in ("price", "surface_m2", "typology", "published_at"):
            if r.get(field) in (None, ""):
                missing[field] = missing.get(field, 0) + 1
    return {
        "listings_registered": len(listings),
        "listings_available": by_status.get("available", 0),
        "status_breakdown": by_status,
        "status_labels": STATUS_LABELS,
        "available_by_comune": by_comune,
        "available_by_source": by_source,
        "available_by_unit_class": by_unit_class,
        "available_by_offer_kind": by_offer_kind,
        "missing_fields": missing,
        "statistical_observations": len(obs),
        "statistical_indicators": sorted({r.get("indicator") for r in obs}),
        "demo_data": False,
    }


@router.get("/prices")
def prices(comune: str | None = None, min_n: int = Query(1, ge=1)) -> dict:
    status = _status_map()
    rows = [r for r in _listings()
            if status.get(r.get("key") or "") == "available"
            and (r.get("unit_class") or "") == "residenziale"
            and not r.get("offer_kind")]
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
    unit_class: str | None = None,
    availability: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    surface_min: float | None = None,
    surface_max: float | None = None,
    format: str = Query("csv", pattern="^(csv|json)$"),
) -> object:
    filtered = list_listings(comune=comune, source=source, unit_class=unit_class, availability=availability,
                             price_min=price_min, price_max=price_max, surface_min=surface_min,
                             surface_max=surface_max, limit=500, offset=0)
    items = filtered["items"]
    if format == "json":
        return {"count": filtered["count"], "items": items, "status_breakdown": filtered["status_breakdown"], "demo_data": False}

    fields = ["source", "native_id", "url", "title", "typology", "contract", "unit_class", "offer_kind", "advertiser", "comune", "province", "price", "currency", "price_qualifier", "previous_price", "discount", "surface_m2", "surface_definition", "rooms", "bedrooms", "bathrooms", "floor", "elevator", "energy_class", "published_at", "observed_at", "first_seen_at", "last_seen_at", "price_m2", "status"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for rec in items:
        writer.writerow({k: _csv_safe(rec.get(k)) for k in fields})

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trei_listings.csv"})

