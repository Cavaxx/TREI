"""Authorized-feed adapter: consume a source through a channel we may legally use.

This adapter exists for the two legitimate ways to obtain data from a source
that forbids ordinary crawling:

1. the grantor provides an **API** and the operator holds credentials for it
   (tier 1), or
2. the grantor provides an **export/feed** (HTTP pull, presigned link, or a file
   the operator receives and drops on disk) under a data agreement (tier 2).

It is *not* a scraper and cannot be turned into one. Three independent controls
apply before a single byte is requested:

* **Licence gate** - ``LicenceRegistry.assert_licensed()``. No active record in
  ``config/source_licences.yaml`` means no request, full stop.
* **Scope gate** - each item is checked against the *exact* URL/file prefixes the
  contract names (``LicenceRecord.covers_url``). A URL the contract does not
  name is refused even if it belongs to the same host.
* **Credential gate** - HTTP channels require operator-supplied credentials from
  the environment. Nothing is guessed, nothing is brute-forced.

robots.txt still applies by default. Skipping it requires a *double* opt-in: the
licence record sets ``robots_applies: false`` **and** the operator sets
``TREI_FEED_RESPECT_ROBOTS=false``. Both decisions are human, written down, and
recorded in the audit log. See ``docs/authorized_feeds.md``.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin

import httpx

from app.core.config import get_settings
from app.core.enums import SourceStatus
from app.core.lineage import make_lineage
from app.fetchers.adapters.parsing import dig, parse_payload
from app.fetchers.audit import AccessEvent, AccessLog, AccessOutcome, scrub_url
from app.fetchers.base import (
    FetchResult,
    RobotsDisallowed,
    SourceAdapter,
    SourceDegraded,
    content_hash,
)
from app.fetchers.credentials import (
    CredentialKind,
    Credentials,
    CredentialStore,
    MissingCredentials,
)
from app.fetchers.licence import LicenceRecord, LicenceRegistry, NotLicensed
from app.fetchers.registry import SourceConfig


class FeedScopeError(Exception):
    """Raised when an item is not covered by the licence's named endpoints."""


