"""Licence / permission register for authorized data feeds.

A source can be consumed automatically through exactly two legal channels:

1. **Open data** - a public dataset whose licence permits reuse (tier 3).
2. **Authorized feed** - an API or export the operator is *contractually*
   entitled to consume, using credentials the operator legitimately holds
   (tier 1/2).

This module governs channel 2, and its rule is absolute and enforced in code:

    no active licence record  ->  no automated request

A licence record is necessarily human-authored: there is no technical signal
that says "you are allowed to call this API". So the person who signs the
contract writes down what may be fetched (``endpoints``), what may be done
(``scope``), for how long, and under which attribution obligations. The runtime
then refuses everything that is not covered. See ``docs/authorized_feeds.md``.

This is deliberately the *opposite* of an evasion layer: access is narrowed to
what was granted, not widened past what was refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from app.core.config import get_settings


class NotLicensed(Exception):
    """Raised when an authorized feed is used without a covering licence."""


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"invalid ISO date in licence register: {raw!r}") from exc


@dataclass
class LicenceRecord:
    """One signed agreement granting automated access to one source."""

    source: str
    grantor: str
    contract_ref: str = ""
    valid_from: str = ""
    valid_to: str | None = None
    #: URL prefixes the contract explicitly names. Empty => nothing is covered.
    endpoints: list[str] = field(default_factory=list)
    #: Permitted operations, e.g. read_listings / read_media_metadata.
    scope: list[str] = field(default_factory=list)
    permitted_formats: list[str] = field(default_factory=lambda: ["json", "csv", "xml", "jsonl"])
    attribution: str = ""
    contact: str = ""
    #: Whether robots.txt still applies to the licensed endpoints. Conservative
    #: default is True; set to False only when the contract explicitly grants it.
    robots_applies: bool = True
    #: Contractual ceiling, if the agreement states one.
    max_requests_per_minute: float | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def licence_id(self) -> str:
        ref = self.contract_ref or "unref"
        return f"{self.source}:{self.grantor}:{ref}"

    def is_active(self, on: date | None = None) -> bool:
        on = on or date.today()
        start = _parse_date(self.valid_from)
        end = _parse_date(self.valid_to)
        if start is not None and on < start:
            return False
        if end is not None and on > end:
            return False
        return True

    def validity_status(self, on: date | None = None) -> str:
        on = on or date.today()
        start = _parse_date(self.valid_from)
        end = _parse_date(self.valid_to)
        if start is not None and on < start:
            return "not_yet_valid"
        if end is not None and on > end:
            return "expired"
        return "active"

    def covers_url(self, url: str) -> bool:
        """True only if the contract explicitly names this URL/prefix."""
        clean = url.split("?")[0]
        return any(clean.startswith(p.rstrip("*")) for p in self.endpoints if p)

    def allows_format(self, fmt: str) -> bool:
        return fmt.lower() in {f.lower() for f in self.permitted_formats}

    def to_dict(self) -> dict:
        return {
            "licence_id": self.licence_id,
            "source": self.source,
            "grantor": self.grantor,
            "contract_ref": self.contract_ref,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "validity_status": self.validity_status(),
            "endpoints": list(self.endpoints),
            "scope": list(self.scope),
            "permitted_formats": list(self.permitted_formats),
            "attribution": self.attribution,
            "robots_applies": self.robots_applies,
            "max_requests_per_minute": self.max_requests_per_minute,
        }



class LicenceRegistry:
    """Typed access to the human-authored licence register."""

    def __init__(self, licences: list[LicenceRecord]) -> None:
        self._licences = list(licences)

    @classmethod
    def load(cls, path: Path | None = None) -> LicenceRegistry:
        path = Path(path or get_settings().licence_path)
        if not path.exists():
            # A missing register means "nothing is licensed" - never an error
            # that a caller could mistake for "allowed".
            return cls([])
        data = yaml.safe_load(path.read_text()) or {}
        raw = data.get("licences", []) if isinstance(data, dict) else data
        return cls([cls._from_dict(d) for d in (raw or [])])

    @staticmethod
    def _from_dict(d: dict) -> LicenceRecord:
        known = set(LicenceRecord.__dataclass_fields__)
        kwargs = {k: v for k, v in d.items() if k in known}
        kwargs.setdefault("grantor", "")
        record = LicenceRecord(**kwargs)
        record.extra = {k: v for k, v in d.items() if k not in known}
        return record

    def all(self) -> list[LicenceRecord]:
        return list(self._licences)

    def for_source(self, source: str) -> list[LicenceRecord]:
        return [lic for lic in self._licences if lic.source == source]

    def active_for(self, source: str, on: date | None = None) -> LicenceRecord | None:
        for lic in self.for_source(source):
            if lic.is_active(on) and lic.endpoints:
                return lic
        return None

    def status(self, source: str, on: date | None = None) -> str:
        """One of: active | not_yet_valid | expired | no_endpoints | missing."""
        records = self.for_source(source)
        if not records:
            return "missing"
        active = [lic for lic in records if lic.validity_status(on) == "active"]
        if active:
            return "active" if any(lic.endpoints for lic in active) else "no_endpoints"
        states = {lic.validity_status(on) for lic in records}
        if "not_yet_valid" in states:
            return "not_yet_valid"
        if "expired" in states:
            return "expired"
        return "missing"

    def assert_licensed(self, source: str, on: date | None = None) -> LicenceRecord:
        """Return the covering licence, or refuse with an explicit reason."""
        state = self.status(source, on)
        if state == "active":
            record = self.active_for(source, on)
            if record is not None:
                return record
            state = "no_endpoints"
        reasons = {
            "missing": "no licence recorded in config/source_licences.yaml",
            "expired": "the recorded licence has expired - renew before fetching",
            "not_yet_valid": "the recorded licence is not in force yet",
            "no_endpoints": "the licence names no endpoints, so nothing is covered",
        }
        raise NotLicensed(f"{source}: {reasons.get(state, state)}")

    def to_dicts(self) -> list[dict]:
        return [lic.to_dict() for lic in self._licences]
