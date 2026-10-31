"""ISTAT NIC index from the official communiqué attachment (xlsx).

Source: official press-release attachment (September 2026):
https://www.istat.it/wp-content/uploads/2026/10/serie09_2026.xlsx
(communicated at https://www.istat.it/comunicato-stampa/prezzi-al-consumo-settembre-2026/)

``Tabella_6`` carries the NIC general index (including tobacco): annual
*means* for the historical series and a monthly window (September 2024 –
September 2026). Only the monthly levels are imported; annual means are never
stored as monthly observations. The sheet exposes base 2015=100 up to
December 2025 and base 2025=100 from January 2026, with the official linking
coefficients. Levels of different bases are never concatenated: each
observation keeps its own base in ``unit`` and the coefficients are recorded
in the metadata. This file does NOT contain the full monthly series from
2015: the remaining months are documented as a gap, not invented.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.acquisition.contracts import Observation
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT

ISTAT_NIC_XLSX_URL = "https://www.istat.it/wp-content/uploads/2026/10/serie09_2026.xlsx"
ISTAT_NIC_COMUNICATO = "https://www.istat.it/comunicato-stampa/prezzi-al-consumo-settembre-2026/"
NIC_DEFINITION = "Indice nazionale dei prezzi al consumo per l'intera collettività (NIC), compresi i tabacchi - Italia"
NIC_SERIES = "ISTAT_NIC"
NIC_INDICATOR = "cpi_nic_index"
CACHE_PATH = PROJECT_ROOT / "data" / "raw" / "istat" / "nic" / "serie09_2026.xlsx"

_MESI = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def download_nic_xlsx(timeout: float = 60.0) -> Path:
    if CACHE_PATH.exists() and CACHE_PATH.stat().st_size > 10_000:
        return CACHE_PATH
    resp = httpx.get(ISTAT_NIC_XLSX_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_bytes(resp.content)
    return CACHE_PATH


def parse_nic_monthly(path: Path | None = None) -> dict:
    """Parse Tabella_6: return {months, coefficients, annual_means_ignored}."""
    import openpyxl

    wb = openpyxl.load_workbook(path or download_nic_xlsx(), read_only=True, data_only=True)
    ws = wb["Tabella_6"]
    rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]

    base = "index_base1995"
    coefficients: dict[str, str] = {}
    months: list[dict] = []
    annual_ignored = 0
    current_year: str | None = None
    for row in rows:
        cells = [(str(c).strip() if c is not None else "") for c in row]
        if not any(cells):
            continue
        base_m = re.match(r"Base\s+(\d{4})=100", cells[2] or "")
        if base_m:
            base = f"index_base{base_m.group(1)}"
            continue
        coeff = re.match(r"Coefficiente di raccordo", cells[0] or cells[1] or "")
        if coeff and len(cells) > 2 and cells[2]:
            coefficients[base] = cells[2]
            continue
        if re.fullmatch(r"\d{4}", cells[0] or ""):
            if cells[1] == "" and cells[2]:
                # year row with an index value: ANNUAL MEAN (never monthly)
                current_year = cells[0]
                annual_ignored += 1
                continue
            if cells[1] == "" and not cells[2]:
                # year header of the monthly window (no value in the index column)
                current_year = cells[0]
                continue
        if cells[0] == "" and (cells[1] or "").lower() in _MESI and current_year and len(cells) > 2 and cells[2]:
            # monthly row inside the recent window
            month = _MESI[cells[1].lower()]
            try:
                value = float(cells[2])
            except ValueError:
                continue
            months.append({
                "period": f"{current_year}-{month:02d}",
                "value": value,
                "unit": base,
                "row_ref": f"{current_year} {cells[1]}",
            })
    # monthly rows are unique by (period, unit): a duplicate means the sheet
    # layout changed and the parser must be reviewed, never silently merged.
    seen: set[tuple] = set()
    for m in months:
        pair = (m["period"], m["unit"])
        if pair in seen:
            raise ValueError(f"duplicate NIC month {pair} in Tabella_6 layout")
        seen.add(pair)
    months = sorted(months, key=lambda m: m["period"])
    return {
        "months": months,
        "coefficients": coefficients,
        "annual_means_ignored": annual_ignored,
        "sheet": "Tabella_6",
    }


def store_nic_from_xlsx(store_path=None) -> dict:
    store = ObservationStore(store_path or PROJECT_ROOT / "data" / "observations.jsonl")
    parsed = parse_nic_monthly()
    months = parsed["months"]
    run_id = "istat-xlsx-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    stored = revised = 0
    for m in months:
        obs = Observation(
            source="ISTAT",
            source_url=ISTAT_NIC_COMUNICATO,
            retrieved_at=_now_iso(),
            period=m["period"],
            territory_code="IT",
            geo_level="national",
            indicator=NIC_INDICATOR,
            value=m["value"],
            unit=m["unit"],
            definition=NIC_DEFINITION,
            definition_version="Tabella_6|serie09_2026.xlsx|raccordo=" + "+".join(
                f"{k}={v}" for k, v in parsed["coefficients"].items()),
            published_at="2026-10",
            series=NIC_SERIES,
            run_id=run_id,
            quality="ok",
            row_ref=m["row_ref"],
        )
        res = store.upsert(obs)
        if res.get("stored"):
            stored += 1
        if res.get("revised"):
            revised += 1
    nic_periods = sorted({r.get("period") for r in store.all()
                          if r.get("indicator") == NIC_INDICATOR})
    return {
        "series": NIC_SERIES,
        "indicator": NIC_INDICATOR,
        "rows": len(months),
        "stored": stored,
        "revised": revised,
        "first_period": nic_periods[0] if nic_periods else None,
        "last_period": nic_periods[-1] if nic_periods else None,
        "annual_means_ignored": parsed["annual_means_ignored"],
        "coefficients": parsed["coefficients"],
        "note": "mensili presenti nell'allegato del comunicato di settembre 2026; "
                "la serie mensile completa dal 2015 va completata dagli allegati d'archivio",
        "run_id": run_id,
        "status": "ok",
    }


