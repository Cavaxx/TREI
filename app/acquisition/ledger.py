"""Run and per-URL status ledger, plus the single availability rule.

The listings store is append-only; it records *what was observed*, not *what
each run tried*. This module keeps the two missing ledgers so every count is
reconstructible from one run and one rule:

* ``data/runs.jsonl`` — one summary per acquisition run (sitemap/filter used,
  discovered/attempted/new/revisit/not_available/error counts, queue split,
  complete flag).
* ``data/listing_status.jsonl`` — one event per URL attempt (outcome, HTTP
  status, timestamp). Backfilled events are marked ``inferred: true``.

``availability()`` is THE rule used by store counts, API, UI and export:
a listing is *available* when it was successfully observed in the latest
complete run of its source (or within ``FRESH_HOURS`` if the latest run was
partial); 404/410 or absence from the latest sitemap make it
*to_reconfirm*; three or more 404s across at least 7 days plus sitemap
absence make it *confirmed_unavailable*. A 404 is NEVER a sale and a partial
run NEVER disables listings it did not visit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import PROJECT_ROOT

RUNS_PATH = PROJECT_ROOT / "data" / "runs.jsonl"
STATUS_PATH = PROJECT_ROOT / "data" / "listing_status.jsonl"

#: A listing keeps its last state when its source run was partial and it was
#: not visited; when no complete run exists yet this window bounds staleness.
FRESH_HOURS = 72

#: Outcomes that count as a successful observation.
SUCCESS_OUTCOMES = {"valid_new", "revisit", "valid_updated"}

#: Outcomes that are terminal for a URL (the page refuses to exist/serve).
TERMINAL_OUTCOMES = {"not_available", "blocked"}

#: Outcomes worth retrying on a later run.
RETRYABLE_OUTCOMES = {"error", "no_data", "http_429", "http_500", "http_502", "http_503"}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    #: split on '\n' only (U+2028/U+2029 must not split JSON strings)
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            out.append(json.loads(line))
    return out


class RunLedger:
    """Append-only per-run summaries (data/runs.jsonl)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else RUNS_PATH

    def record(self, summary: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
        return summary

    def all(self) -> list[dict]:
        return _load_lines(self.path)

    def latest_for(self, source: str) -> dict | None:
        runs = [r for r in self.all() if r.get("source") == source]
        return max(runs, key=lambda r: r.get("started_at") or "", default=None)

    def latest_complete_for(self, source: str) -> dict | None:
        runs = [r for r in self.all() if r.get("source") == source and r.get("complete")]
        return max(runs, key=lambda r: r.get("started_at") or "", default=None)


class StatusLedger:
    """Append-only per-URL attempt events (data/listing_status.jsonl)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else STATUS_PATH

    def record(self, *, run_id: str, source: str, url: str, outcome: str,
               http_status: int | None = None, observed_at: str | None = None,
               detail: str = "", inferred: bool = False) -> dict:
        event = {
            "run_id": run_id,
            "source": source,
            "url": url,
            "outcome": outcome,
            "http_status": http_status,
            "observed_at": observed_at or _now_iso(),
            "detail": detail[:200],
            "inferred": inferred,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def all(self) -> list[dict]:
        return _load_lines(self.path)

    def events_for(self, url: str) -> list[dict]:
        return [e for e in self.all() if e.get("url") == url]

    def not_available_events(self, url: str) -> list[dict]:
        return [e for e in self.events_for(url) if e.get("outcome") in TERMINAL_OUTCOMES]

    def last_attempt_at(self, url: str) -> str:
        events = self.events_for(url)
        return max((e.get("observed_at") or "" for e in events), default="")



def availability(*, store_records: list[dict], source: str | None = None,
                 now: datetime | None = None, runs_path: Path | None = None,
                 status_path: Path | None = None) -> dict[str, str]:
    """Classify every listing key with ONE explicit rule.

    Returns {key: status} where status is one of:
    ``available``, ``to_reconfirm``, ``confirmed_unavailable``, ``uncertain``.
    """
    now = now or datetime.now(UTC)
    runs = RunLedger(runs_path)
    status = StatusLedger(status_path)

    #: Read both ledgers ONCE and index them. The classification loop below
    #: does per-URL lookups; re-reading the status file inside the loop (as
    #: ``events_for``/``not_available_events`` do) is O(listings × file) and
    #: freezes the request once the archive holds thousands of listings.
    run_rows = runs.all()
    latest_complete: dict[str, dict] = {}
    latest_any: dict[str, dict] = {}
    for r in run_rows:
        src = r.get("source")
        if not src:
            continue
        if r.get("complete"):
            if src not in latest_complete or (r.get("started_at") or "") > (
                latest_complete[src].get("started_at") or ""
            ):
                latest_complete[src] = r
        if src not in latest_any or (r.get("started_at") or "") > (
            latest_any[src].get("started_at") or ""
        ):
            latest_any[src] = r

    events_by_url: dict[str, list[dict]] = {}
    events_by_run: dict[str, list[dict]] = {}
    for e in status.all():
        events_by_url.setdefault(e.get("url") or "", []).append(e)
        events_by_run.setdefault(e.get("run_id") or "", []).append(e)

    out: dict[str, str] = {}
    sources = {source} if source else {r.get("source") for r in store_records}
    for src in sources:
        latest_complete_run = latest_complete.get(src)
        latest_any_run = latest_any.get(src)
        complete_urls: set[str] = set()
        complete_discovered: set[str] = set()
        complete_at: datetime | None = None
        if latest_complete_run:
            complete_at = _parse_dt(latest_complete_run.get("started_at"))
            complete_urls = {e.get("url") for e in events_by_run.get(latest_complete_run.get("run_id"), [])
                             if e.get("outcome") in SUCCESS_OUTCOMES}
            complete_discovered = set(latest_complete_run.get("discovered_urls") or [])
        last_run_urls: set[str] = set()
        if latest_any_run:
            last_run_urls = {e.get("url") for e in events_by_run.get(latest_any_run.get("run_id"), [])}

        for rec in store_records:
            if rec.get("source") != src:
                continue
            key = rec.get("key") or f"{src}:{rec.get('native_id')}"
            url = rec.get("url") or ""
            if url in complete_urls:
                out[key] = "available"
                continue
            url_events = events_by_url.get(url, [])
            dead = [e for e in url_events if e.get("outcome") in TERMINAL_OUTCOMES]
            if dead:
                dates = sorted(d for e in dead if (d := _parse_dt(e.get("observed_at") or "")))
                if len(dead) >= 3 and dates and (dates[-1] - dates[0]).days >= 7 and complete_discovered and url not in complete_discovered:
                    out[key] = "confirmed_unavailable"
                    continue
                out[key] = "to_reconfirm"
                continue
            if complete_discovered and url not in complete_discovered:
                out[key] = "to_reconfirm"
                continue
            # No complete run for this source yet: freshness window on the
            # last successful visit decides between available and uncertain.
            ok_events = [e for e in url_events if e.get("outcome") in SUCCESS_OUTCOMES]
            last_ok = max((_parse_dt(e.get("observed_at") or "") for e in ok_events), default=None)
            if complete_at is None:
                cutoff = now - timedelta(hours=FRESH_HOURS)
                out[key] = "available" if last_ok and last_ok >= cutoff else "uncertain"
                continue
            # Partial run that did not visit this URL: keep the previous state.
            if url not in last_run_urls:
                cutoff = now - timedelta(hours=FRESH_HOURS)
                out[key] = "available" if last_ok and last_ok >= cutoff else "uncertain"
                continue
            out[key] = "uncertain"
    return out


def backfill_from_store(store_path: Path | None = None, *, runs_path: Path | None = None,
                        status_path: Path | None = None) -> dict:
    """Rebuild run summaries from the listings store (run_ids already used).

    Only successful observations are reconstructible from the store; URL-level
    failures (404/errors) of past runs are NOT. Rebuilt runs are marked
    ``inferred: true`` and are never ``complete`` (a complete run needs the
    full sitemap manifest, which only new runs record).
    """
    from collections import Counter

    from app.acquisition.listings import ListingStore

    store = ListingStore(store_path or (PROJECT_ROOT / "data" / "listings.jsonl"))
    runs = RunLedger(runs_path)
    status = StatusLedger(status_path)
    existing = {r.get("run_id") for r in runs.all()}
    rebuilt = []
    by_run: dict[str, list[dict]] = {}
    for rec in store.all():
        by_run.setdefault(rec.get("run_id", ""), []).append(rec)

    for run_id, recs in sorted(by_run.items(), key=lambda kv: min(r.get("observed_at") or "" for r in kv[1])):
        if not run_id or run_id in existing:
            continue
        source = recs[0].get("source")
        counters = Counter()
        urls: set[str] = set()
        for r in recs:
            outcome = "revisit" if r.get("content_changed") is False else (
                "valid_new" if not r.get("first_seen_at") or r.get("first_seen_at") == r.get("observed_at")
                else "valid_updated")
            counters[outcome] += 1
            urls.add(r.get("url") or "")
            status.record(
                run_id=run_id, source=source, url=r.get("url") or "", outcome=outcome,
                http_status=200, observed_at=r.get("observed_at"), inferred=True,
            )
        summary = {
            "run_id": run_id,
            "source": source,
            "started_at": min(r.get("observed_at") or "" for r in recs),
            "finished_at": max(r.get("observed_at") or "" for r in recs),
            "complete": False,
            "inferred": True,
            "note": "ricostruito dal listings store: i fallimenti URL (404/errori) del run non sono ricostruibili",
            "discovered": len(urls),
            "attempted": len(urls),
            "outcomes": dict(counters),
            "queue": {"pending": 0, "retryable_errors": 0, "budget_deferred": 0, "terminal": 0},
        }
        runs.record(summary)
        rebuilt.append(run_id)
    return {"rebuilt_runs": rebuilt}
