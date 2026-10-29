"""Daily acquisition job with lock, checkpoint and budget.

Runs the validated listing sources (Tecnocasa, Gabetti) once, guarded by an
advisory lock so two cron/worker processes can never run the same job at once,
and writes a checkpoint with the last outcome and the next expected run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import PROJECT_ROOT, get_settings

CHECKPOINT_PATH = PROJECT_ROOT / "var" / "scheduler" / "daily.json"
LOCK_PATH = PROJECT_ROOT / "var" / "scheduler" / "daily.lock"
INTERVAL_HOURS = 24


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _acquire_lock(path: Path) -> object | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        handle = path.open("w")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write(str(Path(__file__)) + "\n")
        handle.flush()
        return handle
    except (OSError, ImportError):
        return None


def run_daily(*, checkpoint_path=None, lock_path=None, max_listings: int | None = None) -> dict:
    checkpoint_path = Path(checkpoint_path) if checkpoint_path else CHECKPOINT_PATH
    lock_path = Path(lock_path) if lock_path else LOCK_PATH
    settings = get_settings()
    #: Listings are HTTP records, not browser sessions; reuse the pilot record
    #: ceiling as the daily per-source cap (configurable via TREI_BROWSER_MAX_RECORDS_PER_SOURCE).
    limit = max_listings or settings.browser_max_records_per_source

    lock = _acquire_lock(lock_path)
    if lock is None:
        return {"status": "locked", "reason": "another daily job is already running", "checkpoint": checkpoint_path}

    started = _now_iso()
    report = {"status": "completed", "started_at": started, "sources": {}, "errors": []}
    try:
        from app.acquisition.listings import run_gabetti_pilot, run_listings_pilot

        for name, runner in (("tecnocasa_it", run_listings_pilot), ("gabetti_it", run_gabetti_pilot)):
            try:
                r = runner(max_listings=limit if name == "tecnocasa_it" else 30)
                report["sources"][name] = {
                    "status": r.get("status"),
                    "records": r.get("records"),
                    "stored": r.get("stored"),
                    "errors": len(r.get("errors", [])),
                }
            except Exception as exc:  # noqa: BLE001
                report["sources"][name] = {"status": "failed", "error": str(exc)}
                report["errors"].append({"source": name, "error": str(exc)})
    finally:
        report["finished_at"] = _now_iso()
        report["next_run_at"] = (datetime.now(UTC) + timedelta(hours=INTERVAL_HOURS)).isoformat()
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if hasattr(lock, "close"):
            lock.close()

    return report


def status(checkpoint_path=None) -> dict:
    path = Path(checkpoint_path) if checkpoint_path else CHECKPOINT_PATH
    if not path.exists():
        return {"status": "never_run", "next_run_at": "on demand (no timer registered)"}
    data = json.loads(path.read_text(encoding="utf-8"))
    data["next_run_at"] = (datetime.fromisoformat(data["finished_at"].replace("Z", "+00:00")) + timedelta(hours=INTERVAL_HOURS)).isoformat()
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trei-daily", description="TREI daily acquisition job")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(func=lambda a: print(json.dumps(run_daily(), ensure_ascii=False, indent=2)))
    sub.add_parser("status").set_defaults(func=lambda a: print(json.dumps(status(), ensure_ascii=False, indent=2)))
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
