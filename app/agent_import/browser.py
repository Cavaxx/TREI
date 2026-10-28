"""Browser rendering and screenshot/vision fallbacks (§12-13).

These are *extraction* tools, not access tools. Two rules are encoded here:

* A browser is only used when rendering is genuinely required (the deterministic
  passes came back thin) **and** the operator enabled it **and** the source's
  access mode permits client-side rendering. It never hides that it is an
  automation: our own descriptive User-Agent is always sent.
* A screenshot is a way to read data that is only painted on screen. It is never
  a way to get past an access control. Its hash and capture time are stored, so
  a value read from pixels can always be audited.

Both back ends are optional dependencies. When they are missing the fallback is
simply unavailable, and the reason is reported rather than worked around.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import Settings, get_settings
from app.core.fields import ExtractionBundle


class FallbackUnavailable(Exception):
    """The fallback could not run (missing optional dependency or disabled)."""


@dataclass
class RenderedPage:
    """A page rendered by a real browser."""

    url: str
    html: str
    method: str = "browser_dom"
    screenshot: bytes | None = None
    screenshot_hash: str = ""
    captured_at: str = field(
        default_factory=lambda: datetime.now(UTC).replace(microsecond=0).isoformat()
    )


class BrowserRenderer(Protocol):
    def render(self, url: str) -> RenderedPage: ...


class PlaywrightRenderer:
    """Chromium through Playwright, with a truthful identity and a timeout.

    Deliberately absent: stealth plugins, fingerprint spoofing, proxy rotation,
    CAPTCHA handling. If a source blocks automation, that is an answer, and the
    answer is respected.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        headless: bool = True,
        capture_screenshot: bool = False,
        wait_until: str = "domcontentloaded",
        wait_timeout_ms: int = 15_000,
    ) -> None:
        self.settings = settings or get_settings()
        self.headless = headless
        self.capture_screenshot = capture_screenshot
        self.wait_until = wait_until
        self.wait_timeout_ms = wait_timeout_ms
        self._browser = None
        self._playwright = None

    def available(self) -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return True

    def render(self, url: str) -> RenderedPage:  # pragma: no cover - requires a browser
        if not self.settings.allow_browser:
            raise FallbackUnavailable("browser rendering is disabled (TREI_ALLOW_BROWSER=false)")
        if not self.available():
            raise FallbackUnavailable("playwright is not installed")

        from playwright.sync_api import sync_playwright

        if self._playwright is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
        context = self._browser.new_context(user_agent=self.settings.user_agent)
        page = context.new_page()
        try:
            page.goto(url, wait_until=self.wait_until, timeout=self.wait_timeout_ms)
            html = page.content()
            shot = page.screenshot(full_page=True) if self.capture_screenshot else None
        finally:
            context.close()
        return RenderedPage(
            url=url,
            html=html,
            screenshot=shot,
            screenshot_hash=hashlib.sha256(shot).hexdigest() if shot else "",
        )

    def close(self) -> None:  # pragma: no cover - requires a browser
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()


class VisionExtractor(Protocol):
    """Turns a screenshot into fields. Implemented by the operator's own model."""

    def extract(self, screenshot: bytes, *, url: str = "") -> ExtractionBundle: ...


class NoVisionExtractor:
    """Default: vision is not configured, so nothing is fabricated from pixels."""

    def extract(self, screenshot: bytes, *, url: str = "") -> ExtractionBundle:
        raise FallbackUnavailable(
            "no vision extractor configured; a screenshot cannot replace a structured parser"
        )


@dataclass
class FallbackOutcome:
    """What the fallbacks produced, and why - always reported, never hidden."""

    used_browser: bool = False
    used_vision: bool = False
    rendered: RenderedPage | None = None
    bundle: ExtractionBundle = field(default_factory=ExtractionBundle)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "used_browser": self.used_browser,
            "used_vision": self.used_vision,
            "screenshot_hash": self.rendered.screenshot_hash if self.rendered else "",
            "reasons": self.reasons,
            "fields": self.bundle.to_dict(),
        }


def render_if_needed(
    *,
    url: str,
    html: str,
    deterministic_method: str,
    renderer: BrowserRenderer | None = None,
    vision: VisionExtractor | None = None,
    settings: Settings | None = None,
) -> FallbackOutcome:
    """Apply the fallbacks in order, only when the page actually needs them.

    ``deterministic_method`` is the method the structured ladder used; when that
    produced nothing at all, rendering is worth a try.
    """
    settings = settings or get_settings()
    outcome = FallbackOutcome()

    if deterministic_method:
        outcome.reasons.append(
            f"deterministic extraction succeeded via {deterministic_method}; browser not needed"
        )
        return outcome
    if not settings.allow_browser:
        outcome.reasons.append("no deterministic data and browser rendering is disabled")
        return outcome
    if renderer is None:
        outcome.reasons.append("browser rendering is allowed but no renderer is configured")
        return outcome

    try:
        rendered = renderer.render(url)
    except FallbackUnavailable as exc:
        outcome.reasons.append(f"browser fallback unavailable: {exc}")
        return outcome
    except Exception as exc:  # noqa: BLE001 - a failed render must not break the crawl
        outcome.reasons.append(f"browser render failed: {exc}")
        return outcome

    outcome.used_browser = True
    outcome.rendered = rendered
    outcome.reasons.append("browser rendering used because deterministic passes were empty")

    if settings.allow_vision and rendered.screenshot and vision is not None:
        try:
            outcome.bundle = vision.extract(rendered.screenshot, url=url)
            outcome.used_vision = True
            outcome.reasons.append("vision extraction used on the captured screenshot")
        except FallbackUnavailable as exc:
            outcome.reasons.append(f"vision fallback unavailable: {exc}")
    return outcome
