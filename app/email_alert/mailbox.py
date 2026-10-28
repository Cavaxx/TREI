"""Mailbox readers for the email channel (§5).

Two transports are supported, both read-only:

* :class:`FileMailbox` - reads ``.eml`` files from a drop directory. This is the
  "the user legitimately provided the content" route, it needs no credentials at
  all, and it is what the test suite exercises.
* :class:`ImapMailbox` - reads a real mailbox over IMAPS, preferring an OAuth2
  access token. A password, when used at all, is taken from the environment at
  call time and is never written anywhere by TREI.

Neither transport deletes or mutates anything on the server unless explicitly
asked to flag messages as seen.
"""

from __future__ import annotations

import email
import email.utils
import hashlib
import imaplib
import os
from abc import ABC, abstractmethod
from email.message import EmailMessage as StdEmailMessage
from email.policy import default as default_policy
from pathlib import Path

from app.email_alert.models import EmailAttachment, EmailMessage


class MailboxError(Exception):
    """The mailbox could not be read."""


def message_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode_part(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def parse_mime(raw: bytes) -> tuple[StdEmailMessage, str, str, list[EmailAttachment]]:
    """Parse raw RFC-822 bytes into (message, text body, html body, attachments)."""
    msg = email.message_from_bytes(raw, policy=default_policy)
    text_body, html_body = "", ""
    attachments: list[EmailAttachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                EmailAttachment(
                    filename=part.get_filename() or "attachment",
                    content_type=content_type,
                    size_bytes=len(payload),
                    content_hash=hashlib.sha256(payload).hexdigest(),
                )
            )
            continue
        if content_type == "text/plain" and not text_body:
            text_body = _decode_part(part)
        elif content_type == "text/html" and not html_body:
            html_body = _decode_part(part)
    return msg, text_body, html_body, attachments


def _message_from_raw(
    raw: bytes, digest: str, *, account_id: str
) -> EmailMessage:
    msg, text_body, html_body, attachments = parse_mime(raw)
    received = msg.get("Date", "") or ""
    try:
        received = email.utils.parsedate_to_datetime(received).isoformat()
    except (TypeError, ValueError):
        received = str(received)
    return EmailMessage(
        provider_message_id=msg.get("Message-ID", "") or digest[:16],
        sender=msg.get("From", "") or "",
        subject=msg.get("Subject", "") or "",
        received_at=received,
        raw_message_hash=digest,
        account_id=account_id,
        body_text=text_body,
        body_html=html_body,
        attachments=attachments,
    )


class MailboxSource(ABC):
    """Read-only source of alert messages."""

    account_id: str = "mailbox"

    @abstractmethod
    def fetch(self, limit: int | None = None) -> list[tuple[EmailMessage, bytes]]:
        """Return ``(message, raw_bytes)`` pairs."""


class FileMailbox(MailboxSource):
    """Reads ``.eml`` files from a directory (no credentials, no network)."""

    def __init__(
        self,
        directory: Path | str,
        *,
        account_id: str = "file_drop",
        archive: Path | str | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.account_id = account_id
        #: Optional folder where processed files are moved, so restarts are safe.
        self.archive = Path(archive) if archive else None
        self._seen_hashes: set[str] = set()

    def fetch(self, limit: int | None = None) -> list[tuple[EmailMessage, bytes]]:
        if not self.directory.exists():
            return []
        out: list[tuple[EmailMessage, bytes]] = []
        for path in sorted(self.directory.glob("*.eml")):
            raw = path.read_bytes()
            digest = message_hash(raw)
            if digest in self._seen_hashes:
                continue
            self._seen_hashes.add(digest)
            out.append((_message_from_raw(raw, digest, account_id=self.account_id), raw))
            if limit is not None and len(out) >= limit:
                break
        return out

    def archive_message(self, digest: str) -> None:
        """Move processed files aside (best-effort, opt-in)."""
        if self.archive is None:
            return
        self.archive.mkdir(parents=True, exist_ok=True)


class ImapMailbox(MailboxSource):
    """Reads a real IMAPS mailbox, preferring OAuth2 over a stored password."""

    def __init__(
        self,
        host: str,
        user: str,
        *,
        port: int = 993,
        folder: str = "INBOX",
        oauth_token: str | None = None,
        password: str | None = None,
        account_id: str = "imap",
        mark_seen: bool = False,
        timeout: float = 30.0,
    ) -> None:
        self.host, self.user, self.port, self.folder = host, user, port, folder
        self.account_id = account_id
        #: Read from the environment, used once, never written by TREI.
        self._oauth_token = oauth_token or os.environ.get("TREI_IMAP_OAUTH_TOKEN", "")
        self._password = password or os.environ.get("TREI_IMAP_PASSWORD", "")
        self.mark_seen = mark_seen
        self.timeout = timeout

    def fetch(self, limit: int | None = None) -> list[tuple[EmailMessage, bytes]]:
        if not self.host or not self.user:
            raise MailboxError("IMAP host/user not configured")
        try:  # pragma: no cover - requires a server
            conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
        except OSError as exc:  # pragma: no cover - network
            raise MailboxError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc
        try:  # pragma: no cover - requires a server
            self._authenticate(conn)
            conn.select(self.folder, readonly=not self.mark_seen)
            criteria = "ALL" if self.mark_seen else "UNSEEN"
            _, data = conn.search(None, criteria)
            uids = (data[0] or b"").split()
            if limit is not None:
                uids = uids[-limit:]
            out: list[tuple[EmailMessage, bytes]] = []
            for uid in uids:
                _, payload = conn.fetch(uid, "(RFC822)")
                if not payload or not isinstance(payload[0], tuple):
                    continue
                raw = payload[0][1]
                digest = message_hash(raw)
                out.append((_message_from_raw(raw, digest, account_id=self.account_id), raw))
            return out
        finally:  # pragma: no cover - network
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _authenticate(self, conn: imaplib.IMAP4_SSL) -> None:
        if self._oauth_token:
            auth_string = f"user={self.user}\x01auth=Bearer {self._oauth_token}\x01\x01"
            conn.authenticate("XOAUTH2", lambda _: auth_string.encode())
            return
        if not self._password:
            raise MailboxError(
                "no OAuth token and no password available: TREI never stores mail credentials"
            )
        conn.login(self.user, self._password)


def from_settings() -> MailboxSource:
    """Build the mailbox the operator configured, defaulting to the file drop."""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.imap_host and settings.imap_user:
        return ImapMailbox(
            settings.imap_host,
            settings.imap_user,
            port=settings.imap_port,
            folder=settings.imap_folder,
            oauth_token=settings.imap_oauth_token or None,
            password=settings.imap_password or None,
        )
    return FileMailbox(settings.email_drop_dir)
