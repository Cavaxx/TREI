"""Scheduler tests (§47): the timetable honours the policy, not the other way round."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.fetchers.registry import SourceConfig, SourceRegistry
from app.scheduler.plan import build_plan, due_jobs


def registry() -> SourceRegistry:
    return SourceRegistry(
        [
            SourceConfig(
                name="portale_pubblico", domain="p.it", kind="portal", tier="tier4_scraping",
                status="OK", recommended_mode="public", robots_txt=True, scraping_allowed="partial",
                extra={"crawl": {"search_urls": ["https://p.it/annunci/trento/"], "incremental_minutes": 120}},
            ),
            SourceConfig(
                name="portale_chiuso", domain="c.it", kind="portal", tier="tier5_restricted",
                status="restricted", recommended_mode="licence", requires_auth=True,
            ),
            SourceConfig(
                name="asta_rss", domain="a.it", kind="auction", tier="tier4_scraping", status="OK",
                recommended_mode="feed", robots_txt=True, scraping_allowed="partial", feed=True,
                extra={"crawl": {"feeds": ["https://a.it/rss.xml"], "interval_minutes": 720}},
            ),
        ]
    )


def test_plan_includes_email_and_only_permitted_sources():
    jobs = build_plan(registry(), settings=Settings())
    kinds = {job.kind for job in jobs}
    assert "email_ingest" in kinds
    assert "agent_import" in kinds
    assert "auction_fetch" in kinds

    sources = {job.source for job in jobs}
    assert "portale_pubblico" in sources
    assert "portale_chiuso" not in sources  # restricted: never scheduled
    assert "asta_rss" in sources


def test_incremental_and_full_crawls_are_both_scheduled():
    jobs = build_plan(registry(), settings=Settings())
    for job in jobs:
        if job.source == "portale_pubblico" and job.kind == "agent_import":
            if "--mode" in job.command:
                assert "full" in job.command
            else:
                assert job.interval_minutes == 120
    assert any(job.name.startswith("agent-import-full:") for job in jobs)


def test_auction_job_uses_the_configured_feed_url():
    jobs = build_plan(registry(), settings=Settings())
    auction = next(job for job in jobs if job.kind == "auction_fetch")
    assert "--url" in auction.command
    assert "https://a.it/rss.xml" in auction.command
    assert auction.interval_minutes == 720


def test_due_jobs_respect_intervals():
    jobs = build_plan(registry(), settings=Settings())
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    # Nothing has run yet, so everything is due.
    assert len(due_jobs(jobs, now=now)) == len(jobs)

    last_runs = {job.name: (now - timedelta(minutes=1)).isoformat() for job in jobs}
    assert due_jobs(jobs, last_runs=last_runs, now=now) == []

    stale = {job.name: (now - timedelta(days=30)).isoformat() for job in jobs}
    assert len(due_jobs(jobs, last_runs=stale, now=now)) == len(jobs)
