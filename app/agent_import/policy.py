"""Access policy resolution: Registry -> PolicyResolver -> Robots -> Decision (§26).

The crawler asks this module *once*, before it does anything, and obeys the
answer. There are four ways to be refused, and all of them are refusals rather
than obstacles to work around:

* the source is unknown to the registry;
* the registry marks it ``restricted`` / ``license_required`` and no licence covers it;
* the registry marks it ``unknown`` (never auto-enable an unverified source);
* robots.txt disallows the requested URL for our user agent.

Nothing in this module knows how to bypass anything, and nothing here retries a
refusal with a different identity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.core.config import Settings, get_settings
from app.core.enums import AccessMode
from app.fetchers.licence import LicenceRegistry
from app.fetchers.registry import SourceConfig, SourceRegistry
from app.fetchers.robots import RobotsCache

RESTRICTED_STATUSES = {"restricted", "license_required"}
#: Conservative per-source defaults; the registry may lower them via ``crawl:``.
DEFAULT_REQUESTS_PER_MINUTE = 12
DEFAULT_CONCURRENCY = 1
DEFAULT_DELAY_S = 5.0


@dataclass
class AccessDecision:
    """The verdict the crawler must respect."""

    source: str
    allowed: bool
    mode: str
    reason: str
    robots_allowed: bool | None = None
    requires_licence: bool = False
    licence_id: str = ""
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    concurrency: int = DEFAULT_CONCURRENCY
    delay_s: float = DEFAULT_DELAY_S
    retry_after_s: float | None = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _mode_for(config: SourceConfig, *, licensed: bool) -> tuple[AccessMode, bool]:
    """(mode, requires_licence) implied by the registry record."""
    tier = (config.tier or "").lower()
    status = (config.status or "").lower()
    if licensed:
        return (AccessMode.API if tier == "tier1_api" else AccessMode.FEED), True
    if status in RESTRICTED_STATUSES or tier == "tier5_restricted":
        # Only a signed contract may open these; otherwise they are off-limits.
        return AccessMode.RESTRICTED, True
    if status == "unknown":
        return AccessMode.UNKNOWN, False
    if config.api_public:
        return AccessMode.API, False
    if config.feed:
        return AccessMode.FEED, False
    if config.rss:
        return AccessMode.RSS, False
    if tier == "tier4_scraping":
        if config.scraping_allowed and config.robots_txt:
            return AccessMode.BROWSER_PUBLIC, False
        return AccessMode.DISABLED, False
    if tier == "tier3_open_data":
        return AccessMode.API, False
    return AccessMode.UNKNOWN, False


class PolicyResolver:
    """Turns registry + licence + robots into a single access decision."""

    def __init__(
        self,
        registry: SourceRegistry,
        *,
        licences: LicenceRegistry | None = None,
        robots: RobotsCache | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.registry = registry
        self.licences = licences
        self.settings = settings or get_settings()
        self._robots = robots

    def _robots_cache(self) -> RobotsCache:
        if self._robots is None:
            self._robots = RobotsCache(self.settings.user_agent)
        return self._robots

    def decide(self, source_name: str, *, urls: list[str] | None = None) -> AccessDecision:
        config = self.registry.get(source_name)
        if config is None:
            return AccessDecision(
                source=source_name,
                allowed=False,
                mode=AccessMode.UNKNOWN.value,
                reason="source is not in the registry: never crawl an unregistered source",
                evidence=["registry lookup failed"],
            )

        licence = self.licences.active_for(source_name) if self.licences else None
        licensed = licence is not None
        mode, requires_licence = _mode_for(config, licensed=licensed)
        evidence = [f"registry tier={config.tier}", f"registry status={config.status}"]

        rate = self._rate_config(config)
        decision = AccessDecision(
            source=source_name,
            allowed=False,
            mode=mode.value,
            reason="",
            requires_licence=requires_licence,
            licence_id=licence.licence_id if licence else "",
            requests_per_minute=rate["requests_per_minute"],
            concurrency=rate["concurrency"],
            delay_s=rate["delay_s"],
            evidence=evidence,
        )

        if licensed:
            evidence.append(f"licence {licence.licence_id} from {licence.grantor}")
        elif requires_licence:
            decision.reason = (
                "source is restricted/licence-required and no active licence covers it"
            )
            evidence.append("no active licence record")
            return decision
        elif mode is AccessMode.UNKNOWN:
            decision.reason = (
                "source is UNKNOWN: an unverified source is never crawled automatically"
            )
            return decision
        elif mode is AccessMode.DISABLED:
            decision.reason = (
                "source is tier4 but scraping is not permitted (robots/terms): disabled"
            )
            return decision
        elif not mode.automatable:
            decision.reason = f"access mode {mode.value} is not automatable"
            return decision

        if self.settings.respect_robots and urls:
            robots_ok, why = self._check_robots(urls)
            decision.robots_allowed = robots_ok
            evidence.append(why)
            if not robots_ok:
                decision.reason = "robots.txt disallows at least one requested URL"
                return decision

        decision.allowed = True
        decision.reason = "allowed by registry" + (" and licence" if licensed else " (public source)")
        return decision

    def _check_robots(self, urls: list[str]) -> tuple[bool, str]:
        cache = self._robots_cache()
        for url in urls:
            decision = cache.check(url)
            if not decision.allowed:
                return False, f"robots.txt disallows {url}: {decision.reason}"
        return True, f"robots.txt allows all {len(urls)} requested URL(s)"

    @staticmethod
    def _rate_config(config: SourceConfig) -> dict:
        crawl: dict = {}
        extra = getattr(config, "extra", None) or {}
        if isinstance(extra, dict):
            crawl = extra.get("crawl") or {}
        return {
            "requests_per_minute": int(crawl.get("requests_per_minute", DEFAULT_REQUESTS_PER_MINUTE)),
            "concurrency": int(crawl.get("concurrency", DEFAULT_CONCURRENCY)),
            "delay_s": float(crawl.get("delay_s", DEFAULT_DELAY_S)),
        }
