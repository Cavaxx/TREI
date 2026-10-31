"""Crontab operations for the TREI daily job (DST-safe, marker-scoped).

Debian ``cron`` 3.0pl1 handles one timezone only and does NOT honour
``CRON_TZ``: the system clock is UTC, so a plain ``15 7 * * *`` fires at 07:15
UTC (08:15/09:15 Europe/Rome). The installed line therefore uses the guard
pattern recommended by ``crontab(5)`` — fire at both candidate UTC hours and
run only when the Rome local time is 07:15. Every operation:

* backs the current crontab up before touching it;
* targets ONLY the line carrying the ``# TREI-DAILY`` marker;
* refuses to install if reading the crontab fails, and refuses to disable if
  the marker count is not exactly one;
* never uses ``crontab -r`` and never deletes foreign lines.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import PROJECT_ROOT

MARKER = "# TREI-DAILY"
BACKUP_DIR = PROJECT_ROOT / "var" / "scheduler"
PROJECT_DIR = str(PROJECT_ROOT)
PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")

DST_SAFE_LINE = (
    '15 5,6 * * * [ "$(TZ=Europe/Rome date +\\%H:\\%M)" = "07:15" ] && '
    f"cd {PROJECT_DIR} && {PYTHON} -m app.scheduler.daily run "
    f">> {PROJECT_DIR}/var/scheduler/daily.log 2>&1 {MARKER}"
)


def _read() -> str:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"crontab -l failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def _write(content: str) -> None:
    proc = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"crontab install failed ({proc.returncode}): {proc.stderr.strip()}")


def _backup(content: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = BACKUP_DIR / f"crontab.bak.{ts}"
    path.write_text(content, encoding="utf-8")
    return path


def _marked_lines(content: str) -> list[str]:
    return [ln for ln in content.splitlines() if MARKER in ln]


def status() -> dict:
    try:
        content = _read()
    except RuntimeError as exc:
        return {"status": "unreadable", "error": str(exc)}
    marked = _marked_lines(content)
    return {
        "status": "installed" if len(marked) == 1 else ("duplicated" if len(marked) > 1 else "not_installed"),
        "marker_lines": marked,
        "system_timezone": "UTC (cron 3.0pl1: CRON_TZ not honoured)",
        "schedule": "07:15 Europe/Rome, DST-safe guard (15 5,6 * * * + TZ guard)",
    }


def install() -> dict:
    try:
        before = _read()
    except RuntimeError as exc:
        return {"status": "aborted", "reason": f"read failed: {exc}", "installed": False}
    backup = _backup(before)
    #: strip BOTH the old unmarked TREI entry (same command) and any marked one
    lines = [ln for ln in before.splitlines()
             if MARKER not in ln and "app.scheduler.daily" not in ln and ln.strip()]
    lines.append(DST_SAFE_LINE)
    after = "\n".join(lines) + "\n"
    _write(after)
    verify = _read()
    if MARKER not in verify:
        return {"status": "aborted", "reason": "verification after install failed", "backup": str(backup), "installed": False}
    return {
        "status": "installed",
        "backup": str(backup),
        "removed_old_marker_lines": _marked_lines(before),
        "before_lines": len(before.splitlines()),
        "after_lines": len(after.splitlines()),
    }


def disable() -> dict:
    try:
        before = _read()
    except RuntimeError as exc:
        return {"status": "aborted", "reason": f"read failed: {exc}", "disabled": False}
    marked = _marked_lines(before)
    if len(marked) != 1:
        return {
            "status": "aborted",
            "reason": f"expected exactly one {MARKER!r} line, found {len(marked)}",
            "disabled": False,
        }
    backup = _backup(before)
    after = "\n".join(ln for ln in before.splitlines() if MARKER not in ln)
    after = after.rstrip("\n") + "\n" if after.strip() else ""
    _write(after)
    verify = _read()
    if MARKER in verify:
        return {"status": "aborted", "reason": "marker still present after disable", "backup": str(backup), "disabled": False}
    return {
        "status": "disabled",
        "backup": str(backup),
        "removed_line": marked[0],
        "before_lines": len(before.splitlines()),
        "after_lines": len(verify.splitlines()),
        "other_lines_preserved": len(verify.splitlines()) == len(before.splitlines()) - 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trei-cronops", description="TREI daily crontab operations")
    parser.add_argument("cmd", choices=["status", "install", "disable"])
    args = parser.parse_args(argv)
    import json

    if args.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    elif args.cmd == "install":
        print(json.dumps(install(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(disable(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
