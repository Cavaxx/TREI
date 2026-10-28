"""Crawler state objects (§24-25, §44).

Every crawl is recorded, including the parts that failed. Failure modes are
typed, so a blocked page never becomes an ``HTTP_ERROR`` and, above all, is never
recorded as a listing that does not exist.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from app.core.enums import CrawlFailure, CrawlStatus


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _job_id(source: str, mode: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha1(f"{source}|{mode}|{stamp}".encode()).hexdigest()[:8]
    return f"{source}-{mode}-{stamp}-{digest}"


@dataclass
class CrawlPage:
    """One fetched page and what happened to it."""

    url: str
    fetched_at: str = field(default_factory=now_iso)
    status_code: int | None = None
    content_hash: str = ""
    raw_object_id: str = ""
    byte_size: int = 0
    listings_found: int = 0
    failure: CrawlFailure | None = None
    detail: str = ""
    retryable: bool = False
    #: Which extraction method produced the data on this page (§29/§30).
    method: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["failure"] = self.failure.value if self.failure else None
        return data


@dataclass
class CrawlJob:
    """A single crawl run against a single source."""

    source: str
    mode: str = "incremental"
    job_id: str = ""
    status: CrawlStatus = CrawlStatus.PENDING
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    pages_discovered: int = 0
    listings_discovered: int = 0
    listings_processed: int = 0
    listings_failed: int = 0
    blocked_requests: int = 0
    parser_errors: int = 0
    new_listings: int = 0
    updated_listings: int = 0
    removed_listings: int = 0
    duplicate_listings: int = 0
    review_required: int = 0
    pages: list[CrawlPage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.job_id:
            self.job_id = _job_id(self.source, self.mode)

    def add_page(self, page: CrawlPage) -> None:
        self.pages.append(page)
        if page.failure == CrawlFailure.ROBOTS_BLOCKED:
            self.blocked_requests += 1
        elif page.failure in (CrawlFailure.RATE_LIMITED, CrawlFailure.SOURCE_RESTRICTED):
            self.blocked_requests += 1
        elif page.failure == CrawlFailure.PARSER_ERROR:
            self.parser_errors += 1

    def finish(self, status: CrawlStatus | None = None) -> None:
        self.finished_at = now_iso()
        if status is not None:
            self.status = status
        elif self.status == CrawlStatus.RUNNING:
            self.status = CrawlStatus.PARTIAL if self.listings_failed else CrawlStatus.COMPLETED

    @property
    def failure_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for page in self.pages:
            if page.failure:
                counts[page.failure.value] = counts.get(page.failure.value, 0) + 1
        return counts

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["pages"] = [p.to_dict() for p in self.pages]
        data["failure_counts"] = self.failure_counts
        return data


@dataclass
class SourceHealthRecord:
    """Rolling per-source crawler metrics shown on the admin dashboard (§44)."""

    source: str
    crawl_status: str = "unknown"
    last_crawl: str | None = None
    pages: int = 0
    listings: int = 0
    new_listings: int = 0
    updated_listings: int = 0
    removed_listings: int = 0
    duplicate_rate: float | None = None
    parser_error_rate: float | None = None
    blocked_rate: float | None = None
    history: list[dict] = field(default_factory=list)

    @classmethod
    def from_job(cls, job: CrawlJob) -> SourceHealthRecord:
        pages = len(job.pages)
        return cls(
            source=job.source,
            crawl_status=job.status.value,
            last_crawl=job.finished_at or job.started_at,
            pages=pages,
            listings=job.listings_processed,
            new_listings=job.new_listings,
            updated_listings=job.updated_listings,
            removed_listings=job.removed_listings,
            duplicate_rate=(job.duplicate_listings / job.listings_discovered)
            if job.listings_discovered
            else None,
            parser_error_rate=(job.parser_errors / pages) if pages else None,
            blocked_rate=(job.blocked_requests / pages) if pages else None,
            history=[
                {
                    "job_id": job.job_id,
                    "mode": job.mode,
                    "status": job.status.value,
                    "pages": pages,
                    "listings": job.listings_processed,
                    "blocked_requests": job.blocked_requests,
                    "parser_errors": job.parser_errors,
                    "finished_at": job.finished_at,
                }
            ],
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("duplicate_rate", "parser_error_rate", "blocked_rate"):
            value = data.get(key)
            data[key] = None if value is None else round(value, 3)
        return data
