"""Pilot acquisition runner for the ISPAT annuario.

Opens one Kernel session, extracts the pilot tables, stores raw HTML in the data
lake, parses to observations, stores them idempotently, and always releases the
session in ``finally``. A run truncated by a limit/budget reports ``partial``.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from app.acquisition.connectors.ispat import (
    ANNUARIO_INDEX,
    PILOT_TABLES,
    fetch_tables,
    parse_i26,
    parse_v07,
)
from app.acquisition.store import ObservationStore
from app.browser import BrowserProviderError, KernelBrowserProvider, RunBudget
from app.core.config import get_settings
from app.storage.datalake import DataLake


def _run_id() -> str:
    return "ispap-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def run_pilot(*, store_path=None, budget_path=None, source: str = "ispat_provincia_tn") -> dict:
    settings = get_settings()
    budget = RunBudget.from_settings(settings)
    if budget_path is not None:
        budget.path = budget_path
        budget.load()
    provider = KernelBrowserProvider(settings=settings, budget=budget)
    store = ObservationStore(store_path)
    lake = DataLake()
    run_id = _run_id()
    started = time.monotonic()

    report: dict = {
        "run_id": run_id,
        "source": source,
        "method": "kernel_browser",
        "status": "completed",
        "failure": None,
        "pages": 0,
        "tables": {},
        "records": 0,
        "stored": 0,
        "artifacts": [],
        "coverage": {},
    }

    session = None
    try:
        session = provider.open_session(name="trei-pilot-" + uuid.uuid4().hex[:8])
        report["session_id"] = session.session_id
        report["pages"] = 1  # the index page
        data = fetch_tables(provider, session, list(PILOT_TABLES))
        report["pages"] += len(PILOT_TABLES)

        observations = []
        for idt, meta in PILOT_TABLES.items():
            cell = data.get(idt) or {}
            grid = cell.get("grid") or []
            html = cell.get("html") or ""
            if not grid:
                report["tables"][idt] = {"name": meta["name"], "rows": 0, "observations": 0, "error": "empty grid"}
                continue
            raw_ref = lake.store(
                source,
                "html",
                html,
                url=ANNUARIO_INDEX + "tavola.aspx?idt=" + idt,
                crawl_id=run_id,
            )
            parsed = parse_v07(grid, source=source, run_id=run_id) if idt == "5.07" else parse_i26(grid, source=source, run_id=run_id)
            for obs in parsed:
                obs.content_hash = raw_ref.content_hash
                obs.raw_object_id = raw_ref.raw_object_id
            report["tables"][idt] = {
                "name": meta["name"],
                "rows": len(grid),
                "observations": len(parsed),
                "artifact": raw_ref.relative_path,
            }
            report["artifacts"].append(raw_ref.relative_path)
            observations.extend(parsed)

        # Enforce the pilot record limit; a truncated run is partial.
        limit = settings.browser_max_records_per_source
        if len(observations) > limit:
            observations = observations[:limit]
            report["status"] = "partial"
            report["failure"] = "record_limit"
        for obs in observations:
            if store.upsert(obs).get("stored"):
                report["stored"] += 1
        report["records"] = len(observations)
        report["coverage"] = store.coverage()
    except BrowserProviderError as exc:
        report["status"] = "failed"
        report["failure"] = exc.failure.value
        report["error"] = str(exc)
    finally:
        if session is not None:
            try:
                session.close()
            except BrowserProviderError as exc:
                report.setdefault("cleanup_failures", []).append(exc.to_dict())

    report["duration_seconds"] = round(time.monotonic() - started, 2)
    report["budget"] = budget.to_dict()
    return report
