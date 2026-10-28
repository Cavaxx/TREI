"""Kernel browser adapter (Playwright Execution mode).

Implements the run lifecycle the integration requires:

* the API key is checked BEFORE the Kernel client is constructed — a missing key
  raises ``configuration_missing`` and never opens a session or makes a paid call;
* a session is always released in ``close()`` / a ``finally``, including on
  parse, cancel, timeout and download errors, and ``delete_by_id`` (not a local
  Playwright ``close``) is what actually destroys the remote browser;
* the provider's ``timeout_seconds`` (standby) stays distinct from the
  application wall-clock deadline and the run's cumulative budget;
* downloads are pulled through ``fs.read_file`` while the session is still alive
  (remote files do not survive destruction) and validated before release;
* cleanup failure preserves the session id in the raised error so a limited,
  run-scoped recovery can pick it up later without touching foreign sessions.

The ``kernel`` import is lazy so the core and offline tests work without the SDK.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.browser.budget import RunBudget
from app.browser.errors import BrowserFailure, BrowserProviderError
from app.core.config import Settings, get_settings


def _monotonic() -> float:
    return time.monotonic()


@dataclass
class BrowserResult:
    """Normalized result of one Playwright Execution call."""

    success: bool
    result: Any = None
    error: str = ""
    stderr: str = ""
    stdout: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "stderr": self.stderr,
            "stdout": self.stdout,
        }


@dataclass
class KernelSession:
    """One open remote browser, released in ``close()``."""

    session_id: str
    created_at: float
    deadline_at: float
    provider: KernelBrowserProvider
    name: str = ""
    closed: bool = field(default=False, init=False)

    def close(self) -> None:
        self.provider.release(self)


class KernelBrowserProvider:
    """Thin, mockable adapter over the ``kernel`` SDK (Playwright Execution)."""

    def __init__(self, settings: Settings | None = None, budget: RunBudget | None = None) -> None:
        self.settings = settings or get_settings()
        self.budget = budget or RunBudget.from_settings(self.settings)
        self._client = None

    # -- configuration ------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.settings.kernel_api_key)

    def require_configured(self) -> None:
        if not self.configured:
            raise BrowserProviderError(
                BrowserFailure.CONFIGURATION_MISSING,
                "KERNEL_API_KEY is not set; refusing to open a browser session",
            )

    def _client_checked(self):
        self.require_configured()
        if self._client is None:
            from kernel import Kernel

            # max_retries=0: the SDK must not silently retry 429s for us; we
            # account every creation attempt and decide what is safe to retry.
            self._client = Kernel(api_key=self.settings.kernel_api_key, max_retries=0)
        return self._client

    # -- session lifecycle --------------------------------------------------
    def open_session(
        self,
        *,
        name: str = "",
        start_url: str | None = None,
        headless: bool = True,
        stealth: bool = False,
    ) -> KernelSession:
        """Create a session, reserving budget.

        ``stealth`` is OFF by default: it enables Kernel's CAPTCHA solver and
        stealth proxy, which this project never turns on automatically (a
        blocked source is recorded, not evaded)."""
        self._assert_allowed_domain(start_url)
        client = self._client_checked()  # raises configuration_missing before any budget is reserved
        wall_seconds = self.budget.register_open()
        kwargs: dict[str, Any] = {
            "timeout_seconds": self.settings.kernel_standby_timeout_seconds,
            "headless": headless,
            "stealth": stealth,
        }
        if name:
            kwargs["name"] = name
        if start_url:
            kwargs["start_url"] = start_url
        try:
            browser = client.browsers.create(**kwargs)
        except BrowserProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify and re-raise typed
            raise self._classify(exc) from exc

        now = _monotonic()
        return KernelSession(
            session_id=browser.session_id,
            created_at=now,
            deadline_at=now + wall_seconds,
            provider=self,
            name=name,
        )

    def release(self, session: KernelSession) -> None:
        """Record wall time and delete the remote browser.

        Only sessions created by this run are ever deleted. A failure keeps the
        session id in the raised error (``detail``) for a limited recovery."""
        if session.closed:
            return
        session.closed = True
        self.budget.record_wall(_monotonic() - session.created_at)
        try:
            self._client_checked().browsers.delete_by_id(session.session_id)
        except BrowserProviderError as exc:
            raise BrowserProviderError(
                BrowserFailure.CLEANUP_FAILED,
                f"session {session.session_id} not deleted: {exc}",
                detail=session.session_id,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise BrowserProviderError(
                BrowserFailure.CLEANUP_FAILED,
                f"session {session.session_id} not deleted: {exc}",
                detail=session.session_id,
            ) from exc

    # -- operations ---------------------------------------------------------
    def execute(
        self,
        session: KernelSession,
        code: str,
        *,
        timeout_sec: int | None = None,
    ) -> BrowserResult:
        self._assert_wall_ok(session)
        client = self._client_checked()
        t = timeout_sec if timeout_sec is not None else max(1, int(self._navigation_timeout_sec()))
        # Never exceed the session's remaining wall-clock budget.
        t = min(t, max(1, int(session.deadline_at - _monotonic())))
        try:
            resp = client.browsers.playwright.execute(session.session_id, code=code, timeout_sec=t)
        except BrowserProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
        return BrowserResult(
            success=bool(resp.success),
            result=resp.result,
            error=resp.error or "",
            stderr=resp.stderr or "",
            stdout=resp.stdout or "",
        )

    def list_files(self, session: KernelSession, path: str = "/tmp") -> list[dict]:
        self._assert_wall_ok(session)
        client = self._client_checked()
        try:
            items = client.browsers.fs.list_files(session.session_id, path=path)
        except BrowserProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
        return [
            {
                "name": item.name,
                "path": item.path,
                "is_dir": item.is_dir,
                "size_bytes": item.size_bytes,
            }
            for item in items
        ]

    def read_file(self, session: KernelSession, path: str) -> bytes:
        self._assert_wall_ok(session)
        client = self._client_checked()
        try:
            resp = client.browsers.fs.read_file(session.session_id, path=path)
        except BrowserProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
        return bytes(resp.content)

    # -- helpers ------------------------------------------------------------
    def _navigation_timeout_sec(self) -> float:
        return max(1.0, self.settings.browser_navigation_timeout_ms / 1000.0)

    def _assert_wall_ok(self, session: KernelSession) -> None:
        if _monotonic() >= session.deadline_at:
            raise BrowserProviderError(
                BrowserFailure.TIMEOUT,
                f"session {session.session_id} exceeded its wall-clock deadline",
                detail=session.session_id,
            )

    def _assert_allowed_domain(self, url: str | None) -> None:
        if not url:
            return
        host = (urlparse(url).hostname or "").lower()
        allowed = {d.lower().lstrip(".") for d in self.settings.browser_allowed_domains}
        if host and not any(host == d or host.endswith("." + d) for d in allowed):
            raise BrowserProviderError(
                BrowserFailure.SOURCE_BLOCKED,
                f"host {host!r} is not in TREI_BROWSER_ALLOWED_DOMAINS",
            )

    def _classify(self, exc: Exception) -> BrowserProviderError:
        try:
            from kernel import (  # noqa: PLC0415
                APIConnectionError,
                APITimeoutError,
                AuthenticationError,
                PermissionDeniedError,
                RateLimitError,
            )
        except ImportError:
            return BrowserProviderError(BrowserFailure.NETWORK_ERROR, str(exc))

        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return BrowserProviderError(BrowserFailure.PROVIDER_AUTH, f"Kernel auth/perm error: {exc}")
        if isinstance(exc, RateLimitError):
            return BrowserProviderError(BrowserFailure.PROVIDER_RATE_LIMITED, f"Kernel rate limit: {exc}")
        if isinstance(exc, APITimeoutError):
            return BrowserProviderError(BrowserFailure.TIMEOUT, f"Kernel timeout: {exc}")
        if isinstance(exc, APIConnectionError):
            return BrowserProviderError(BrowserFailure.NETWORK_ERROR, f"Kernel connection error: {exc}")
        return BrowserProviderError(BrowserFailure.NETWORK_ERROR, str(exc))

