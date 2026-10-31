"""Macro-economic series acquisition (ECB, ISTAT) via official SDMX/CSV APIs.

Stores each observation with full metadata (key, unit, geography, periodicity,
definition, extraction time). These are national/euro-area series used as
*context*, never presented as Trentino-specific or personal offers.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import httpx

from app.acquisition.contracts import Observation
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT

ECB_MIR_KEY = "MIR.M.IT.B.A2C.A.R.A.2250.EUR.N"
#: SDMX dataflow is MIR; the resource key excludes the dataflow prefix.
ECB_MIR_URL = "https://data-api.ecb.europa.eu/service/data/MIR/M.IT.B.A2C.A.R.A.2250.EUR.N?format=csvdata&startPeriod=2015-01"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def fetch_ecb_mir(*, start_period: str = "2015-01", timeout: float = 40.0) -> list[dict]:
    """Return monthly ECB mortgage-rate observations (period, value).

    ``start_period`` is the data portal's ``startPeriod`` parameter; the ECB
    endpoint only returns what it has, so gaps are checked separately.
    """
    resp = httpx.get(f"{ECB_MIR_URL.split('?')[0]}?format=csvdata&startPeriod={start_period}",
                     timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    out = []
    for r in rows:
        period = r.get("TIME_PERIOD", "")
        value = r.get("OBS_VALUE", "")
        flag = r.get("OBS_STATUS", "")
        if not period or value in ("", "NaN"):
            continue
        try:
            out.append({"period": period, "value": float(value), "obs_status": flag})
        except ValueError:
            continue
    return out


def _missing_months(periods: list[str]) -> list[str]:
    """Contiguous-month gaps between the first and last observed period."""
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


def store_ecb_mir(store_path=None) -> dict:
    store = ObservationStore(store_path or PROJECT_ROOT / "data" / "observations.jsonl")
    #: incremental: fetch only from the month after the last stored period.
    prior = [r for r in store.all() if r.get("indicator") == "mortgage_rate_new_loans"]
    periods = sorted({r.get("period") for r in prior if r.get("period")})
    start = "2015-01"
    if periods:
        last = datetime.strptime(periods[-1], "%Y-%m")
        nxt = (last.replace(day=1) + timedelta(days=32)).replace(day=1)
        start = nxt.strftime("%Y-%m")
    rows = fetch_ecb_mir(start_period=start)
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
    all_periods = sorted({r.get("period") for r in store.all()
                          if r.get("indicator") == "mortgage_rate_new_loans" and r.get("period")})
    return {
        "series": ECB_MIR_KEY,
        "rows": len(rows),
        "stored": stored,
        "run_id": run_id,
        "fetched_from": start,
        "first_period": all_periods[0] if all_periods else None,
        "last_period": all_periods[-1] if all_periods else None,
        "missing_months": _missing_months(all_periods),
    }


def fetch_istat_cpi(timeout: float = 40.0) -> list[dict]:
    """ISTAT NIC monthly index (levels, base 2015=100) via IstatData SDMX."""
    from app.acquisition.connectors.istat_nic import fetch_istat_nic

    return [{"period": r["period"], "value": r["value"]} for r in fetch_istat_nic()]


def store_istat_cpi(store_path=None) -> dict:
    """ISTAT NIC from the official communiqué attachment (monthly window).

    The SDMX data endpoint on esploradati failed with 404 on 8 variants
    (evidence in the 31/10 runs): do not retry the same channel. The official
    xlsx attachment is the working channel.
    """
    from app.acquisition.connectors.istat_nic_xlsx import store_nic_from_xlsx

    return store_nic_from_xlsx(store_path=store_path)


def run_macro(*, store_path=None) -> dict:
    """Fetch and store the macro series; each failure is reported separately."""
    report = {"ecb": None, "istat": None, "hicp": None}
    try:
        report["ecb"] = store_ecb_mir(store_path)
    except Exception as exc:  # noqa: BLE001
        report["ecb"] = {"status": "failed", "error": str(exc)}
    try:
        report["istat"] = store_istat_cpi(store_path)
    except Exception as exc:  # noqa: BLE001
        report["istat"] = {"status": "failed", "error": str(exc)}
    try:
        from app.acquisition.connectors.eurostat_hicp import store_hicp

        report["hicp"] = store_hicp(store_path)
    except Exception as exc:  # noqa: BLE001
        report["hicp"] = {"status": "failed", "error": str(exc)}
    return report
