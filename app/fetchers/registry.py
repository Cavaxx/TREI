"""Source registry loader (Agent 1 output -> runtime config).

Reads ``config/source_registry.yaml`` and exposes typed access. The registry is
the single source of truth for which sources exist, their tier, whether we are
allowed to acquire them automatically, and the recommended acquisition mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.core.config import get_settings


@dataclass
class SourceConfig:
    name: str
    domain: str
    kind: str
    tier: str
    status: str
    recommended_mode: str
    coverage: str = ""
    update_frequency: str = ""
    api_public: bool = False
    api_commercial: bool = False
    feed: bool = False
    rss: bool = False
    robots_txt: bool = False
    scraping_allowed: bool = False
    requires_auth: bool = False
    cost_or_license: str = ""
    rate_limit: str = ""
    quality_estimate: str = ""
    duplication_risk: str = ""
    last_verified: str = ""
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def auto_acquisition_allowed(self) -> bool:
        """True only when automated acquisition is legal and polite.

        Tier-1/2/3 sources, or Tier-4 sources whose robots/terms permit it.
        Anything 'restricted'/'license_required'/'unknown', or any Tier-5
        source, is excluded.
        """
        status = self.status.lower()
        if status in {"restricted", "license_required", "unknown"}:
            return False
        if self.tier == "tier5_restricted":
            return False
        if self.requires_auth and self.tier != "tier1_api":
            return False
        if self.tier == "tier4_scraping":
            return self.scraping_allowed and self.robots_txt
        return True


class SourceRegistry:
    def __init__(self, sources: list[SourceConfig]) -> None:
        self._sources = {s.name: s for s in sources}

    @classmethod
    def load(cls, path: Path | None = None) -> SourceRegistry:
        path = path or get_settings().registry_path
        data = yaml.safe_load(Path(path).read_text())
        raw = data.get("sources", []) if isinstance(data, dict) else data
        sources = [cls._from_dict(d) for d in raw]
        return cls(sources)

    @staticmethod
    def _from_dict(d: dict) -> SourceConfig:
        known = {f for f in SourceConfig.__dataclass_fields__}
        kwargs = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        obj = SourceConfig(**kwargs)
        obj.extra = extra
        return obj

    def all(self) -> list[SourceConfig]:
        return list(self._sources.values())

    def get(self, name: str) -> SourceConfig | None:
        return self._sources.get(name)

    def by_tier(self, tier: str) -> list[SourceConfig]:
        return [s for s in self._sources.values() if s.tier == tier]

    def auto_enabled(self) -> list[SourceConfig]:
        return [s for s in self._sources.values() if s.auto_acquisition_allowed]

    def restricted(self) -> list[SourceConfig]:
        return [s for s in self._sources.values() if not s.auto_acquisition_allowed]
