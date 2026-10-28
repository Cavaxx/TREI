"""Schedule construction and the scheduler entry point (§47).

Nothing here decides *whether* a source may be crawled - that is the policy
resolver's job. The scheduler only decides *when* a permitted job runs, and it
refuses to schedule anything the policy would refuse.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from app.agent_import.policy import PolicyResolver
from app.core.config import Settings, get_settings
from app.fetchers.licence import LicenceRegistry
from app.fetchers.registry import SourceRegistry

#: Default cadences in minutes. Overridable per source in the registry.
DEFAULTS = {
    "email_ingest": 15,
    "auction_check": 1440,  # at least once a day
    "macro_refresh": 43200,  # monthly-ish sources
}


@dataclass
class ScheduledJob:
    name: str
    kind: str
    interval_minutes: int
    command: list[str]
    source: str = ""
    note: str = ""
    last_run: str | None = None

    @property
    def due_at(self) -> str:
        return (datetime.now(UTC) + timedelta(minutes=self.interval_minutes)).isoformat()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["due_at"] = self.due_at
        return data


def _extra(registry: SourceRegistry, source_name: str) -> dict:
    source = registry.get(source_name)
    if source is None or not isinstance(source.extra, dict):
        return {}
    return source.extra


def _crawl_config(registry: SourceRegistry, source_name: str) -> dict:
    crawl = _extra(registry, source_name).get("crawl")
    return crawl if isinstance(crawl, dict) else {}


def build_plan(
    registry: SourceRegistry,
    *,
    licences: LicenceRegistry | None = None,
    settings: Settings | None = None,
) -> list[ScheduledJob]:
    """The full timetable. Jobs whose source policy is not allowed are omitted."""
    settings = settings or get_settings()
    resolver = PolicyResolver(registry, licences=licences)
    jobs: list[ScheduledJob] = [
        ScheduledJob(
            name="email-ingest",
            kind="email_ingest",
            interval_minutes=DEFAULTS["email_ingest"],
            command=["python", "-m", "app.email_alert.cli", "ingest"],
            note="poll the alert mailbox; the email channel needs no policy decision",
        )
    ]

    for source in registry.all():
        crawl = _crawl_config(registry, source.name)
        urls = [str(u) for u in (crawl.get("search_urls") or [])]
        if not resolver.decide(source.name, urls=urls or None).allowed:
            continue

        if source.kind in {"portal", "agency"}:
            incremental = int(crawl.get("incremental_minutes", settings.crawl_incremental_minutes))
            jobs.append(
                ScheduledJob(
                    name=f"agent-import:{source.name}",
                    kind="agent_import",
                    source=source.name,
                    interval_minutes=incremental,
                    command=["python", "-m", "app.agent_import.cli", "crawl", source.name],
                    note="incremental crawl" if urls else "no search URLs configured yet",
                )
            )
            jobs.append(
                ScheduledJob(
                    name=f"agent-import-full:{source.name}",
                    kind="agent_import",
                    source=source.name,
                    interval_minutes=settings.crawl_full_hours * 60,
                    command=[
                        "python", "-m", "app.agent_import.cli", "crawl", source.name, "--mode", "full"
                    ],
                    note="full re-discovery; absences become 'removed', never a sale",
                )
            )
        elif source.kind == "auction":
            feeds = crawl.get("feeds") or []
            if feeds:
                command = ["python", "-m", "app.auction_fetcher.cli", "feeds", source.name]
                for feed in feeds:
                    command += ["--url", str(feed)]
                jobs.append(
                    ScheduledJob(
                        name=f"auction-fetch:{source.name}",
                        kind="auction_fetch",
                        source=source.name,
                        interval_minutes=int(crawl.get("interval_minutes", DEFAULTS["auction_check"])),
                        command=command,
                        note="auction feed; PVP remains authoritative for identity",
                    )
                )
        elif source.kind in {"statistics", "macro", "transactions"}:
            jobs.append(
                ScheduledJob(
                    name=f"macro-refresh:{source.name}",
                    kind="macro_refresh",
                    source=source.name,
                    interval_minutes=int(crawl.get("interval_minutes", DEFAULTS["macro_refresh"])),
                    command=["python", "-m", "app.fetchers.cli", "list"],
                    note=(
                        "placeholder: the macro adapter is wired per source once its "
                        "publication format is verified"
                    ),
                )
            )
    return jobs


def due_jobs(
    jobs: list[ScheduledJob],
    *,
    last_runs: dict[str, str] | None = None,
    now: datetime | None = None,
) -> list[ScheduledJob]:
    """Which jobs are due, given the last run recorded for each."""
    now = now or datetime.now(UTC)
    last_runs = last_runs or {}
    due: list[ScheduledJob] = []
    for job in jobs:
        raw = last_runs.get(job.name)
        if raw is None:
            due.append(job)
            continue
        try:
            last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            due.append(job)
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if now - last >= timedelta(minutes=job.interval_minutes):
            due.append(job)
    return due


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(prog="trei-scheduler", description="TREI schedule plan")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "due"):
        sub.add_parser(name).set_defaults(cmd_name=name)
    args = parser.parse_args(argv)

    registry = SourceRegistry.load()
    try:
        licences = LicenceRegistry.load()
    except FileNotFoundError:
        licences = LicenceRegistry([])
    jobs = build_plan(registry, licences=licences)
    selected = jobs if args.cmd_name == "plan" else due_jobs(jobs)

    print(
        json.dumps(
            {
                "command": args.cmd_name,
                "job_count": len(selected),
                "jobs": [job.to_dict() for job in selected],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
