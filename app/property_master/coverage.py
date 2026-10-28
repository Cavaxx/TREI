"""Source coverage reporting (§43).

The dashboard must never claim completeness. It reports what was *observed*: how
many unique properties, through how many sources, with what overlap — and an
explicit coverage-confidence figure that says how much of that observation is
corroborated by more than one channel.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field

from app.property_master.models import ListingRecord


@dataclass
class SourceCoverage:
    unique_property_master: int
    raw_listings: int
    active_listings: int
    sources: list[str] = field(default_factory=list)
    coverage_by_source: list[dict] = field(default_factory=list)
    #: Share of raw listings that resolve onto an already-seen physical unit.
    duplicate_rate: float | None = None
    #: Share of physical units seen through two or more independent sources.
    multi_source_share: float | None = None
    #: Share of listings that received a property_master_id at all.
    resolution_rate: float | None = None
    coverage_confidence: float = 0.0
    statement: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("duplicate_rate", "multi_source_share", "resolution_rate", "coverage_confidence"):
            value = data.get(key)
            data[key] = None if value is None else round(value, 3)
        return data


def coverage_report(listings: list[ListingRecord]) -> SourceCoverage:
    """Build the coverage view from the resolved listing records."""
    total = len(listings)
    by_master: dict[str, list[ListingRecord]] = defaultdict(list)
    for record in listings:
        if record.property_master_id:
            by_master[record.property_master_id].append(record)

    per_source: dict[str, dict] = defaultdict(lambda: {"listings": 0, "masters": set(), "active": 0})
    for record in listings:
        bucket = per_source[record.source]
        bucket["listings"] += 1
        bucket["active"] += 1 if record.is_active else 0
        if record.property_master_id:
            bucket["masters"].add(record.property_master_id)

    unique_masters = len(by_master)
    resolved = sum(len(v) for v in by_master.values())
    multi_source = sum(1 for recs in by_master.values() if len({r.source for r in recs}) > 1)

    source_rows = [
        {
            "source": source,
            "listings": bucket["listings"],
            "active_listings": bucket["active"],
            "unique_property_master": len(bucket["masters"]),
            "share_of_listings": round(bucket["listings"] / total, 4) if total else None,
        }
        for source, bucket in sorted(per_source.items())
    ]

    duplicate_rate = (1 - unique_masters / total) if (total and unique_masters) else None
    multi_source_share = (multi_source / unique_masters) if unique_masters else None
    resolution_rate = (resolved / total) if total else None
    confidence = _coverage_confidence(
        n_sources=len(per_source),
        multi_source_share=multi_source_share,
        resolution_rate=resolution_rate,
    )

    return SourceCoverage(
        unique_property_master=unique_masters,
        raw_listings=total,
        active_listings=sum(1 for r in listings if r.is_active),
        sources=sorted(per_source),
        coverage_by_source=source_rows,
        duplicate_rate=duplicate_rate,
        multi_source_share=multi_source_share,
        resolution_rate=resolution_rate,
        coverage_confidence=confidence,
        statement=(
            f"TREI observes {unique_masters} unique properties across "
            f"{len(per_source)} source(s); it does not claim to contain the whole market."
        ),
        notes=[
            "Coverage describes observations, not the total dwelling stock.",
            "A single-source property is an observation awaiting corroboration.",
        ],
    )


def _coverage_confidence(
    *, n_sources: int, multi_source_share: float | None, resolution_rate: float | None
) -> float:
    """0..1 heuristic: breadth of sources x corroboration x resolution quality."""
    breadth = min(1.0, n_sources / 4)
    corroboration = multi_source_share or 0.0
    resolution = resolution_rate if resolution_rate is not None else 0.0
    return max(0.0, min(1.0, 0.45 * breadth + 0.35 * corroboration + 0.20 * resolution))
