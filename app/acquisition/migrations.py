"""Controlled, idempotent migrations for acquisition data.

A migration marks the affected records as superseded (never silently merged) and
re-derives the corrected records from the preserved raw evidence, so the fix is
reproducible and re-runnable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.acquisition.connectors.ispat import grid_from_html, parse_v07, raw_content_hash
from app.acquisition.store import ObservationStore
from app.core.config import PROJECT_ROOT


def _find_v07_raw(root: Path) -> Path | None:
    for p in sorted(root.rglob("*.html")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Compravendite" in text and "Mutui con costituzione" in text:
            return p
    return None


def _wrong_v07(rec: dict) -> bool:
    """Predicate for the V.07 records that were mislabeled before ispat-0.2.0."""
    if rec.get("definition_version") != "V.07 (Fonte: Istat)":
        return False
    if rec.get("indicator") in ("mutui_residenziali", "mutui_economici"):
        return True
    if rec.get("indicator") == "compravendite_residenziali" and rec.get("series") == "ISPAT_NOTARIAL":
        return True
    return False


def _correct_v07(rec: dict) -> bool:
    """Predicate for the corrected V.07 records (must never be superseded)."""
    if rec.get("definition_version") != "V.07 (Fonte: Istat)":
        return False
    return not _wrong_v07(rec)


def migrate_v07(*, store_path=None, data_root=None) -> dict:
    store = ObservationStore(store_path)
    data_root = Path(data_root) if data_root else PROJECT_ROOT / "data" / "raw"
    before_active = len(store.all())
    wrong_before = [r for r in store.all() if _wrong_v07(r)]

    raw = _find_v07_raw(data_root)
    if raw is None:
        return {"status": "no_raw", "message": "no V.07 raw artifact found", "before_active": before_active}

    reason = (
        "V.07 header misread: columns are compravendite (abitazione ed accessori, uso economico) "
        "and mutui; series corrected (ispat-0.2.0)"
    )
    # Self-heal first: never leave the corrected records flagged.
    restored = store.restore(where=_correct_v07)
    superseded = store.supersede(where=_wrong_v07, reason=reason)

    html = raw.read_text(encoding="utf-8")
    grid = grid_from_html(html)
    run_id = "migrate-v07-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    observations = parse_v07(grid, source="ispat_provincia_tn", run_id=run_id)
    content_hash = raw_content_hash(html.encode())
    for obs in observations:
        obs.content_hash = content_hash
        obs.raw_object_id = f"ispat_provincia_tn:{content_hash[:16]}"

    newly_stored = sum(1 for obs in observations if store.upsert(obs).get("stored"))

    after_active = store.all()
    return {
        "status": "done",
        "raw_artifact": str(raw),
        "restored": restored,
        "superseded": superseded,
        "wrong_before": len(wrong_before),
        "reparsed": len(observations),
        "newly_stored": newly_stored,
        "active_before": before_active,
        "active_after": len(after_active),
        "active_indicators": sorted({r["indicator"] for r in after_active}),
    }
