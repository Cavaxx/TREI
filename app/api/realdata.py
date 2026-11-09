"""Read-only API over the real data stores (listings + statistics).

The web app reads the JSONL stores directly: each request opens a fresh store
instance, so concurrent writers are always reflected and there is no divergent
second archive. Everything here is real data, never ``synthetic_demo``.
"""

from __future__ import annotations

import csv
import io
import statistics as _stats
from datetime import datetime

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


#: Convenience (vantaggio percentuale) — the FIRST usable ordering metric.
#: vantage = 100 × (1 − €/m² listing / median €/m² of comparable listings).
#: Comparables: available, residential, full-ownership (no offer_kind),
#: positive price and valid surface, same typology, surface within ±20%.
#: Zone level is used when it has ≥10 usable comparables, otherwise the whole
#: comune (same typology and band); the level used is reported per listing.
VALUE_MIN_COMPARABLES = 10
VALUE_SURFACE_BAND = 0.20


def value_scores(items: list[dict], *, min_n: int = VALUE_MIN_COMPARABLES,
                 band: float = VALUE_SURFACE_BAND) -> dict[str, dict]:
    """Compute a ``{key: score dict}`` for EVERY input record.

    Eligible comparables are available residential full-ownership listings with
    a positive price and a valid surface. Anything else (auctions, quotas,
    multi-ownership, bare ownership, or missing/invalid price or surface) gets
    ``vantage_pct=None`` plus a readable reason, and is never used as a
    comparable. A listing never counts as its own comparable.
    """

    def _eligible(r: dict) -> bool:
        return ((r.get("unit_class") or "") == "residenziale"
                and not r.get("offer_kind")
                and isinstance(r.get("price"), (int, float)) and r["price"] > 0
                and isinstance(r.get("surface_m2"), (int, float)) and r["surface_m2"] > 0)

    universe = [r for r in items if _eligible(r)]
    by_zone: dict[tuple, list[dict]] = {}
    by_comune: dict[tuple, list[dict]] = {}
    for r in universe:
        comune = (r.get("comune") or "").lower()
        zona = (r.get("zone") or "").strip().lower()
        typ = (r.get("typology") or "").lower()
        by_zone.setdefault((comune, zona, typ), []).append(r)
        by_comune.setdefault((comune, typ), []).append(r)

    def _comparable_pm2(pool: list[dict], rec: dict) -> list[float]:
        s = rec["surface_m2"]
        return [r["price"] / r["surface_m2"] for r in pool
                if r.get("key") != rec.get("key")
                and abs(r["surface_m2"] - s) / s <= band]

    excluded_reason = ("fuori dal perimetro del confronto (asta, quota, "
                       "multiproprietà, nuda proprietà o dati mancanti)")
    out: dict[str, dict] = {}
    for rec in items:
        key = rec.get("key") or ""
        if not _eligible(rec):
            out[key] = {
                "vantage_pct": None,
                "median_eur_m2": None,
                "comparables": None,
                "level": None,
                "band": f"±{int(band * 100)}%",
                "min_comparables": min_n,
                "reason": excluded_reason,
            }
            continue
        comune = (rec.get("comune") or "").lower()
        zona = (rec.get("zone") or "").strip().lower()
        typ = (rec.get("typology") or "").lower()
        zone_pool = by_zone.get((comune, zona, typ), [])
        vals = _comparable_pm2(zone_pool, rec)
        level = "zona"
        if len(vals) < min_n:
            comune_pool = by_comune.get((comune, typ), [])
            vals = _comparable_pm2(comune_pool, rec)
            level = "comune"
        if len(vals) < min_n:
            out[key] = {
                "vantage_pct": None,
                "median_eur_m2": None,
                "comparables": len(vals),
                "level": level,
                "band": f"±{int(band * 100)}%",
                "min_comparables": min_n,
                "reason": f"campione insufficiente: {len(vals)} comparabili utilizzabili (minimo {min_n})",
            }
            continue
        median = _stats.median(vals)
        pm2 = rec["price"] / rec["surface_m2"]
        out[key] = {
            "vantage_pct": round(100 * (1 - pm2 / median), 2),
            "median_eur_m2": round(median, 2),
            "comparables": len(vals),
            "level": level,
            "band": f"±{int(band * 100)}%",
            "min_comparables": min_n,
            "reason": None,
        }
    return out



