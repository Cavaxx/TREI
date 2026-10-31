"""Eurostat HICP (IPCA) monthly index for Italy — prc_hicp_minr (JSON-stat).

Official dissemination API, TLS verified:
https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr?lang=EN&geo=IT&coicop18=TOTAL&freq=M&sinceTimePeriod=2015-01

``prc_hicp_midx`` is archived (ends 2025); ``prc_hicp_minr`` supersedes it
with ECOICOP v2 (dimension ``coicop18``, code ``TOTAL``). Units published:
I25 (index 2025=100), I15 (index 2015=100) and percentage changes. Only the
I25 index levels are stored here; percentage changes are never used in place
of levels, and the two bases are never concatenated into one series.

The JSON-stat cube is decoded with ``id``/``size``/category indexes — the
flat ``value`` order is NOT assumed to be a single time series.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from app.acquisition.contracts import Observation
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT

EUROSTAT_HICP_URL = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr"
    "?lang=EN&geo=IT&coicop18=TOTAL&freq=M&sinceTimePeriod=2015-01"
)
HICP_DEFINITION = "Indice armonizzato dei prezzi al consumo (IPCA/HICP) - Italia, totale (ECOICOP v2)"
HICP_SERIES = "EUROSTAT_HICP_IT_TOTAL_I25"
HICP_INDICATOR = "hicp_index"
#: Raw responses cached here (re-fetch every run; cache used for offline retries).
CACHE_ROOT = PROJECT_ROOT / "data" / "raw" / "eurostat" / "prc_hicp_minr"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()



def _decode_json_stat(doc: dict) -> list[dict]:
    """Decode a JSON-stat cube into {dim values..., 'value', 'status'} rows."""
    dims = doc.get("id") or []
    sizes = doc.get("size") or []
    dimensions = doc.get("dimension") or {}
    status = doc.get("status") or {}
    prov = set(status.get("p") or []) if isinstance(status, dict) else set()

    indexes: list[list[str]] = []
    for dim in dims:
        d = dimensions.get(dim) or {}
        cat = d.get("category") or {}
        if isinstance(cat, dict) and "index" in cat and isinstance(cat["index"], dict):
            order = sorted(cat["index"].items(), key=lambda kv: kv[1])
            indexes.append([k for k, _ in order])
        elif isinstance(cat, list):
            indexes.append([str(c) for c in cat])
        else:
            indexes.append(sorted(cat.keys()) if isinstance(cat, dict) else [])

    rows: list[dict] = []
    raw_values = doc.get("value")
    if isinstance(raw_values, dict):
        # JSON-stat sparse serialization: integer index keys
        pairs = sorted(((int(k), v) for k, v in raw_values.items()), key=lambda kv: kv[0])
    else:
        pairs = list(enumerate(raw_values or []))
    total = 1
    for s in sizes:
        total *= int(s)
    if total != len(pairs):
        return rows  # malformed cube: caller reports it
    coords = [0] * len(dims)
    for i, value in pairs:
        row = {dims[k]: indexes[k][coords[k]] if indexes[k] else "" for k in range(len(dims))}
        row["value"] = value
        row["status"] = "p" if i in prov else ""
        rows.append(row)
        for k in range(len(dims) - 1, -1, -1):
            coords[k] += 1
            if coords[k] < int(sizes[k]):
                break
            coords[k] = 0
    return rows


def fetch_hicp(timeout: float = 60.0) -> list[dict]:
    """Download and decode the I25 monthly HICP index for Italy."""
    resp = httpx.get(EUROSTAT_HICP_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    doc = resp.json()
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    (CACHE_ROOT / f"hicp_{stamp}.json").write_text(json.dumps(doc), encoding="utf-8")

    rows = _decode_json_stat(doc)
    if not rows:
        raise ValueError("decoded HICP cube is empty (size/value mismatch)")
    out = []
    for r in rows:
        if r.get("unit") != "I25" or r.get("freq") != "M" or r.get("geo") != "IT":
            continue
        if r.get("coicop18") != "TOTAL":
            continue
        try:
            value = float(r["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if value <= 0:
            continue
        out.append({"period": str(r.get("time")), "value": value, "status": r.get("status") or ""})
    # uniqueness month-wise (a malformed cube must never silently drop rows)
    seen: dict[str, dict] = {}
    for r in out:
        if r["period"] in seen:
            raise ValueError(f"duplicate HICP period {r['period']!r} in decoded cube")
        seen[r["period"]] = r
    return sorted(seen.values(), key=lambda r: r["period"])



def store_hicp(store_path=None) -> dict:
    store = ObservationStore(store_path or PROJECT_ROOT / "data" / "observations.jsonl")
    rows = fetch_hicp()
    run_id = "eurostat-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    stored = revised = provisional = 0
    for r in rows:
        obs = Observation(
            source="EUROSTAT",
            source_url=EUROSTAT_HICP_URL,
            retrieved_at=_now_iso(),
            period=r["period"],
            territory_code="IT",
            geo_level="national",
            indicator=HICP_INDICATOR,
            value=r["value"],
            unit="index_base2025",
            definition=HICP_DEFINITION,
            definition_version="prc_hicp_minr|coicop18=TOTAL|I25",
            series=HICP_SERIES,
            run_id=run_id,
            quality="provisional" if r.get("status") == "p" else "ok",
        )
        res = store.upsert(obs)
        if res.get("stored"):
            stored += 1
        if res.get("revised"):
            revised += 1
        if r.get("status") == "p":
            provisional += 1
    periods = sorted({r.get("period") for r in store.all() if r.get("indicator") == HICP_INDICATOR})
    return {
        "series": HICP_SERIES,
        "indicator": HICP_INDICATOR,
        "rows": len(rows),
        "stored": stored,
        "revised": revised,
        "provisional": provisional,
        "first_period": periods[0] if periods else None,
        "last_period": periods[-1] if periods else None,
        "missing_months": _missing_months(periods),
        "run_id": run_id,
        "status": "ok",
    }


def _missing_months(periods: list[str]) -> list[str]:
    from datetime import timedelta

    if len(periods) < 2:
        return []
    out = []
    cur = datetime.strptime(periods[0], "%Y-%m")
    end = datetime.strptime(periods[-1], "%Y-%m")
    have = set(periods)
    while cur < end:
        label = cur.strftime("%Y-%m")
        if label not in have:
            out.append(label)
        cur = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
    return out
