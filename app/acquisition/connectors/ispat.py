"""ISPAT annuario connector (Kernel browser, Playwright Execution).

The annuario is ASP.NET WebForms with a cookieless session: the index page must be
loaded first (it issues the ``(S(...))`` session redirect), and then
``tavola.aspx?idt=...`` renders the table only inside that same session. A plain
HTTP GET of the table page answers "sessione scaduta!" (session expired), so a
browser is genuinely required for this source — but the router still prefers HTTP
whenever a direct export exists for other sources.

Navigation/extraction runs as JavaScript in the remote VM; the parsing is
deterministic Python (pure functions, unit-testable offline).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from app.acquisition.contracts import Observation
from app.browser import (
    BrowserFailure,
    BrowserProviderError,
    BrowserResult,
    KernelBrowserProvider,
    KernelSession,
)
from app.core.versions import PARSER_VERSION, SCHEMA_VERSION

ANNUARIO_INDEX = "https://statweb.provincia.tn.it/annuario/"

#: Tables acquired in the pilot.
PILOT_TABLES = {
    "5.07": {"name": "V.07", "title": "Compravendite immobiliari e mutui (2000-2023)"},
    "1.26": {"name": "I.26", "title": "Numero di famiglie per comunita' di valle (1990-2024)"},
}

#: Province of Trento (NUTS-3).
PROVINCE_CODE = "ITH20"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def grid_js(idts: list[str], index: str = ANNUARIO_INDEX) -> str:
    """JS executed in the browser VM: navigate and extract the biggest table on
    each ``tavola.aspx?idt=...`` page as a 2-D array of cell text (plus its HTML)."""
    idt_list = json.dumps(idts)
    return (
        "await page.goto(" + json.dumps(index) + ");"
        "await page.waitForLoadState('domcontentloaded');"
        "await page.waitForTimeout(1200);"
        "const res = {};"
        "for (const idt of " + idt_list + ") {"
        "  await page.goto(new URL('tavola.aspx?idt=' + idt, page.url()).toString());"
        "  await page.waitForLoadState('domcontentloaded');"
        "  await page.waitForTimeout(1200);"
        "  const cell = await page.evaluate(() => {"
        "    let best = null;"
        "    for (const t of document.querySelectorAll('table')) {"
        "      const n = t.rows.length;"
        "      if (!best || n > best.n) best = {n, t};"
        "    }"
        "    if (!best) return {grid: [], html: ''};"
        "    const grid = [];"
        "    for (const tr of best.t.rows) {"
        "      const row = [];"
        "      for (const c of tr.cells) row.push((c.textContent||'').trim().replace(/\\\\s+/g,' '));"
        "      grid.push(row);"
        "    }"
        "    return {grid, html: best.t.outerHTML};"
        "  });"
        "  res[idt] = cell;"
        "}"
        "return res;"
    )


def fetch_tables(
    provider: KernelBrowserProvider,
    session: KernelSession,
    idts: list[str],
) -> dict:
    result: BrowserResult = provider.execute(session, grid_js(idts))
    if not result.success:
        raise BrowserProviderError(
            BrowserFailure.FORMAT_CHANGED,
            f"ISPAT extraction failed: {result.error or result.stderr}",
        )
    data = result.result
    if not isinstance(data, dict):
        raise BrowserProviderError(
            BrowserFailure.FORMAT_CHANGED,
            "ISPAT extraction returned no table data",
        )
    return data


def _parse_it_number(raw: str) -> float | None:
    """Italian number format: '.' is thousands, ',' is decimal."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    text = text.replace(".", "").replace(",", ".").replace(" ", "")
    try:
        return float(text)
    except ValueError:
        return None


def _year_rows(grid: list[list[str]]) -> list[list[str]]:
    return [row for row in grid if row and re.fullmatch(r"\d{4}", (row[0] or "").strip())]


#: Parser version for the ISPAT connector. Bumped whenever the column mapping or
#: definitions change, so lineage can tell a corrected record from an old one.
ISPAT_PARSER_VERSION = "ispat-0.2.0"


