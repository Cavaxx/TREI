"""Append-only evidence log for authorized-feed access (compliance due diligence).

Every attempt to read a licensed source is recorded - including the ones we
*refuse*. That is the point: the log is the artefact that demonstrates, after
the fact, that the platform only ever touched what its contracts allowed.

Records are JSON Lines, one event per line, never rewritten. Secret material is
never logged: only the credential *kind* and the *name* of the header used.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.config import get_settings


class AccessOutcome(str, Enum):
    """Why an access attempt ended the way it did."""

    ALLOWED = "allowed"
    REFUSED_NO_LICENCE = "refused_no_licence"
    REFUSED_OUT_OF_SCOPE = "refused_out_of_scope"
    REFUSED_NO_CREDENTIALS = "refused_no_credentials"
    REFUSED_ROBOTS = "refused_robots"
    HTTP_ERROR = "http_error"
    PARSER_ERROR = "parser_error"


#: A key whose name contains one of these is treated as secret material.
_SECRET_HINTS = ("key", "token", "secret", "password", "authorization", "cookie", "credential")

#: ...unless it also carries a descriptive suffix. The *name* of the header we
#: sent is not itself a secret, and losing it would make the log less useful.
_SAFE_SUFFIXES = ("_header", "_name", "_env", "_var", "_id", "_present", "_configured", "_kind")

#: Query-string parameter names that may carry a credential.
_SECRET_QUERY_HINTS = ("key", "token", "secret", "password", "auth", "sig", "credential")


def is_secret_key(key: str) -> bool:
    """True when a dict key most likely holds secret material."""
    lowered = key.lower()
    if lowered.startswith("has_") or lowered.endswith(_SAFE_SUFFIXES):
        return False
    return any(hint in lowered for hint in _SECRET_HINTS)


def redact(payload: dict | None) -> dict | None:
    """Recursively mask anything that looks like a credential."""
    if payload is None:
        return None
    out: dict = {}
    for key, value in payload.items():
        if is_secret_key(key):
            out[key] = "***redacted***"
        elif isinstance(value, dict):
            out[key] = redact(value)
        elif isinstance(value, list):
            out[key] = [redact(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    return out


def scrub_url(url: str) -> str:
    """Mask credential-looking query values.

    Some grantors accept the API key as a query parameter; those URLs must never
    reach the evidence log (or a stored lineage record) verbatim.
    """
    if not url or "?" not in url:
        return url
    parts = urlsplit(url)
    pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if any(hint in key.lower() for hint in _SECRET_QUERY_HINTS):
            pairs.append((key, "***redacted***"))
        else:
            pairs.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class AccessEvent:
    """One access attempt against one licensed endpoint."""

    source: str
    outcome: AccessOutcome
    operation: str = "read_listings"
    endpoint: str = ""
    transport: str = "http"
    fmt: str = ""
    licence_id: str = ""
    grantor: str = ""
    http_status: int | None = None
    records: int = 0
    content_hash: str | None = None
    reason: str = ""
    #: Who performed the access. Left empty so AccessLog can supply its default.
    actor: str = ""
    user_agent: str = ""
    credentials: dict | None = None
    occurred_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        data["credentials"] = redact(self.credentials)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


class AccessLog:
    """JSONL evidence log. Writes are appended; nothing is ever overwritten."""

    def __init__(self, path: Path | None = None, actor: str = "system") -> None:
        self.path = Path(path) if path is not None else get_settings().audit_log_path
        self.actor = actor

    def record(self, event: AccessEvent) -> dict:
        """Append one event and return the written (redacted) dict."""
        if not event.actor:
            event.actor = self.actor
        if not event.user_agent:
            event.user_agent = get_settings().user_agent
        payload = event.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def refused(self) -> list[dict]:
        return [e for e in self.entries() if str(e.get("outcome", "")).startswith("refused_")]

    def for_source(self, source: str) -> list[dict]:
        return [e for e in self.entries() if e.get("source") == source]
