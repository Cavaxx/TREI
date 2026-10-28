"""Channel C - Email alerts (§4-7).

Portal alert emails are turned into structured observations. This is often the
*first* channel to see a property, so its timestamps are preserved separately
(``first_seen_at`` / ``email_received_at`` / ``source_published_at``).

Nothing here stores or requests a user password; OAuth2 is preferred.
"""

from app.email_alert.ingest import EmailIngestPipeline, EmailIngestReport
from app.email_alert.mailbox import (
    FileMailbox,
    ImapMailbox,
    MailboxError,
    MailboxSource,
    from_settings,
)
from app.email_alert.models import (
    EmailAccount,
    EmailAttachment,
    EmailListingCandidate,
    EmailMessage,
)
from app.email_alert.parser import EmailListingParser, ParsedEmail
from app.email_alert.profiles import PROFILES, PortalProfile, profile_for

__all__ = [
    "PROFILES",
    "EmailAccount",
    "EmailAttachment",
    "EmailIngestPipeline",
    "EmailIngestReport",
    "EmailListingCandidate",
    "EmailListingParser",
    "EmailMessage",
    "FileMailbox",
    "ImapMailbox",
    "MailboxError",
    "MailboxSource",
    "ParsedEmail",
    "PortalProfile",
    "from_settings",
    "profile_for",
]