class AuthorizedFeedAdapter(SourceAdapter):
    """Pull a licensed API/export. Refuses anything the licence does not cover."""

    name = "authorized_feed"

    def __init__(
        self,
        config: SourceConfig,
        *,
        licences: LicenceRegistry | None = None,
        credentials: CredentialStore | None = None,
        audit: AccessLog | None = None,
        urls: list[str] | None = None,
        directory: Path | str | None = None,
        fmt: str = "json",
        records_path: str | None = None,
        record_tag: str | None = None,
        record_mapper=None,
        next_field: str | None = None,
        max_pages: int = 1,
        actor: str = "system",
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(config=config, client=client)
        self.name = config.name
        self.base_url = f"https://{config.domain}" if config.domain else ""
        self.fmt = fmt
        self.records_path = records_path
        self.record_tag = record_tag
        self.record_mapper = record_mapper or (lambda row: row)
        self.next_field = next_field
        self.max_pages = max(1, int(max_pages))
        self.actor = actor
        self._directory = Path(directory) if directory else None
        self._urls = list(urls or [])
        self._default_query: dict[str, str] = {}
        self.licences = licences if licences is not None else LicenceRegistry.load()
        self.credentials_store = credentials or CredentialStore()
        self.audit = audit if audit is not None else AccessLog(actor=actor)
        self.licence: LicenceRecord | None = None
        self.creds = Credentials()
        self.transport = "file" if self._directory else "http"

    # --- policy -------------------------------------------------------------
    def assert_allowed(self) -> None:
        """Licence gate first, then credential gate. Refusals are always logged."""
        if self.config is None:
            raise NotLicensed(f"{self.name}: no registry entry, so no licence can cover it")
        try:
            self.licence = self.licences.assert_licensed(self.name)
        except NotLicensed as exc:
            self.status = SourceStatus.LICENSE_REQUIRED
            self.audit.record(
                AccessEvent(
                    source=self.name,
                    outcome=AccessOutcome.REFUSED_NO_LICENCE,
                    transport=self.transport,
                    reason=str(exc),
                    actor=self.actor,
                )
            )
            raise

        if self.licence.max_requests_per_minute:
            contractual = 60.0 / float(self.licence.max_requests_per_minute)
            self._min_delay = max(self._min_delay, contractual)

        if not self.licence.allows_format(self.fmt):
            raise FeedScopeError(
                f"{self.name}: format {self.fmt!r} is not permitted by "
                f"{self.licence.licence_id} (allowed: {self.licence.permitted_formats})"
            )

        if self.transport == "http":
            try:
                self.creds = self.credentials_store.resolve(self.name)
            except MissingCredentials as exc:
                self.status = SourceStatus.LICENSE_REQUIRED
                self.audit.record(
                    AccessEvent(
                        source=self.name,
                        outcome=AccessOutcome.REFUSED_NO_CREDENTIALS,
                        licence_id=self.licence.licence_id,
                        grantor=self.licence.grantor,
                        transport=self.transport,
                        reason=str(exc),
                        actor=self.actor,
                    )
                )
                raise
            self._apply_credentials()
        else:
            # A file the operator legitimately received needs no credential.
            self.creds = self.credentials_store.optional(self.name)

    def _apply_credentials(self) -> None:
        headers = self.creds.headers()
        if headers:
            self.client.headers.update(headers)
        if self.creds.kind is CredentialKind.API_KEY and self.creds.api_key_query_param:
            self._default_query = self.creds.query()
        # Basic/oauth2 are passed as httpx auth handlers so secrets cannot leak
        # into request URLs (and therefore into logs).
        auth = self.creds.auth()
        if auth is not None:
            self.client.auth = auth

    def _assert_item_licensed(self, item: str) -> None:
        """Refuse any item the contract does not name, and log the refusal."""
        if self.licence is None or not self.licence.covers_url(item):
            self.audit.record(
                AccessEvent(
                    source=self.name,
                    outcome=AccessOutcome.REFUSED_OUT_OF_SCOPE,
                    licence_id=self.licence.licence_id if self.licence else "",
                    grantor=self.licence.grantor if self.licence else "",
                    endpoint=scrub_url(item),
                    transport=self.transport,
                    reason="endpoint not named in the licence",
                    actor=self.actor,
                )
            )
            raise FeedScopeError(
                f"{self.name}: {item} is not among the endpoints named by the licence"
            )

    def _robots_required(self) -> bool:
        """robots.txt applies unless BOTH the contract and the env waive it."""
        if get_settings().feed_respect_robots:
            return True
        return bool(self.licence and self.licence.robots_applies)

    def credential_summary(self) -> dict:
        """Redacted credential/transport description, safe to print or serve."""
        return {
            "transport": self.transport,
            "format": self.fmt,
            "credentials": self.creds.redacted(),
            "robots_applied": self._robots_required(),
        }

    # --- acquisition --------------------------------------------------------
    def fetch_urls(self) -> list[str]:
        """Seed items: licensed URLs (http) or ``file://`` refs into the drop dir."""
        if self._directory is not None:
            if not self._directory.exists():
                return []
            return [
                f"file://{p.resolve()}"
                for p in sorted(self._directory.rglob("*"))
                if p.is_file()
            ]
        return list(self._urls)

    def parse(self, item: str, payload: bytes) -> list[dict]:
        text = payload.decode("utf-8-sig", errors="replace")
        try:
            rows = parse_payload(self.fmt, text, self.records_path, self.record_tag)
        except (ValueError, csv.Error, SyntaxError, UnicodeDecodeError) as exc:
            raise SourceDegraded(f"cannot parse {item}: {exc}") from exc
        out: list[dict] = []
        for row in rows:
            mapped = self.record_mapper(row)
            if mapped:
                out.append(mapped)
        return out

    def _operation(self) -> str:
        if self.licence and self.licence.scope:
            return ",".join(self.licence.scope)
        return "read_listings"

    def _with_default_query(self, url: str) -> str:
        """Append api-key-in-query credentials, if the grantor requires that style."""
        if not self._default_query:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urlencode(self._default_query)}"

    def _request(self, url: str) -> httpx.Response:
        target = self._with_default_query(url)
        if self._robots_required():
            decision = self._robots.check(target)
            if not decision.allowed:
                self.health.record_blocked()
                raise RobotsDisallowed(f"robots.txt disallows {target}")
            if decision.crawl_delay:
                self._min_delay = max(self._min_delay, decision.crawl_delay)
        self._throttle()
        response = self.client.get(target)
        response.raise_for_status()
        return response

    def _read(self, item: str) -> bytes:
        if item.startswith("file://"):
            return Path(item[len("file://") :]).read_bytes()
        return self._request(item).content

    def _next_item(self, item: str, payload: bytes) -> str | None:
        """Follow the provider's own ``next`` link, if the licence covers it."""
        if item.startswith("file://") or not self.next_field:
            return None
        try:
            body = json.loads(payload.decode("utf-8-sig", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            return None
        candidate = dig(body, self.next_field)
        if not isinstance(candidate, str) or not candidate:
            return None
        return urljoin(item, candidate)

    def run(self) -> FetchResult:
        """Licence-gated acquisition with pagination and per-attempt audit."""
        self.assert_allowed()
        collected: list[dict] = []
        raw_all = bytearray()
        last_item = ""
        last_hash = ""
        seen: set[str] = set()
        pages_followed = 0
        queue = self.fetch_urls()

        for item in queue:
            if item in seen:
                continue
            seen.add(item)
            self._assert_item_licensed(item)
            # Anything persisted (audit log, lineage) must never carry an inline
            # credential, so scrub the URL once here and reuse it everywhere.
            safe_item = scrub_url(item)

            started = time.monotonic()
            try:
                payload = self._read(item)
            except RobotsDisallowed as exc:
                # Fail closed: a robots refusal is evidence too.
                self.audit.record(
                    AccessEvent(
                        source=self.name,
                        outcome=AccessOutcome.REFUSED_ROBOTS,
                        operation=self._operation(),
                        endpoint=safe_item,
                        transport=self.transport,
                        fmt=self.fmt,
                        licence_id=self.licence.licence_id if self.licence else "",
                        reason=str(exc),
                        actor=self.actor,
                    )
                )
                raise
            except httpx.HTTPError as exc:
                self.health.record_http_error((time.monotonic() - started) * 1000)
                self.status = SourceStatus.WARNING
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                self.audit.record(
                    AccessEvent(
                        source=self.name,
                        outcome=AccessOutcome.HTTP_ERROR,
                        operation=self._operation(),
                        endpoint=safe_item,
                        transport=self.transport,
                        fmt=self.fmt,
                        licence_id=self.licence.licence_id if self.licence else "",
                        http_status=status_code,
                        reason=str(exc),
                        actor=self.actor,
                    )
                )
                return FetchResult(
                    source=self.name,
                    status=self.status,
                    error=f"http error on {safe_item}: {exc}",
                    source_url=safe_item,
                )
            latency_ms = (time.monotonic() - started) * 1000

            try:
                records = self.parse(item, payload)
            except SourceDegraded as exc:
                self.health.record_parser_error()
                self.status = SourceStatus.DEGRADED
                self.audit.record(
                    AccessEvent(
                        source=self.name,
                        outcome=AccessOutcome.PARSER_ERROR,
                        operation=self._operation(),
                        endpoint=safe_item,
                        transport=self.transport,
                        fmt=self.fmt,
                        licence_id=self.licence.licence_id if self.licence else "",
                        reason=str(exc),
                        actor=self.actor,
                    )
                )
                return FetchResult(
                    source=self.name,
                    status=self.status,
                    error=f"parser degraded: {exc}",
                    source_url=safe_item,
                )

            digest = content_hash(payload)
            for record in records:
                record.setdefault("source", self.name)
                record["lineage"] = make_lineage(
                    self.name, source_url=safe_item, content_hash=digest
                ).to_dict()
            collected.extend(records)
            raw_all.extend(payload)
            last_item, last_hash = safe_item, digest
            self.health.record_success(latency_ms, len(records))
            self.audit.record(
                AccessEvent(
                    source=self.name,
                    outcome=AccessOutcome.ALLOWED,
                    operation=self._operation(),
                    endpoint=safe_item,
                    transport=self.transport,
                    fmt=self.fmt,
                    licence_id=self.licence.licence_id if self.licence else "",
                    grantor=self.licence.grantor if self.licence else "",
                    http_status=None if item.startswith("file://") else 200,
                    records=len(records),
                    content_hash=digest,
                    credentials=self.creds.redacted(),
                    actor=self.actor,
                )
            )

            following = self._next_item(item, payload)
            if following and pages_followed < self.max_pages - 1:
                pages_followed += 1
                queue.append(following)

        self.status = SourceStatus.OK
        return FetchResult(
            source=self.name,
            records=collected,
            raw_object=bytes(raw_all) if raw_all else None,
            content_hash=last_hash,
            source_url=last_item,
            status=self.status,
        )
