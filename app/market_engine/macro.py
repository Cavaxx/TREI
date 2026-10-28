"""Macro panel and derived market metrics (§36-37).

The one rule that shapes this module: **an unpublished figure is not zero, and it
is not interpolated.** A period whose official value has not been released is
stored with ``data_status = NOT_YET_PUBLISHED`` and a ``None`` value, so every
downstream calculation can see the difference between "the market was flat" and
"we do not know yet".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from app.core.enums import DataStatus

#: Indicators the platform tracks, with the cadence of their source (§36).
INDICATORS: dict[str, str] = {
    "price_index": "quarterly",
    "transactions": "quarterly",
    "dwelling_stock": "annual",
    "turnover": "quarterly",
    "households": "annual",
    "mortgage_rate": "monthly",
    "mortgage_volume": "quarterly",
    "inflation": "monthly",
    "time_on_market": "quarterly",
    "discount": "quarterly",
    "income": "annual",
    "employment": "quarterly",
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class MacroObservation:
    """One indicator value for one period, with its publication status."""

    indicator: str
    period: str
    value: float | None = None
    status: DataStatus = DataStatus.OBSERVED
    unit: str = ""
    source: str = ""
    retrieved_at: str = field(default_factory=_now_iso)

    @property
    def published(self) -> bool:
        """True only when a real number is present."""
        return self.status in (DataStatus.OBSERVED, DataStatus.ESTIMATED) and self.value is not None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["published"] = self.published
        return data


@dataclass
class MacroPanel:
    """A set of observations, addressable by (indicator, period)."""

    observations: list[MacroObservation] = field(default_factory=list)

    def add(self, observation: MacroObservation) -> None:
        self.observations.append(observation)

    def series(self, indicator: str) -> list[MacroObservation]:
        """Chronological observations for one indicator (unpublished included)."""
        rows = [o for o in self.observations if o.indicator == indicator]
        return sorted(rows, key=lambda o: o.period)

    def published_series(self, indicator: str) -> list[MacroObservation]:
        return [o for o in self.series(indicator) if o.published]

    def at(self, indicator: str, period: str) -> MacroObservation | None:
        for observation in self.observations:
            if observation.indicator == indicator and observation.period == period:
                return observation
        return None

    def latest(self, indicator: str) -> MacroObservation | None:
        rows = self.series(indicator)
        return rows[-1] if rows else None

    def values(self, indicator: str) -> list[float | None]:
        """Values in order, with ``None`` where the figure is not published yet."""
        return [None if not o.published else o.value for o in self.series(indicator)]

    def to_dict(self) -> dict:
        return {
            "observations": [o.to_dict() for o in self.observations],
            "indicators": sorted({o.indicator for o in self.observations}),
        }


def build_panel(rows: list[dict]) -> MacroPanel:
    """Build a panel from rows, honouring an explicit ``status`` when given."""
    panel = MacroPanel()
    for row in rows:
        status = row.get("status")
        value = row.get("value")
        if isinstance(status, str):
            status = DataStatus(status)
        elif value is None:
            # A missing value with no declared status is treated as unavailable:
            # we refuse to call an absent figure "observed".
            status = DataStatus.UNAVAILABLE
        else:
            status = DataStatus.OBSERVED
        panel.add(
            MacroObservation(
                indicator=str(row.get("indicator", "")),
                period=str(row.get("period", "")),
                value=None if value is None else float(value),
                status=status,
                unit=str(row.get("unit", "")),
                source=str(row.get("source", "")),
                retrieved_at=str(row.get("retrieved_at") or _now_iso()),
            )
        )
    return panel


def not_yet_published(indicator: str, period: str, *, source: str = "") -> MacroObservation:
    """Explicit placeholder for a period whose figure has not been released."""
    return MacroObservation(
        indicator=indicator,
        period=period,
        value=None,
        status=DataStatus.NOT_YET_PUBLISHED,
        source=source,
    )


# --- derived demographic & credit metrics -----------------------------------

def _deltas(values: list[float | None]) -> list[float | None]:
    """Period-over-period changes; ``None`` propagates (never bridged)."""
    out: list[float | None] = []
    for previous, current in zip(values, values[1:]):
        if previous is None or current is None:
            out.append(None)
        else:
            out.append(current - previous)
    return out


def household_growth(levels: list[float | None]) -> list[float | None]:
    """Absolute change in households (Δ households)."""
    return _deltas(levels)


def household_acceleration(levels: list[float | None]) -> list[float | None]:
    """Change in the change (Δ² households) - the demographic inflection."""
    return _deltas(household_growth(levels))


def growth_rate(values: list[float | None]) -> list[float | None]:
    """Relative period-over-period growth; ``None`` where either side is missing."""
    out: list[float | None] = []
    for previous, current in zip(values, values[1:]):
        if previous in (None, 0) or current is None:
            out.append(None)
        else:
            out.append((current - previous) / previous)
    return out


def real_price(nominal_index: float | None, inflation_index: float | None) -> float | None:
    """Deflate a nominal price index by an inflation index (both base-consistent)."""
    if nominal_index is None or inflation_index in (None, 0):
        return None
    return nominal_index / inflation_index


def absorption_rate(
    monthly_closed_sales: float | None, active_listings: float | None
) -> float | None:
    """Share of the visible supply absorbed in a month (§37)."""
    if monthly_closed_sales is None or active_listings in (None, 0):
        return None
    return monthly_closed_sales / active_listings


def market_context(panel: MacroPanel, *, inflation_period: str | None = None) -> dict:
    """Assemble the macro inputs the Market Engine consumes (§37).

    Every entry states whether it is observed or still pending publication, so a
    regime classification built on partial data can say so.
    """
    context: dict = {}
    for indicator in INDICATORS:
        rows = panel.series(indicator)
        if not rows:
            context[indicator] = {"status": DataStatus.UNAVAILABLE.value, "value": None}
            continue
        latest = rows[-1]
        context[indicator] = {
            "status": latest.status.value,
            "value": latest.value,
            "period": latest.period,
            "published": latest.published,
        }

    households = panel.values("households")
    infl = (
        panel.values("inflation")
        if inflation_period is None
        else [panel.at("inflation", inflation_period) and panel.at("inflation", inflation_period).value]
    )
    context["derived"] = {
        "household_growth": household_growth(households),
        "household_acceleration": household_acceleration(households),
        "price_growth": growth_rate(panel.values("price_index")),
        "real_price": real_price(
            panel.latest("price_index").value if panel.latest("price_index") else None,
            infl[-1] if infl else None,
        ),
    }
    context["notes"] = [
        "NOT_YET_PUBLISHED periods are never interpolated or treated as zero.",
        "Derived series propagate None rather than bridging a gap.",
    ]
    return context
