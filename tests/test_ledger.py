"""Ledger + availability rule tests: one explicit rule for store/API/UI/export."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.acquisition.ledger import RunLedger, StatusLedger, availability, backfill_from_store
from app.acquisition.listings import Listing, ListingStore


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _store_record(source: str, native_id: str, url: str) -> dict:
    return {"key": f"{source}:{native_id}", "source": source, "native_id": native_id, "url": url}


def test_available_when_seen_in_latest_complete_run(tmp_path):
    now = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)
    runs_path = tmp_path / "runs.jsonl"
    status_path = tmp_path / "status.jsonl"
    runs = RunLedger(runs_path)
    status = StatusLedger(status_path)
    run = {"run_id": "tc-r1", "source": "tecnocasa_it", "started_at": _iso(now - timedelta(hours=1)),
           "complete": True, "discovered_urls": ["https://x/1"]}
    runs.record(run)
    status.record(run_id="tc-r1", source="tecnocasa_it", url="https://x/1", outcome="revisit",
                  observed_at=_iso(now - timedelta(minutes=30)))
    recs = [_store_record("tecnocasa_it", "1", "https://x/1")]
    assert availability(store_records=recs, now=now, runs_path=runs_path, status_path=status_path)["tecnocasa_it:1"] == "available"


def test_single_404_is_to_reconfirm_not_unavailable(tmp_path):
    now = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)
    runs_path = tmp_path / "runs.jsonl"
    status_path = tmp_path / "status.jsonl"
    runs = RunLedger(runs_path)
    status = StatusLedger(status_path)
    runs.record({"run_id": "tc-r2", "source": "tecnocasa_it", "started_at": _iso(now - timedelta(hours=1)),
                 "complete": True, "discovered_urls": ["https://x/2"]})
    status.record(run_id="tc-r2", source="tecnocasa_it", url="https://x/1", outcome="not_available",
                  http_status=404, observed_at=_iso(now - timedelta(hours=1)))
    recs = [_store_record("tecnocasa_it", "1", "https://x/1")]
    assert availability(store_records=recs, now=now, runs_path=runs_path, status_path=status_path)["tecnocasa_it:1"] == "to_reconfirm"


def test_three_404_over_a_week_plus_sitemap_absence_confirms(tmp_path):
    now = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)
    runs_path = tmp_path / "runs.jsonl"
    status_path = tmp_path / "status.jsonl"
    runs = RunLedger(runs_path)
    status = StatusLedger(status_path)
    runs.record({"run_id": "tc-r3", "source": "tecnocasa_it", "started_at": _iso(now - timedelta(hours=1)),
                 "complete": True, "discovered_urls": ["https://x/2"]})
    for i, days in enumerate((8, 4, 1)):
        status.record(run_id=f"tc-{i}", source="tecnocasa_it", url="https://x/1", outcome="not_available",
                      http_status=404, observed_at=_iso(now - timedelta(days=days)))
    recs = [_store_record("tecnocasa_it", "1", "https://x/1")]
    assert availability(store_records=recs, now=now, runs_path=runs_path, status_path=status_path)["tecnocasa_it:1"] == "confirmed_unavailable"


def test_partial_run_never_disables_unvisited_listings(tmp_path):
    now = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)
    runs_path = tmp_path / "runs.jsonl"
    status_path = tmp_path / "status.jsonl"
    runs = RunLedger(runs_path)
    status = StatusLedger(status_path)
    runs.record({"run_id": "tc-c", "source": "tecnocasa_it", "started_at": _iso(now - timedelta(days=2)),
                 "complete": True, "discovered_urls": ["https://x/1", "https://x/2"]})
    status.record(run_id="tc-c", source="tecnocasa_it", url="https://x/1", outcome="revisit",
                  observed_at=_iso(now - timedelta(days=2)))
    #: partial run visits only https://x/2 (budget stop)
    runs.record({"run_id": "tc-p", "source": "tecnocasa_it", "started_at": _iso(now - timedelta(hours=1)),
                 "complete": False, "discovered_urls": ["https://x/1", "https://x/2"]})
    status.record(run_id="tc-p", source="tecnocasa_it", url="https://x/2", outcome="revisit",
                  observed_at=_iso(now - timedelta(hours=1)))
    recs = [_store_record("tecnocasa_it", "1", "https://x/1")]
    # within the 72h freshness window -> still available
    assert availability(store_records=recs, now=now, runs_path=runs_path, status_path=status_path)["tecnocasa_it:1"] == "available"


def test_backfill_rebuilds_inferred_runs(tmp_path):
    store = ListingStore(tmp_path / "listings.jsonl")
    store.observe(Listing(source="tecnocasa_it", native_id="1", price=100.0, run_id="tc-r9"))
    runs_path = tmp_path / "runs.jsonl"
    status_path = tmp_path / "status.jsonl"
    result = backfill_from_store(tmp_path / "listings.jsonl", runs_path=runs_path, status_path=status_path)
    assert "tc-r9" in result["rebuilt_runs"]
    runs = RunLedger(runs_path)
    assert runs.latest_for("tecnocasa_it")["inferred"] is True
    status = StatusLedger(status_path)
    assert status.last_attempt_at("")
    #: a backfilled run is never marked complete (URL failures are unknown)
    assert runs.latest_complete_for("tecnocasa_it") is None
