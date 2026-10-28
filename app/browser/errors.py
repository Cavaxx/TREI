"""Failure taxonomy for browser/acquisition runs.

A Kernel (SDK) 401/403/429 is a *provider* problem and must never be confused
with a 401/403 returned by the site being fetched. The two are distinct codes
here so the operational report can tell them apart.
"""

from __future__ import annotations

from enum import Enum


class BrowserFailure(str, Enum):
    """Typed outcome of a browser/acquisition attempt."""

    CONFIGURATION_MISSING = "configuration_missing"
    PROVIDER_AUTH = "provider_auth"  # Kernel SDK 401/403
    PROVIDER_RATE_LIMITED = "provider_rate_limited"  # Kernel SDK 429
    SOURCE_BLOCKED = "source_blocked"  # the site itself refused (401/403)
    TIMEOUT = "timeout"
    FORMAT_CHANGED = "format_changed"
    VALIDATION_FAILED = "validation_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CLEANUP_FAILED = "cleanup_failed"
    NETWORK_ERROR = "network_error"


class BrowserProviderError(Exception):
    """A typed browser/acquisition failure. ``detail`` may carry a session id
    only when it is needed for a later, limited cleanup recovery."""

    def __init__(
        self,
        failure: BrowserFailure,
        message: str,
        *,
        source: str = "",
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.source = source
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "failure": self.failure.value,
            "message": str(self),
            "source": self.source,
            "detail": self.detail,
        }
