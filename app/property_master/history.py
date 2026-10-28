"""Listing history: price trajectory and observed time on market (§21-22, §40).

Everything here is computed from immutable :class:`ListingSnapshot` records, so
the history is reconstructible and auditable. We never state that a listing was
*sold*: a disappearance is recorded as ``removed`` and the ambiguity is kept.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from app.core.enums import TimeOnMarketKind
from app.property_master.models import ListingRecord, ListingSnapshot


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _sorted_snapshots(snapshots: list[ListingSnapshot]) -> list[ListingSnapshot]:
    return sorted(snapshots, key=lambda s: (s.captured_at or "", s.source))


@dataclass
class PriceHistoryEntry:
    captured_at: str
    price: float
    change: float | None = None
    change_pct: float | None = None
    source: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["change"] = None if self.change is None else round(self.change, 2)
        data["change_pct"] = None if self.change_pct is None else round(self.change_pct, 4)
        return data


def price_history(snapshots: list[ListingSnapshot]) -> list[PriceHistoryEntry]:
    """Chronological price observations with per-step deltas."""
    out: list[PriceHistoryEntry] = []
    previous: float | None = None
    for snap in _sorted_snapshots(snapshots):
        if snap.price is None:
            continue
        change = None if previous is None else snap.price - previous
        change_pct = None
        if previous not in (None, 0):
            change_pct = (snap.price - previous) / previous
        out.append(
            PriceHistoryEntry(
                captured_at=snap.captured_at,
                price=snap.price,
                change=change,
                change_pct=change_pct,
                source=snap.source,
            )
        )
        previous = snap.price
    return out


def price_cut_count(snapshots: list[ListingSnapshot]) -> int:
    """Number of genuine downward price revisions."""
    entries = price_history(snapshots)
    return sum(1 for e in entries if e.change is not None and e.change < 0)


def cumulative_discount(snapshots: list[ListingSnapshot]) -> float | None:
    """(first - last) / first, restricted to *downward* movement.

    Negative (i.e. asking price increased) is reported as ``0.0`` because it is
    not a discount; the caller can inspect the full price history for increases.
    """
    entries = price_history(snapshots)
    if len(entries) < 2:
        return None
    first, last = entries[0].price, entries[-1].price
    if first <= 0:
        return None
    movement = (first - last) / first
    return max(0.0, movement)


def observed_time_on_market(
    record: ListingRecord, *, now: datetime | None = None
) -> tuple[int | None, str]:
    """(days, kind). Observed - never an estimate of time-to-sale."""
    start = _parse_ts(record.first_seen_at)
    end = _parse_ts(record.last_seen_at) or (now or datetime.now(UTC))
    if start is None:
        return None, TimeOnMarketKind.OBSERVED_TIME_ON_MARKET.value
    return max(0, (end - start).days), TimeOnMarketKind.OBSERVED_TIME_ON_MARKET.value


@dataclass
class ListingIntelligence:
    """The derived facts the listing page shows (§42)."""

    property_master_id: str | None
    days_on_market: int | None
    time_on_market_kind: str
    price_cut_count: int
    cumulative_discount: float | None
    first_price: float | None
    last_price: float | None
    last_price_change_at: str | None
    days_since_last_cut: int | None
    snapshots_count: int
    availability: str
    price_history: list[PriceHistoryEntry] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "property_master_id": self.property_master_id,
            "days_on_market": self.days_on_market,
            "time_on_market_kind": self.time_on_market_kind,
            "price_cut_count": self.price_cut_count,
            "cumulative_discount": None
            if self.cumulative_discount is None
            else round(self.cumulative_discount, 4),
            "first_price": self.first_price,
            "last_price": self.last_price,
            "last_price_change_at": self.last_price_change_at,
            "days_since_last_cut": self.days_since_last_cut,
            "snapshots_count": self.snapshots_count,
            "availability": self.availability,
            "price_history": [e.to_dict() for e in self.price_history],
            "sources": sorted(self.sources),
            "notes": self.notes,
        }


def listing_intelligence(
    record: ListingRecord,
    snapshots: list[ListingSnapshot],
    *,
    now: datetime | None = None,
) -> ListingIntelligence:
    """Assemble the listing's derived facts from its snapshot trail."""
    now = now or datetime.now(UTC)
    history = price_history(snapshots)
    days, kind = observed_time_on_market(record, now=now)

    last_change_at: str | None = None
    for entry in reversed(history):
        if entry.change is not None and entry.change < 0:
            last_change_at = entry.captured_at
            break

    days_since_cut: int | None = None
    if last_change_at:
        cut_at = _parse_ts(last_change_at)
        if cut_at is not None:
            days_since_cut = max(0, (now - cut_at).days)

    latest = _sorted_snapshots(snapshots)[-1] if snapshots else None
    notes: list[str] = [
        "Time on market is OBSERVED, not an estimate of time-to-sale.",
    ]
    if latest is not None and latest.availability == "removed":
        notes.append("Listing disappeared from the source; absence is not a confirmed sale.")

    return ListingIntelligence(
        property_master_id=record.property_master_id,
        days_on_market=days,
        time_on_market_kind=kind,
        price_cut_count=price_cut_count(snapshots),
        cumulative_discount=cumulative_discount(snapshots),
        first_price=history[0].price if history else None,
        last_price=history[-1].price if history else None,
        last_price_change_at=last_change_at,
        days_since_last_cut=days_since_cut,
        snapshots_count=len(snapshots),
        availability=latest.availability if latest else "unknown",
        price_history=history,
        sources=sorted({s.source for s in snapshots if s.source}),
        notes=notes,
    )