def parse_v07(grid: list[list[str]], *, source: str, run_id: str) -> list[Observation]:
    """Parse V.07 "Compravendite immobiliari e mutui".

    Header (two rows):
      Anni (rowspan 2) | Compravendite (colspan 2) | Mutui (rowspan 2)
                        | abitazione ed accessori   | uso economico

    Columns per year row: year, compravendite residenziali (abitazione ed
    accessori), compravendite ad uso economico, mutui con ipoteca immobiliare.
    These are *units* (unita' immobiliari) transacted and *number* of mortgages,
    not notarial deeds / NTN.
    """
    observations: list[Observation] = []
    for row in _year_rows(grid):
        year = row[0].strip()
        if len(row) < 4:
            continue
        values = [_parse_it_number(row[i]) for i in range(1, 4)]
        if any(v is None for v in values):
            continue
        specs = [
            (
                "compravendite_residenziali",
                "ISPAT_COMPRAVENDITE",
                "Compravendite di unita' immobiliari ad uso abitazione ed accessori (tav. V.07, Fonte Istat)",
                "unita_immobiliari",
                values[0],
            ),
            (
                "compravendite_economiche",
                "ISPAT_COMPRAVENDITE",
                "Compravendite di unita' immobiliari ad uso economico: artigianale, commerciale, industriale, ufficio, rurale (tav. V.07, Fonte Istat)",
                "unita_immobiliari",
                values[1],
            ),
            (
                "mutui",
                "ISPAT_MUTUI",
                "Mutui con costituzione di ipoteca immobiliare (tav. V.07, Fonte Istat)",
                "count",
                values[2],
            ),
        ]
        for indicator, series, definition, unit, value in specs:
            observations.append(
                Observation(
                    source=source,
                    source_url=ANNUARIO_INDEX,
                    retrieved_at=_now_iso(),
                    period=year,
                    territory_code=PROVINCE_CODE,
                    geo_level="province",
                    indicator=indicator,
                    value=value,
                    unit=unit,
                    definition=definition,
                    definition_version="V.07 (Fonte: Istat)",
                    content_hash="",
                    parser_version=ISPAT_PARSER_VERSION,
                    schema_version=SCHEMA_VERSION,
                    run_id=run_id,
                    quality="ok",
                    row_ref=f"tav. V.07 riga {year}",
                    series=series,
                )
            )
    return observations


def parse_i26(grid: list[list[str]], *, source: str, run_id: str) -> list[Observation]:
    """Parse I.26 "Numero di famiglie per comunita' di valle".

    The rendered view shows a subset of years. Only the columns actually present
    are emitted; missing years stay out of coverage rather than interpolated.
    """
    observations: list[Observation] = []
    if not grid:
        return observations
    header = [c.strip() for c in grid[0]]
    year_cols = [(i, c) for i, c in enumerate(header) if re.fullmatch(r"\d{4}", c)]
    if not year_cols:
        return observations
    for row in grid[1:]:
        if not row or not row[0].strip():
            continue
        territory = row[0].strip()
        if territory.lower() in ("fonte:", "note", "avvertenze"):
            continue
        for col_idx, year in year_cols:
            if col_idx >= len(row):
                continue
            value = _parse_it_number(row[col_idx])
            if value is None:
                continue
            observations.append(
                Observation(
                    source=source,
                    source_url=ANNUARIO_INDEX,
                    retrieved_at=_now_iso(),
                    period=year,
                    territory_code=territory,
                    geo_level="comunita_di_valle",
                    indicator="households",
                    value=value,
                    unit="count",
                    definition="Numero di famiglie per comunita' di valle (tav. I.26)",
                    definition_version="I.26",
                    content_hash="",
                    parser_version=PARSER_VERSION,
                    schema_version=SCHEMA_VERSION,
                    run_id=run_id,
                    quality="ok",
                    row_ref=f"tav. I.26 riga {territory}",
                    series="ISPAT_FAMIGLIE",
                )
            )
    return observations


def raw_content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def grid_from_html(html: str) -> list[list[str]]:
    """Reconstruct a 2-D cell grid from a stored raw ``<table>`` fragment.

    Used by the migration so corrected records are re-derived from the preserved
    raw evidence, not from hard-coded values.
    """
    from bs4 import BeautifulSoup  # noqa: PLC0415

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []
    grid: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        grid.append(cells)
    return grid

