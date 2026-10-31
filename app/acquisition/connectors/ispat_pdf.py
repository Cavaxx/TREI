"""ISPAT annuario 2014 (ed. 2015) PDF parsers: households I.28 and V.07.

Official PDF:
https://statweb.provincia.tn.it/pubblicazioniHTML/.../Annuario statistico 2014/AnnStat2014.pdf
(9 MB, downloaded and cached under data/raw/ispat/)

* TAV. I.28 ``Numero delle famiglie per comunità di valle (1990-2014)`` —
  page 84 of the PDF — carries 1990, 2000, 2005, 2010, 2013, 2014 for the
  16 valley communities + Provincia. Territory names match the stored I.26
  series 1:1; only the missing years are imported (2000, 2005, 2013, 2014).
  Overlapping years (1990, 2010) are compared against the store and any
  edition difference is reported, never silently overwritten.
* V.07 ``Compravendite immobiliari e mutui`` — looked up by title; the
  edition-year definitions are kept explicit per observation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from app.acquisition.contracts import Observation
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT

PDF_PATH = PROJECT_ROOT / "data" / "raw" / "ispat" / "AnnStat2014.pdf"
PDF_URL = (
    "https://statweb.provincia.tn.it/pubblicazioniHTML/Annuari%20e%20altre%20pubblicazioni"
    "%20di%20carattere%20generale/annuari%20statistici/Annuario%20statistico%202014/AnnStat2014.pdf"
)
FAMIGLIE_DEFINITION = "Numero di famiglie per comunità di valle (tav. I.28, Annuario statistico 2014, ed. 2015, Fonte Istat-ISPAT)"
PROVINCE_CODE = "ITH20"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _it_num(raw: str) -> float | None:
    try:
        return float(raw.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def parse_i28_2014(pdf_path: Path | None = None) -> list[dict]:
    """Parse TAV. I.28 (page 84): rows {territorio: {anno: valore}}."""
    import pypdf

    reader = pypdf.PdfReader(pdf_path or PDF_PATH)
    text = reader.pages[84].extract_text() or ""
    rows = []
    for line in text.splitlines():
        m = re.match(
            r"^(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$",
            line,
        )
        if not m:
            continue
        territorio = m.group(1).strip()
        if territorio in {"Comunità di Valle", "Territorio", "Comunita' di Valle"}:
            continue  # header row, not a territory
        valori = [_it_num(v) for v in m.groups()[1:]]
        if any(v is None for v in valori):
            continue
        rows.append({
            "territorio": territorio,
            "values": dict(zip(("1990", "2000", "2005", "2010", "2013", "2014"), valori)),
        })
    return rows


def store_i28_2014(store_path=None) -> dict:
    store = ObservationStore(store_path or PROJECT_ROOT / "data" / "observations.jsonl")
    rows = parse_i28_2014()
    run_id = "ispat-pdf-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    existing = {}
    for r in store.all():
        if r.get("indicator") == "households":
            existing.setdefault((r.get("territory_code"), r.get("period")), []).append(r.get("value"))

    stored = 0
    edition_diffs: list[dict] = []
    for row in rows:
        territorio = row["territorio"]
        for year, value in row["values"].items():
            prior = existing.get((territorio, year))
            if prior:
                if abs(prior[-1] - value) > 0.5:
                    edition_diffs.append({"territorio": territorio, "year": year,
                                          "stored": prior[-1], "edition_2014": value})
                continue  # overlapping years are compared, never overwritten
            obs = Observation(
                source="ISPAT",
                source_url=PDF_URL,
                retrieved_at=_now_iso(),
                period=year,
                territory_code=territorio,
                geo_level="comunita_di_valle",
                indicator="households",
                value=value,
                unit="count",
                definition=FAMIGLIE_DEFINITION,
                definition_version="I.28|Annuario statistico 2014 (ed. 2015)",
                published_at="2015",
                series="ISPAT_FAMIGLIE",
                run_id=run_id,
                quality="ok",
                row_ref=f"tav. I.28 riga {territorio}",
            )
            if store.upsert(obs).get("stored"):
                stored += 1
    return {
        "rows": len(rows),
        "stored": stored,
        "years_imported": ["2000", "2005", "2013", "2014"],
        "edition_diffs": edition_diffs,
        "run_id": run_id,
    }


def parse_v7_2014(pdf_path: Path | None = None) -> list[dict]:
    """Parse TAV. V.7 (annuario 2014, PDF page index 206): rows year -> 3 values.

    Columns: compravendite residenziali (abitazione ed accessori),
    compravendite ad uso economico, mutui con ipoteca immobiliare.
    """
    import pypdf

    reader = pypdf.PdfReader(pdf_path or PDF_PATH)
    text = reader.pages[206].extract_text() or ""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^(\d{4})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$", line.strip())
        if not m:
            continue
        vals = [_it_num(v) for v in m.groups()[1:]]
        if any(v is None for v in vals):
            continue
        rows.append({"year": m.group(1), "residenziali": vals[0],
                     "economiche": vals[1], "mutui": vals[2]})
    return rows


def store_v7_2014(store_path=None) -> dict:
    """Import the V.7 years missing from the store; overlapping years are
    compared across editions and never overwritten."""
    store = ObservationStore(store_path or PROJECT_ROOT / "data" / "observations.jsonl")
    rows = parse_v7_2014()
    existing = {}
    for r in store.all():
        if r.get("indicator") in ("compravendite_residenziali", "compravendite_economiche", "mutui"):
            existing.setdefault((r.get("indicator"), r.get("period")), []).append(r.get("value"))

    run_id = "ispat-pdf-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    stored = 0
    diffs: list[dict] = []
    specs = [
        ("compravendite_residenziali", "residenziali",
         "Compravendite di unita' immobiliari ad uso abitazione ed accessori (tav. V.7, Annuario statistico 2014, ed. 2015, Fonte Istat)",
         "unita_immobiliari", "ISPAT_COMPRAVENDITE"),
        ("compravendite_economiche", "economiche",
         "Compravendite di unita' immobiliari ad uso economico: artigianale, commerciale, industriale, ufficio, rurale (tav. V.7, Annuario statistico 2014, ed. 2015, Fonte Istat)",
         "unita_immobiliari", "ISPAT_COMPRAVENDITE"),
        ("mutui", "mutui",
         "Mutui con costituzione di ipoteca immobiliare (tav. V.7, Annuario statistico 2014, ed. 2015, Fonte Istat)",
         "count", "ISPAT_MUTUI"),
    ]
    for row in rows:
        for indicator, field, definition, unit, series in specs:
            value = row[field]
            prior = existing.get((indicator, row["year"]))
            if prior:
                if abs(prior[-1] - value) > 0.5:
                    diffs.append({"indicator": indicator, "year": row["year"],
                                  "stored": prior[-1], "edition_2014": value})
                continue  # overlapping years are compared, never overwritten
            obs = Observation(
                source="ISPAT",
                source_url=PDF_URL,
                retrieved_at=_now_iso(),
                period=row["year"],
                territory_code=PROVINCE_CODE,
                geo_level="province",
                indicator=indicator,
                value=value,
                unit=unit,
                definition=definition,
                definition_version="V.7|Annuario statistico 2014 (ed. 2015)",
                published_at="2015",
                series=series,
                run_id=run_id,
                quality="ok",
                row_ref=f"tav. V.7 riga {row['year']}",
            )
            if store.upsert(obs).get("stored"):
                stored += 1
    return {"rows": len(rows), "stored": stored, "edition_diffs": diffs, "run_id": run_id}

