"""CrawlerManager and the specialised acquisition agents (§8-10, §23-31).

The manager is deliberately *not* an autonomous LLM. It is a deterministic
orchestrator with narrow, testable steps::

    CrawlerManager
        |- policy:         is this source allowed at all?      (policy.py)
        |- discovery:      which listing URLs does it expose?  (extraction.py)
        |- navigation:     fetch each detail page politely      (limiter.py)
        |- extraction:     JSON-LD -> ... -> visible text       (extraction.py)
        |- validation:     is it really in Trentino, is it real
        |- deduplication:  is this a property we have seen?     (property_master)
        |- review:         anything uncertain goes to a human

Full and incremental crawls share the same code path; the only difference is
which URLs are considered new, which is derived from the Property Master.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import httpx

from app.agent_import import extraction as ex
from app.agent_import.browser import BrowserRenderer, VisionExtractor, render_if_needed
from app.agent_import.limiter import RateLimiter, parse_retry_after
from app.agent_import.models import CrawlJob, CrawlPage, SourceHealthRecord, now_iso
from app.agent_import.policy import AccessDecision, PolicyResolver
from app.core.config import Settings, get_settings
from app.core.enums import CrawlFailure, CrawlStatus, ObservationSource
from app.core.fields import ExtractionBundle
from app.fetchers.audit import AccessEvent, AccessLog, AccessOutcome, scrub_url
from app.processors.normalize import normalize_listing
from app.processors.territory import assess_territory
from app.property_master.master import PropertyMasterStore
from app.storage.datalake import DataLake


@dataclass
class CrawlRequest:
    """What to crawl, and within which limits (§9)."""

    source: str
    search_urls: list[str] = field(default_factory=list)
    territory: str = "Provincia Autonoma di Trento"
    #: Full crawl re-discovers everything; incremental revisits known listings.
    crawl_mode: str = "incremental"
    crawl_depth: int = 1
    max_requests: int = 200
    max_listings: int | None = None
    #: Source-specific URL conventions (from the source adapter).
    id_patterns: tuple[str, ...] = ()
    url_patterns: tuple[str, ...] = ()
    #: Restrict the run to URLs already known to the Property Master.
    known_urls: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "search_urls": list(self.search_urls),
            "territory": self.territory,
            "crawl_mode": self.crawl_mode,
            "crawl_depth": self.crawl_depth,
            "max_requests": self.max_requests,
            "max_listings": self.max_listings,
        }


@dataclass
class FetchOutcome:
    """The result of one polite HTTP request."""

    url: str
    status_code: int | None = None
    payload: bytes = b""
    text: str = ""
    failure: CrawlFailure | None = None
    detail: str = ""
    retryable: bool = False
    retry_after_s: float | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None and (self.status_code or 0) < 400


#: HTTP status -> crawler failure. A 404 is an HTTP_ERROR on the *page*, never a
#: statement that the property does not exist (§25).
_STATUS_FAILURE = {
    401: CrawlFailure.SOURCE_RESTRICTED,
    403: CrawlFailure.SOURCE_RESTRICTED,
    404: CrawlFailure.HTTP_ERROR,
    410: CrawlFailure.HTTP_ERROR,
    429: CrawlFailure.RATE_LIMITED,
    503: CrawlFailure.RATE_LIMITED,
}


def classify_status(status_code: int, retry_after_s: float | None = None) -> tuple[CrawlFailure, bool]:
    """(failure, retryable) for a non-2xx response."""
    failure = _STATUS_FAILURE.get(status_code)
    if failure is None:
        failure = CrawlFailure.HTTP_ERROR
    retryable = status_code >= 500 or status_code in (408, 429)
    if failure is CrawlFailure.SOURCE_RESTRICTED:
        retryable = False  # a refusal is final; we do not try to get around it
    return failure, retryable


class HttpTransport:
    """Polite HTTP for the Agent Import channel.

    Everything that makes a request go through here, so the User-Agent is always
    ours, the timeout is always set, and no header is ever mutated to disguise
    the automation.
    """

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client or httpx.Client(
            timeout=self.settings.request_timeout_s,
            headers={"User-Agent": self.settings.user_agent, "Accept-Language": "it-IT,it;q=0.9"},
            follow_redirects=True,
        )

    def fetch(self, url: str) -> FetchOutcome:
        try:
            response = self._client.get(url)
        except httpx.TimeoutException as exc:
            return FetchOutcome(url=url, failure=CrawlFailure.TIMEOUT, detail=str(exc), retryable=True)
        except httpx.HTTPError as exc:
            return FetchOutcome(url=url, failure=CrawlFailure.HTTP_ERROR, detail=str(exc), retryable=True)

        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        if response.status_code >= 400:
            failure, retryable = classify_status(response.status_code, retry_after)
            return FetchOutcome(
                url=url,
                status_code=response.status_code,
                failure=failure,
                detail=f"HTTP {response.status_code}",
                retryable=retryable,
                retry_after_s=retry_after,
            )
        text = response.text
        return FetchOutcome(
            url=url,
            status_code=response.status_code,
            payload=response.content,
            text=text,
        )


class CrawlerManager:
    """Deterministic orchestrator for the Agent Import channel (§28)."""

    def __init__(
        self,
        *,
        resolver: PolicyResolver,
        store: PropertyMasterStore,
        datalake: DataLake | None = None,
        transport: HttpTransport | None = None,
        audit: AccessLog | None = None,
        renderer: BrowserRenderer | None = None,
        vision: VisionExtractor | None = None,
        settings: Settings | None = None,
        sleeper=None,
        clock=None,
    ) -> None:
        self.resolver = resolver
        self.store = store
        self.datalake = datalake or DataLake()
        self.settings = settings or get_settings()
        self.transport = transport or HttpTransport(self.settings)
        self.audit = audit or AccessLog()
        self.renderer = renderer
        self.vision = vision
        self._sleeper = sleeper
        self._clock = clock

    # --- entry point --------------------------------------------------------
    def decide(self, request: CrawlRequest) -> AccessDecision:
        return self.resolver.decide(request.source, urls=list(request.search_urls))

    def run(self, request: CrawlRequest) -> CrawlJob:
        decision = self.decide(request)
        job = CrawlJob(source=request.source, mode=request.crawl_mode, status=CrawlStatus.RUNNING)
        if not decision.allowed:
            return self._refuse(job, request, decision)

        limiter = RateLimiter(
            source=request.source,
            requests_per_minute=decision.requests_per_minute,
            delay_s=decision.delay_s,
        )
        if self._sleeper is not None:
            limiter.sleeper = self._sleeper
        if self._clock is not None:
            limiter.clock = self._clock

        before = {record.key for record in self.store.listings()}
        discovery_urls = list(request.search_urls)
        if request.crawl_mode == "incremental" and request.known_urls:
            discovery_urls = [u for u in discovery_urls if u in set(request.known_urls)] or discovery_urls

        links = self._discover(discovery_urls, request, job, limiter)
        job.listings_discovered = len(links)
        if job.status != CrawlStatus.BLOCKED:
            self._visit(links, request, job, limiter, before)
            self.store.resolve()
            removed = self._mark_absent(links, request) if request.crawl_mode == "full" else 0
            job.removed_listings = removed

        if job.status == CrawlStatus.RUNNING:
            job.finish()
        else:
            job.finish(job.status)
        return job

    def _refuse(self, job: CrawlJob, request: CrawlRequest, decision: AccessDecision) -> CrawlJob:
        """A refusal costs zero requests and is written to the evidence log."""
        if decision.robots_allowed is False:
            failure, outcome = CrawlFailure.ROBOTS_BLOCKED, AccessOutcome.REFUSED_ROBOTS
        elif decision.requires_licence:
            failure, outcome = CrawlFailure.SOURCE_RESTRICTED, AccessOutcome.REFUSED_NO_LICENCE
        else:
            failure, outcome = CrawlFailure.SOURCE_RESTRICTED, AccessOutcome.REFUSED_OUT_OF_SCOPE
        for url in request.search_urls or [""]:
            job.add_page(
                CrawlPage(
                    url=scrub_url(url),
                    failure=failure,
                    detail=decision.reason,
                    method="policy",
                )
            )
        job.notes.append(decision.reason)
        job.notes.extend(decision.evidence)
        job.status = CrawlStatus.BLOCKED
        job.finish(CrawlStatus.BLOCKED)
        self.audit.record(
            AccessEvent(
                source=request.source,
                outcome=outcome,
                operation="crawl",
                endpoint=scrub_url(request.search_urls[0]) if request.search_urls else "",
                reason=decision.reason,
                licence_id=decision.licence_id,
                actor="agent_import",
                credentials={"mode": decision.mode},
            )
        )
        return job

    # --- discovery (§10) ----------------------------------------------------
    def _discover(
        self, urls: list[str], request: CrawlRequest, job: CrawlJob, limiter: RateLimiter
    ) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        budget = min(request.max_requests, self.settings.crawl_max_requests)
        for url in urls:
            try:
                outcome = self._fetch_with_retries(limiter, url)
            except Exception as exc:  # RateLimitPause: the source asked us to stop
                job.add_page(
                    CrawlPage(url=scrub_url(url), failure=CrawlFailure.RATE_LIMITED, detail=str(exc))
                )
                job.status = CrawlStatus.BLOCKED
                job.notes.append(f"paused by source: {exc}")
                break

            page = CrawlPage(
                url=scrub_url(url),
                status_code=outcome.status_code,
                failure=outcome.failure,
                detail=outcome.detail,
                retryable=outcome.retryable,
            )
            if outcome.ok:
                ref = self.datalake.store(
                    request.source, "html", outcome.text, url=scrub_url(url), crawl_id=job.job_id
                )
                page.content_hash = ref.content_hash
                page.raw_object_id = ref.raw_object_id
                page.byte_size = ref.byte_size
                found = ex.discover_listing_urls(
                    outcome.text, base_url=url, url_patterns=request.url_patterns
                )
                page.listings_found = len(found)
                for link in found:
                    if link not in seen:
                        seen.add(link)
                        links.append(link)
                job.pages_discovered += 1
            job.add_page(page)

            if outcome.failure is CrawlFailure.SOURCE_RESTRICTED:
                job.status = CrawlStatus.BLOCKED
                job.notes.append("source refused access during discovery; stopping")
                break
            if outcome.failure is CrawlFailure.RATE_LIMITED:
                job.status = CrawlStatus.BLOCKED
                job.notes.append("rate limited during discovery; pausing instead of pushing")
                break
            if len(links) >= budget:
                job.notes.append(f"listing budget reached ({budget})")
                break
        return links[:budget]

    # --- navigation + extraction + validation (§11-14, §17) -----------------
    def _visit(
        self,
        links: list[str],
        request: CrawlRequest,
        job: CrawlJob,
        limiter: RateLimiter,
        before: set[str],
    ) -> None:
        limit = request.max_listings if request.max_listings is not None else len(links)
        for url in links[:limit]:
            try:
                outcome = self._fetch_with_retries(limiter, url)
            except Exception as exc:  # RateLimitPause
                job.add_page(
                    CrawlPage(url=scrub_url(url), failure=CrawlFailure.RATE_LIMITED, detail=str(exc))
                )
                job.status = CrawlStatus.BLOCKED
                job.notes.append(f"paused by source: {exc}")
                break

            page = CrawlPage(
                url=scrub_url(url),
                status_code=outcome.status_code,
                failure=outcome.failure,
                detail=outcome.detail,
                retryable=outcome.retryable,
            )
            if not outcome.ok:
                job.listings_failed += 1
                job.add_page(page)
                if outcome.failure is CrawlFailure.SOURCE_RESTRICTED:
                    job.status = CrawlStatus.BLOCKED
                    job.notes.append("source refused access on a listing page; stopping")
                    break
                if outcome.failure is CrawlFailure.RATE_LIMITED:
                    job.status = CrawlStatus.BLOCKED
                    job.notes.append("rate limited on a listing page; pausing")
                    break
                continue

            ref = self.datalake.store(
                request.source, "html", outcome.text, url=scrub_url(url), crawl_id=job.job_id
            )
            page.content_hash = ref.content_hash
            page.raw_object_id = ref.raw_object_id
            page.byte_size = ref.byte_size

            extraction = ex.extract_listing(
                outcome.text,
                url=url,
                id_patterns=request.id_patterns,
                url_patterns=request.url_patterns,
            )
            if not extraction.has_listing_data:
                fallback = render_if_needed(
                    url=url,
                    html=outcome.text,
                    deterministic_method=extraction.method,
                    renderer=self.renderer,
                    vision=self.vision,
                    settings=self.settings,
                )
                if fallback.reasons:
                    page.detail = "; ".join(fallback.reasons)
                if fallback.rendered is not None:
                    if fallback.rendered.screenshot:
                        shot = self.datalake.store(
                            request.source,
                            "screenshot",
                            fallback.rendered.screenshot,
                            url=scrub_url(url),
                            crawl_id=job.job_id,
                        )
                        page.detail += f"; screenshot {shot.content_hash[:12]}"
                    extraction = ex.extract_listing(
                        fallback.rendered.html,
                        url=url,
                        id_patterns=request.id_patterns,
                        url_patterns=request.url_patterns,
                    )
                for name, field_value in fallback.bundle.fields.items():
                    extraction.bundle.set(
                        name,
                        field_value.value,
                        method=field_value.method,
                        confidence=field_value.confidence,
                        evidence=field_value.evidence,
                    )

            if not extraction.has_listing_data:
                page.failure = (
                    CrawlFailure.SCHEMA_CHANGED
                    if len(outcome.text) > 2_000
                    else CrawlFailure.INVALID_LISTING
                )
                page.detail = page.detail or "no listing fields found on the page"
                job.listings_failed += 1
                job.add_page(page)
                continue

            if not self._store_extraction(request, job, extraction, url, page, before):
                job.add_page(page)
                continue

            page.listings_found = 1
            page.method = extraction.method
            job.add_page(page)

    def _store_extraction(
        self,
        request: CrawlRequest,
        job: CrawlJob,
        extraction: ex.PageExtraction,
        url: str,
        page: CrawlPage,
        before: set[str],
    ) -> bool:
        """Validate, then deposit the observation. False means 'not stored'."""
        bundle: ExtractionBundle = extraction.bundle
        ex.finalize_bundle(bundle, url=url, id_patterns=request.id_patterns)

        municipality = bundle.value("municipality") or bundle.value("municipality_raw")
        assessment = assess_territory(
            municipality=str(municipality) if municipality else None,
            province=bundle.value("province"),
            postal_code=bundle.value("postal_code"),
            address=bundle.value("address"),
            lat=bundle.value("lat"),
            lon=bundle.value("lon"),
            hints=extraction.title,
        )
        if not assessment.in_territory:
            page.failure = CrawlFailure.OUT_OF_TERRITORY
            page.detail = "; ".join(assessment.evidence)
            job.listings_failed += 1
            return False

        listing_id = bundle.value("source_listing_id")
        if not listing_id:
            listing_id = f"URL-{hashlib.sha1(url.encode()).hexdigest()[:12]}"

        flat = bundle.flatten()
        payload = normalize_listing(
            {
                **flat,
                "title": extraction.title,
                "comune": assessment.municipality or (str(municipality) if municipality else None),
                "description": flat.get("description") or flat.get("description_raw"),
                "measure_type": "asking",
            }
        )
        review_fields = sorted(
            set(bundle.low_confidence(self.settings.review_confidence_threshold))
            | set(bundle.missing(["price", "surface_m2"]))
        )
        record = self.store.observe(
            payload,
            source=request.source,
            source_listing_id=str(listing_id),
            url=url,
            observed_at=now_iso(),
            channel=ObservationSource.AGENT_IMPORT.value,
            raw_object_id=page.raw_object_id,
            territory=assessment,
            review_fields=review_fields,
        )
        if record.key in before:
            job.updated_listings += 1
        else:
            job.new_listings += 1
        job.listings_processed += 1
        if review_fields:
            job.review_required += 1
        return True

    # --- politeness (§27) ---------------------------------------------------
    def _fetch_with_retries(self, limiter: RateLimiter, url: str) -> FetchOutcome:
        """One polite request, with backoff. Never changes identity to retry."""
        attempt = 0
        while True:
            limiter.wait()
            outcome = self.transport.fetch(url)
            limiter.note_response(outcome.status_code, outcome.retry_after_s)
            transient = outcome.failure in (CrawlFailure.RATE_LIMITED, CrawlFailure.TIMEOUT) or (
                outcome.failure is CrawlFailure.HTTP_ERROR and outcome.retryable
            )
            if not transient or attempt >= limiter.max_retries:
                return outcome
            attempt += 1
            limiter.guard(outcome.retry_after_s)
            delay = (
                outcome.retry_after_s
                if outcome.retry_after_s is not None
                else limiter.backoff(attempt)
            )
            limiter.sleeper(delay)

    # --- full-crawl bookkeeping (§23) --------------------------------------
    def _mark_absent(self, seen_links: list[str], request: CrawlRequest) -> int:
        """After a full crawl, listings of this source no longer seen go inactive.

        Absence is recorded as ``removed``; it is never reported as a sale.
        """
        if not seen_links:
            return 0
        seen = set(seen_links)
        removed = 0
        for record in self.store.listings(active_only=True):
            if record.source != request.source or not record.url:
                continue
            if record.url in seen:
                continue
            self.store.observe(
                {**record.payload, "price": record.price, "surface_m2": record.surface_m2},
                source=record.source,
                source_listing_id=record.listing_source_id,
                url=record.url,
                observed_at=now_iso(),
                channel=ObservationSource.AGENT_IMPORT.value,
                availability="removed",
            )
            removed += 1
        return removed

    # --- reporting (§44) ---------------------------------------------------
    def health(self, job: CrawlJob) -> SourceHealthRecord:
        return SourceHealthRecord.from_job(job)
