"""ISTAT NIC index connector (IstatData SDMX 2.1 REST, esploradati.istat.it).

Official documentation: https://www.istat.it/classificazioni-e-strumenti/web-services-sdmx/
Rate limit: 5 queries/minute per IP. ``IstatLimiter`` enforces a shared
minimum interval with margin (default 15 s). Every parameter below was
resolved from the official DSD/codelists on 2026-10-31 (never guessed):

* dataflow 167_744_DF_DCSP_NIC1B2015_1 = NIC mensili dal 2016 (base 2015);
* FREQ M, REF_AREA IT, DATA_TYPE 39 = NIC base 2015=100 dati mensili,
  MEASURE 4 = numeri indici, E_COICOP_REV_ISTAT 000000 = indice generale.
"""

from __future__ import annotations

import csv
import io
import threading
import time
from datetime import UTC, datetime

import httpx

from app.acquisition.contracts import Observation
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT, get_settings

ISTAT_FLOW = "167_744_DF_DCSP_NIC1B2015_1"
ISTAT_SERIES = "M.IT.39.4.000000"
ISTAT_URL = f"https://esploradati.istat.it/SDMXWS/rest/data/IT1,{ISTAT_FLOW}/{ISTAT_SERIES}"
ISTAT_METADATA = {
    "dataflow": ISTAT_FLOW,
    "definition": "Indice nazionale dei prezzi al consumo per l'intera collettività (NIC) - Italia",
    "definition_version": "DCSP_NIC1B2015",
    "base": "2015=100",
    "frequenza": "mensile",
    "territorio": "Italia",
    "note": "mensili dal 2016 (base 2015); DSD/codelist verificati il 2026-10-31",
}


class IstatLimiter:
    """Shared ISTAT rate limiter: one request per ``min_interval`` seconds."""

    _lock = threading.Lock()
    _last = 0.0

    @classmethod
    def min_interval(cls) -> float:
        return float(get_settings().istat_min_interval_seconds)

    @classmethod
    def wait(cls) -> float:
        with cls._lock:
            wait = cls.min_interval() - (time.monotonic() - cls._last)
            if wait > 0:
                time.sleep(wait)
            cls._last = time.monotonic()
            return max(0.0, wait)


def fetch_istat_nic(start_period: str = "2015-01", timeout: float = 60.0) -> list[dict]:
    """Download the monthly NIC general index (levels, base 2015=100)."""
    IstatLimiter.wait()
    url = f"{ISTAT_URL}?startPeriod={start_period}&format=csvdata"
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    rows = []
    for r in csv.DictReader(io.StringIO(resp.text)):
        period = (r.get("TIME_PERIOD") or "").strip()
        value = (r.get("OBS_VALUE") or "").strip()
        flag = (r.get("OBS_STATUS") or "").strip()
        if not period or value in ("", "NaN"):
            continue
        try:
            rows.append({"period": period, "value": float(value), "obs_status": flag})
        except ValueError:
            continue
    return rows


def store_istat_nic(store_path=None, start_period: str = "2015-01") -> dict:
    """Store the NIC monthly index with full metadata (base, provisional flags)."""
    store = ObservationStore(store_path or PROJECT_ROOT / "data" / "observations.jsonl")
    rows = fetch_istat_nic(start_period=start_period)
    run_id = "istat-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    stored = 0
    provisional = 0
    for r in rows:
        obs = Observation(
            source="ISTAT",
            source_url=ISTAT_URL,
            retrieved_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            period=r["period"],
            territory_code="IT",
            geo_level="national",
            indicator="cpi_nic_index",
            value=r["value"],
            unit="index_base2015",
            definition=ISTAT_METADATA["definition"],
            definition_version=f"{ISTAT_METADATA['definition_version']}:{ISTAT_METADATA['base']}",
            series=f"ISTAT_NIC_{ISTAT_METADATA['base'].replace('=100','')}",
            run_id=run_id,
            quality="provisional" if r.get("obs_status") == "P" else "ok",
        )
        if store.upsert(obs).get("stored"):
            stored += 1
        if r.get("obs_status") == "P":
            provisional += 1
    periods = sorted({r["period"] for r in rows})
    return {
        "series": "ISTAT_NIC_2015",
        "metadata": ISTAT_METADATA,
        "rows": len(rows),
        "stored": stored,
        "provisional": provisional,
        "first_period": periods[0] if periods else None,
        "last_period": periods[-1] if periods else None,
        "run_id": run_id,
        "status": "ok",
    }
