"""Email-alert domain objects (§5-7).

The email channel is the *first observation* channel: it can see a listing before
a crawl would, so the three timestamps are kept strictly apart:

``first_seen_at``      when TREI recorded the observation
``email_received_at``  when the alert arrived in the mailbox
``source_published_at`` what the portal claimed (may be absent or unreliable)

The user's credentials are never stored here: an :class:`EmailAccount` records
*how* to reach the mailbox (and which OAuth/OAuth-free mechanism was used), not
any secret.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.core.enums import ObservationSource
from app.core.fields import (
    CANONICAL_FIELDS,
    OPTIONAL_FIELDS,
    ExtractionBundle,
    bundle_to_payload,
)

__all__ = [
    "CANONICAL_FIELDS",
    "OPTIONAL_FIELDS",
    "EmailAccount",
    "EmailAttachment",
    "EmailListingCandidate",
    "EmailMessage",
]


@dataclass
class EmailAccount:
    """A mailbox the platform is allowed to read. Holds no secret material."""

    account_id: str
    provider: str = "file_drop"  # imap | gmail_api | graph | file_drop
    address: str = ""
    folder: str = "INBOX"
    active: bool = True
    #: True when the mailbox is read through an OAuth grant (preferred, §5).
    uses_oauth: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EmailAttachment:
    filename: str
    content_type: str = ""
    size_bytes: int = 0
    content_hash: str = ""
    raw_object_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EmailMessage:
    """One ingested alert message."""

    provider_message_id: str
    sender: str
    subject: str
    received_at: str
    #: Portal the alert belongs to, resolved from the sender/profile table.
    source: str = ""
    raw_message_hash: str = ""
    account_id: str = ""
    body_text: str = ""
    body_html: str = ""
    attachments: list[EmailAttachment] = field(default_factory=list)
    #: True when this message (or a message with the same content) was seen before.
    duplicate: bool = False
    raw_object_id: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["attachments"] = [a.to_dict() for a in self.attachments]
        return data


@dataclass
class EmailListingCandidate:
    """One listing extracted from one alert message."""

    source: str
    source_listing_id: str
    url: str = ""
    fields: ExtractionBundle = field(default_factory=ExtractionBundle)
    images: list[str] = field(default_factory=list)
    email_received_at: str = ""
    email_message_hash: str = ""
    account_id: str = ""
    sender: str = ""
    subject: str = ""
    raw_object_id: str = ""
    observed_via: str = ObservationSource.EMAIL_ALERT.value

    def to_payload(self) -> dict:
        """The normalizable listing payload, in the canonical §6 shape."""
        return bundle_to_payload(
            self.fields,
            source=self.source,
            source_listing_id=self.source_listing_id,
            url=self.url,
            images=self.images,
            extra={
                "email_received_at": self.email_received_at,
                "first_seen_source": self.observed_via,
                "mail_account_id": self.account_id,
                "email_message_hash": self.email_message_hash,
                "raw_object_id": self.raw_object_id,
            },
        )

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_listing_id": self.source_listing_id,
            "url": self.url,
            "images": list(self.images),
            "email_received_at": self.email_received_at,
            "email_message_hash": self.email_message_hash,
            "account_id": self.account_id,
            "sender": self.sender,
            "subject": self.subject,
            "raw_object_id": self.raw_object_id,
            "observed_via": self.observed_via,
            "fields": self.fields.to_dict(),
        }