def _listings_query(*, comune=None, zone=None, source=None, typology=None,
                    unit_class=None, availability=None, price_min=None,
                    price_max=None, surface_min=None, surface_max=None,
                    sort: str = "observed_at", direction: str = "desc",
                    with_scores: bool = True) -> list[dict]:
    """Filter, score and sort the whole matching set (no pagination here)."""
    status = _status_map()
    out = []
    for rec in _listings():
        if comune and (rec.get("comune") or "").lower() != comune.lower():
            continue
        if zone and (zone or "").strip().lower() not in (
                (rec.get("zone") or "").lower(), (rec.get("comune") or "").lower()):
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

    scores = value_scores(out) if with_scores else {}
    for item in out:
        sc = scores.get(item.get("key") or "")
        if sc:
            item["vantage_pct"] = sc["vantage_pct"]
            item["median_eur_m2"] = sc["median_eur_m2"]
            item["comparables"] = sc["comparables"]
            item["comparison_level"] = sc["level"]
            item["comparison_reason"] = sc["reason"]
        else:
            item["vantage_pct"] = None
            item["median_eur_m2"] = None
            item["comparables"] = None
            item["comparison_level"] = None
            item["comparison_reason"] = ("fuori perimetro del confronto "
                                         "(es. asta, quota, multiproprietà o dati mancanti)")

    desc = direction != "asc"

    def _tie(r: dict) -> tuple:
        #: stable tie-break independent of direction, by source then id.
        return (r.get("source") or "", r.get("native_id") or "")

    def _order(value_of) -> None:
        #: missing values ALWAYS last in both directions; only the non-missing
        #: part is reversed, so ties and missing records stay deterministic.
        present = [r for r in out if value_of(r) is not None]
        missing = [r for r in out if value_of(r) is None]
        present.sort(key=lambda r: (value_of(r), _tie(r)), reverse=desc)
        missing.sort(key=_tie)
        out[:] = present + missing

    if sort == "value_score":
        _order(lambda r: r.get("vantage_pct"))
    elif sort == "zone":

        def _zone_value(r: dict):
            comune = (r.get("comune") or "").strip().lower()
            if not comune:
                return None
            return (comune, (r.get("zone") or "").strip().lower())

        _order(_zone_value)
    elif sort == "surface":
        #: the sort key is ``surface`` but the record field is ``surface_m2``.
        _order(lambda r: r.get("surface_m2"))
    else:  # observed_at, comune, price, price_m2 and other fields
        _order(lambda r: r.get(sort))
    return out


