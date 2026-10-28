"""Crawler state store (§24, §44).

Keeps the record of what each crawl did: pages, listings, failures, blocked
requests. In production this is a Postgres table plus Redis for in-flight jobs;
the JSONL journal here keeps the same shape so the admin dashboard and the
service layer do not change when the storage is swapped.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.agent_import.models import CrawlJob, SourceHealthRecord


class CrawlStateStore:
    """Append-only journal of crawl jobs, plus rolling per-source metrics."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._jobs: dict[str, CrawlJob] = {}
        self._by_source: dict[str, list[str]] = defaultdict(list)

    # --- writes -------------------------------------------------------------
    def record(self, job: CrawlJob) -> None:
        self._jobs[job.job_id] = job
        self._by_source[job.source].append(job.job_id)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(job.to_dict(), ensure_ascii=False) + "\n")

    # --- reads --------------------------------------------------------------
    def job(self, job_id: str) -> CrawlJob | None:
        return self._jobs.get(job_id)

    def jobs(self, source: str | None = None) -> list[CrawlJob]:
        ids = self._by_source[source] if source else list(self._jobs)
        return [self._jobs[i] for i in ids]

    def latest(self, source: str) -> CrawlJob | None:
        jobs = self.jobs(source)
        return jobs[-1] if jobs else None

    def health(self, source: str) -> SourceHealthRecord | None:
        """Rolling metrics: the latest job, with history accumulated across runs."""
        jobs = self.jobs(source)
        if not jobs:
            return None
        record = SourceHealthRecord.from_job(jobs[-1])
        record.history = [
            {
                "job_id": job.job_id,
                "mode": job.mode,
                "status": job.status.value,
                "pages": len(job.pages),
                "listings": job.listings_processed,
                "blocked_requests": job.blocked_requests,
                "parser_errors": job.parser_errors,
                "finished_at": job.finished_at,
            }
            for job in jobs
        ]
        return record

    def metrics(self) -> dict:
        """Everything the admin crawler dashboard needs (§44)."""
        sources = sorted(self._by_source)
        return {
            "sources": [self.health(source).to_dict() for source in sources if self.health(source)],
            "totals": {
                "sources": len(sources),
                "jobs": len(self._jobs),
                "pages": sum(len(job.pages) for job in self._jobs.values()),
                "listings": sum(job.listings_processed for job in self._jobs.values()),
                "blocked_requests": sum(job.blocked_requests for job in self._jobs.values()),
                "parser_errors": sum(job.parser_errors for job in self._jobs.values()),
            },
        }
