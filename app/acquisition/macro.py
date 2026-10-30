"""Macro-economic series acquisition (ECB, ISTAT) via official SDMX/CSV APIs.

Stores each observation with full metadata (key, unit, geography, periodicity,
definition, extraction time). These are national/euro-area series used as
*context*, never presented as Trentino-specific or personal offers.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import httpx

from app.acquisition.contracts import Observation
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT

ECB_MIR_KEY = "MIR.M.IT.B.A2C.A.R.A.2250.EUR.N"
#: SDMX dataflow is MIR; the resource key excludes the dataflow prefix.
ECB_MIR_URL = "https://data-api.ecb.europa.eu/service/data/MIR/M.IT.B.A2C.A.R.A.2250.EUR.N?format=csvdata&startPeriod=2015-01"

#: ISTAT NIC monthly index — the SDMX dataflow/endpoint is NOT yet resolved
#: (sdmx.istat.it returned an SSL error on 2026-10-30). Left empty until a real
#: dataflow is confirmed; ``fetch_istat_cpi`` reports ``blocked`` rather than
#: guessing an endpoint.
ISTAT_CPI_URL = ""


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def fetch_ecb_mir(timeout: float = 40.0) -> list[dict]:
    """Return monthly ECB mortgage-rate observations (period, value)."""
    resp = httpx.get(ECB_MIR_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    out = []
    for r in rows:
        period = r.get("TIME_PERIOD", "")
        value = r.get("OBS_VALUE", "")
        if not period or value in ("", "NaN"):
            continue
        try:
            out.append({"period": period, "value": float(value)})
        except ValueError:
            continue
    return out


def store_ecb_mir(store_path=None) -> dict:
    store = ObservationStore(store_path or PROJECT_ROOT / "data" / "observations.jsonl")
    rows = fetch_ecb_mir()
    run_id = "ecb-" + _now_iso().replace(":", "").replace("-", "")[:14]
    stored = 0
    for r in rows:
        obs = Observation(
            source="ECB",
            source_url=ECB_MIR_URL,
            retrieved_at=_now_iso(),
            period=r["period"],
            territory_code="IT",
            geo_level="national",
            indicator="mortgage_rate_new_loans",
            value=r["value"],
            unit="percent_per_annum",
            definition="Tassi di interesse bancari sui prestiti alle famiglie per l'acquisto di abitazioni (nuove operazioni) - Italia, AAR/NDER",
            definition_version=ECB_MIR_KEY,
            series=ECB_MIR_KEY,
            run_id=run_id,
            quality="ok",
        )
        if store.upsert(obs).get("stored"):
            stored += 1
    return {"series": ECB_MIR_KEY, "rows": len(rows), "stored": stored, "run_id": run_id}


def fetch_istat_cpi(timeout: float = 40.0) -> list[dict]:
    """ISTAT NIC index: returns [] when the endpoint is not yet resolved."""
    if not ISTAT_CPI_URL:
        return []
    resp = httpx.get(ISTAT_CPI_URL, timeout=timeout, follow_redirects=True)
    if resp.status_code != 200:
        return []
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    out = []
    for r in rows:
        period = r.get("TIME_PERIOD", "") or r.get("TIME", "")
        value = r.get("OBS_VALUE", "") or r.get("OBS", "")
        if not period or value in ("", "NaN"):
            continue
        try:
            out.append({"period": period, "value": float(value)})
        except ValueError:
            continue
    return out


def store_istat_cpi(store_path=None) -> dict:
    store = ObservationStore(store_path or PROJECT_ROOT / "data" / "observations.jsonl")
    rows = fetch_istat_cpi()
    if not rows:
        return {"series": "ISTAT_NIC", "rows": 0, "stored": 0, "status": "blocked", "reason": "sdmx.istat.it SSL error 2026-10-30; endpoint not resolved"}
    run_id = "istat-" + _now_iso().replace(":", "").replace("-", "")[:14]
    stored = 0
    for r in rows:
        obs = Observation(
            source="ISTAT",
            source_url=ISTAT_CPI_URL,
            retrieved_at=_now_iso(),
            period=r["period"],
            territory_code="IT",
            geo_level="national",
            indicator="cpi_nic_index",
            value=r["value"],
            unit="index",
            definition="Indice nazionale dei prezzi al consumo per l'intera collettivita' (NIC) - Italia",
            definition_version="NIC",
            series="ISTAT_NIC",
            run_id=run_id,
            quality="ok",
        )
        if store.upsert(obs).get("stored"):
            stored += 1
    return {"series": "ISTAT_NIC", "rows": len(rows), "stored": stored, "run_id": run_id, "status": "ok"}


def run_macro(*, store_path=None) -> dict:
    """Fetch and store the two macro series; each failure is reported separately."""
    report = {"ecb": None, "istat": None}
    try:
        report["ecb"] = store_ecb_mir(store_path)
    except Exception as exc:  # noqa: BLE001
        report["ecb"] = {"status": "failed", "error": str(exc)}
    try:
        report["istat"] = store_istat_cpi(store_path)
    except Exception as exc:  # noqa: BLE001
        report["istat"] = {"status": "failed", "error": str(exc)}
    return report
