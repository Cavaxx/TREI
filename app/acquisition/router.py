"""Acquisition method routing.

Order of preference (§router): official API/feed/export -> HTTP of public pages ->
browser (only when JS/navigation is required AND an explicit pilot run is enabled)
-> manual import.

An HTTP failure never silently escalates to the browser: the browser is chosen
only when the *method* requires it, never because access was denied.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.fetchers.registry import SourceConfig


@dataclass
class RouteDecision:
    source: str
    mode: str  # api | feed | http | browser | manual | blocked
    reason: str
    urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"source": self.source, "mode": self.mode, "reason": self.reason, "urls": self.urls}


def _extra(config: SourceConfig) -> dict:
    extra = getattr(config, "extra", None) or {}
    return extra if isinstance(extra, dict) else {}


def _browser_available(settings: Settings) -> bool:
    # Browser jobs run only as an explicit, bounded pilot.
    return settings.browser_provider == "kernel" and settings.acquisition_mode == "pilot"


def route_acquisition(config: SourceConfig, settings: Settings | None = None) -> RouteDecision:
    settings = settings or get_settings()
    acq = _extra(config).get("acquisition")
    acq = acq if isinstance(acq, dict) else {}

    # 1. Licensed / official API or feed.
    if config.tier in ("tier1_api", "tier2_feed"):
        return RouteDecision(config.name, "feed", "licensed/official API or feed", list(acq.get("urls") or []))

    # 2. Public API.
    if config.api_public:
        return RouteDecision(config.name, "api", "public API", list(acq.get("urls") or []))

    # 3. Direct official export/files via HTTP.
    direct = acq.get("direct_files")
    if direct:
        return RouteDecision(config.name, "http", "direct export/file via HTTP", list(direct))

    # 4. Browser for JS-driven pages, only when the method (not a refusal) needs it.
    if acq.get("requires_browser"):
        if not _browser_available(settings):
            return RouteDecision(
                config.name,
                "blocked",
                "browser required but acquisition is not enabled (TREI_ACQUISITION_MODE != pilot)",
                list(acq.get("urls") or []),
            )
        return RouteDecision(
            config.name,
            "browser",
            acq.get("browser_reason") or "JS-driven navigation",
            list(acq.get("urls") or []),
        )

    # 5. Restricted / unverified sources are never automated.
    if config.status.lower() in ("restricted", "license_required", "unknown") or config.tier == "tier5_restricted":
        return RouteDecision(config.name, "blocked", "source is restricted/licence-required/unknown")

    # 6. Ordinary permitted public pages.
    if config.tier == "tier4_scraping" and config.scraping_allowed and config.robots_txt:
        return RouteDecision(config.name, "http", "permitted public pages", list(acq.get("urls") or []))

    return RouteDecision(config.name, "manual", "no automatable path; manual import only")