@router.get("/listings")
def list_listings(
    comune: str | None = None,
    zone: str | None = None,
    source: str | None = None,
    typology: str | None = None,
    unit_class: str | None = None,
    availability: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    surface_min: float | None = None,
    surface_max: float | None = None,
    sort: str = Query("observed_at",
                      pattern="^(observed_at|price|price_m2|comune|surface|zone|value_score)$"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    out = _listings_query(
        comune=comune, zone=zone, source=source, typology=typology,
        unit_class=unit_class, availability=availability, price_min=price_min,
        price_max=price_max, surface_min=surface_min, surface_max=surface_max,
        sort=sort, direction=direction,
    )
    total = len(out)
    page = out[offset : offset + limit]
    summary = {}
    for st in ("available", "to_reconfirm", "confirmed_unavailable", "uncertain"):
        summary[st] = sum(1 for r in out if r.get("status") == st)
    return {
        "count": total,
        "limit": limit,
        "offset": offset,
        "from": offset + 1 if total else 0,
        "to": min(offset + limit, total),
        "sort": sort,
        "direction": direction,
        "items": page,
        "status_breakdown": summary,
        "status_labels": STATUS_LABELS,
        "demo_data": False,
    }


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


@router.get("/health")
def archive_health() -> dict:
    """Distinguish: archive missing, unreadable, valid-empty, or ready."""
    if not LISTINGS_PATH.parent.exists():
        return {"status": "missing_dir", "message":
                "directory dati assente: verificare il mount ../data:/app/data:ro "
                "nel servizio api (docker-compose) o il percorso del job cron", "demo_data": False}
    if not LISTINGS_PATH.exists():
        return {"status": "missing_files", "message":
                "listings.jsonl assente: verificare il mount ../data:/app/data:ro "
                "o eseguire il job di acquisizione (app.scheduler.daily)", "demo_data": False}
    try:
        listings = ListingStore(LISTINGS_PATH).current()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unreadable", "message": f"archivio illeggibile: {exc}", "demo_data": False}
    obs_ok = True
    obs_count = 0
    try:
        obs_count = len(ObservationStore(OBSERVATIONS_PATH).all())
    except Exception:  # noqa: BLE001
        obs_ok = False
    return {
        "status": "empty" if not listings else "ok",
        "listings_registered": len(listings),
        "observations_active": obs_count if obs_ok else None,
        "observations_readable": obs_ok,
        "demo_data": False,
    }


@router.get("/listings/{native_id}/analysis")
def listing_analysis(native_id: str) -> dict:
    """Convenience breakdown + TREI context indicators for one listing."""
    store = ListingStore(LISTINGS_PATH)
    history = store.history(native_id)
    if not history:
        raise HTTPException(status_code=404, detail="listing not found")
    current = history[-1]
    key = current.get("key") or ""
    #: read the listings store and compute the status map ONCE — recomputing
    #: them per listing (as a comprehension over ``_listings()`` would) is
    #: O(n × file) and freezes the request.
    all_listings = _listings()
    status_map = _status_map()
    status = status_map.get(key, "uncertain")
    scores = value_scores([dict(r) for r in all_listings])
    sc = scores.get(key)
    #: verified price reductions = consecutive content versions with a lower
    #: numeric price (parser revisions that ADD a previously missing price
    #: are NOT reductions).
    cuts = []
    prev = None
    for h in history:
        p = h.get("price")
        if isinstance(p, (int, float)) and isinstance(prev, (int, float)) and p < prev:
            cuts.append({"from": prev, "to": p, "at": h.get("observed_at")})
        if isinstance(p, (int, float)):
            prev = p
    first_seen = min((h.get("observed_at") or "") for h in history)
    last_seen = max((h.get("observed_at") or "") for h in history)
    obs = [r for r in _observations() if not r.get("superseded")]
    families = [r for r in obs if r.get("indicator") == "households"]
    rates = [r for r in obs if r.get("indicator") == "mortgage_rate_new_loans"]
    hicp = [r for r in obs if r.get("indicator") == "hicp_index"]
    sold = [r for r in obs if r.get("indicator") == "compravendite_residenziali"]
    context = {
        "offer_depth": {
            "available": status == "available",
            "source_available_total": sum(
                1 for r in all_listings
                if r.get("source") == current.get("source")
                and status_map.get(r.get("key") or "") == "available"),
            "note": "profondità dell'offerta osservata per la fonte (non il mercato)",
        },
        "permanence_days": {
            "first_seen_at": first_seen,
            "last_seen_at": last_seen,
            "value": (None if not first_seen else
                      max(0, (datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                              - datetime.fromisoformat(first_seen.replace("Z", "+00:00"))).days)),
            "note": "permanenza = giorni dalla prima osservazione; la scomparsa NON è una vendita",
        },
        "verified_price_cuts": {"count": len(cuts), "cuts": cuts[-10:],
                                "note": "solo riduzioni di prezzo tra versioni osservate; "
                                        "le revisioni del parser CaseTrentine (prezzo aggiunto) sono escluse"},
        "households": {
            "available": bool(families),
            "count": len(families),
            "periods": sorted({r.get("period") for r in families})[:3] + ["…"] if len(families) > 3 else sorted({r.get("period") for r in families}),
            "geo": "comunità di valle + Provincia (ISPAT)",
            "source": "ISPAT",
        },
        "compravendite_residenziali": {
            "available": bool(sold),
            "count": len(sold),
            "periods": sorted({r.get("period") for r in sold}),
            "geo": "provincia ITH20",
            "source": "ISPAT (tav. V.07, Fonte Istat)",
        },
        "mortgage_rates": {
            "available": bool(rates),
            "last_period": max((r.get("period") for r in rates), default=None),
            "unit": "percent_per_annum (AAR/NDER, non TAEG)",
            "source": "BCE MIR.M.IT.B.A2C.A.R.A.2250.EUR.N",
        },
        "inflation": {
            "available": bool(hicp),
            "last_period": max((r.get("period") for r in hicp), default=None),
            "unit": "IPCA/HICP base 2025=100",
            "source": "Eurostat prc_hicp_minr",
        },
    }
    return {
        "native_id": native_id,
        "status": status,
        "offer_kind": current.get("offer_kind") or "",
        "convenience": sc,
        "price_m2": _price_m2(current),
        "context": context,
        "note": "la convenienza usa SOLO prezzi chiesti e mediana dei comparabili; "
                "gli altri parametri forniscono contesto, non entrano nel calcolo",
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
    zone: str | None = None,
    source: str | None = None,
    unit_class: str | None = None,
    availability: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    surface_min: float | None = None,
    surface_max: float | None = None,
    sort: str = Query("observed_at",
                      pattern="^(observed_at|price|price_m2|comune|surface|zone|value_score)$"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    format: str = Query("csv", pattern="^(csv|json)$"),
) -> object:
    #: export reflects the SAME filters and ordering of every matching row
    #: (no fixed 500-row cap: the page cap applies to the UI table only).
    items = _listings_query(
        comune=comune, zone=zone, source=source, unit_class=unit_class,
        availability=availability, price_min=price_min, price_max=price_max,
        surface_min=surface_min, surface_max=surface_max, sort=sort,
        direction=direction,
    )
    if format == "json":
        return {"count": len(items), "sort": sort, "direction": direction,
                "items": items, "demo_data": False}

    fields = ["source", "native_id", "url", "title", "typology", "contract", "unit_class", "offer_kind", "advertiser", "comune", "province", "zone", "price", "currency", "price_qualifier", "previous_price", "discount", "surface_m2", "surface_definition", "rooms", "bedrooms", "bathrooms", "floor", "elevator", "energy_class", "published_at", "observed_at", "first_seen_at", "last_seen_at", "price_m2", "vantage_pct", "median_eur_m2", "comparables", "comparison_level", "status"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for rec in items:
        writer.writerow({k: _csv_safe(rec.get(k)) for k in fields})

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trei_listings.csv"})

