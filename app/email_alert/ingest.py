"""Email ingestion pipeline: mailbox -> parser -> normalizer -> Property Master.

The order is exactly the one the specification draws (§4.1)::

    EmailFetcher -> EmailParser -> ListingNormalizer -> PropertyMatcher -> PropertyMaster

The pipeline never mutates what it read: the raw message is written to the data
lake first, and the structured record carries a link to it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.enums import ObservationSource
from app.email_alert.mailbox import MailboxSource, from_settings
from app.email_alert.models import EmailMessage
from app.email_alert.parser import EmailListingParser
from app.processors.normalize import normalize_listing
from app.processors.territory import assess_territory
from app.property_master.master import PropertyMasterStore
from app.storage.datalake import DataLake

#: Fields that must be present for an email-sourced listing to be usable.
REQUIRED_FIELDS = ("price", "surface_m2", "municipality")


@dataclass
class EmailIngestReport:
    account_id: str
    messages_seen: int = 0
    messages_duplicate: int = 0
    messages_parsed: int = 0
    candidates: int = 0
    stored: int = 0
    out_of_territory: int = 0
    review_required: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    listings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class EmailIngestPipeline:
    """Reads a mailbox and deposits observations in the Property Master."""

    def __init__(
        self,
        store: PropertyMasterStore,
        *,
        mailbox: MailboxSource | None = None,
        parser: EmailListingParser | None = None,
        datalake: DataLake | None = None,
        channel: str = ObservationSource.EMAIL_ALERT.value,
        review_threshold: float | None = None,
    ) -> None:
        self.store = store
        self.mailbox = mailbox or from_settings()
        self.parser = parser or EmailListingParser()
        self.datalake = datalake or DataLake()
        self.channel = channel
        self.review_threshold = (
            review_threshold
            if review_threshold is not None
            else get_settings().review_confidence_threshold
        )
        self._seen_messages: set[str] = set()
        #: Alert mails are frequently re-delivered with the same Message-ID but a
        #: different transport header; identity is the provider id, not the bytes.
        self._seen_message_ids: set[str] = set()

    # --- public -------------------------------------------------------------
    def run(self, limit: int | None = None) -> EmailIngestReport:
        report = EmailIngestReport(account_id=getattr(self.mailbox, "account_id", "mailbox"))
        for message, raw in self.mailbox.fetch(limit=limit):
            report.messages_seen += 1
            if (
                message.raw_message_hash in self._seen_messages
                or message.provider_message_id in self._seen_message_ids
            ):
                message.duplicate = True
                report.messages_duplicate += 1
                continue
            self._seen_messages.add(message.raw_message_hash)
            self._seen_message_ids.add(message.provider_message_id)
            try:
                self._ingest_one(message, raw, report)
            except Exception as exc:  # noqa: BLE001 - one bad message must not stop the batch
                report.errors.append(f"{message.provider_message_id}: {exc}")
        self.store.resolve()
        return report

    # --- internals ----------------------------------------------------------
    def _ingest_one(self, message: EmailMessage, raw: bytes, report: EmailIngestReport) -> None:
        crawl_id = message.raw_message_hash[:12]
        ref = self.datalake.store("email", "response", raw, crawl_id=crawl_id)
        ref_html = None
        if message.body_html:
            ref_html = self.datalake.store("email", "html", message.body_html, crawl_id=crawl_id)
        message.raw_object_id = ref.raw_object_id

        received = _parse_ts(message.received_at)
        if received and received > datetime.now(UTC):
            report.warnings.append(
                f"{message.provider_message_id}: received_at is in the future; kept verbatim"
            )

        parsed = self.parser.parse(message)
        report.messages_parsed += 1
        for warning in parsed.warnings:
            report.warnings.append(f"{message.provider_message_id}: {warning}")

        for candidate in parsed.candidates:
            report.candidates += 1
            payload = candidate.to_payload()
            payload["comune"] = payload.get("municipality")
            normalized = normalize_listing(payload)
            assessment = assess_territory(
                municipality=normalized.get("comune") or payload.get("municipality"),
                postal_code=payload.get("postal_code"),
                address=normalized.get("address"),
                province=payload.get("province"),
                hints=candidate.subject,
            )
            if not assessment.in_territory:
                report.out_of_territory += 1

            review_fields = self._review_fields(candidate)
            if review_fields:
                report.review_required += 1

            record = self.store.observe(
                normalized,
                source=candidate.source,
                source_listing_id=candidate.source_listing_id,
                url=candidate.url,
                channel=self.channel,
                raw_object_id=ref_html.raw_object_id if ref_html else ref.raw_object_id,
                territory=assessment,
                review_fields=review_fields,
            )
            report.stored += 1
            report.listings.append(
                {
                    "listing_key": record.key,
                    "source": record.source,
                    "source_listing_id": record.listing_source_id,
                    "first_seen_at": record.first_seen_at,
                    "email_received_at": record.email_received_at,
                    "review_required": record.review_required,
                    "in_territory": assessment.in_territory,
                    "territory_confidence": assessment.confidence.value,
                }
            )

    def _review_fields(self, candidate) -> list[str]:
        """Fields below the confidence threshold, plus required fields missing."""
        low = candidate.fields.low_confidence(self.review_threshold)
        missing = candidate.fields.missing(list(REQUIRED_FIELDS))
        return sorted(set(low) | set(missing))
