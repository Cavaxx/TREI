"""Market depth metrics (sections 13 and 17).

All functions are pure and defensive: they never raise on missing/zero inputs,
they return ``None`` instead, because "unavailable" must be distinguishable
from "zero" (section 63).
"""

from __future__ import annotations

from dataclasses import dataclass


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def stock_turnover(annual_transactions: float | None, total_dwellings: float | None) -> float | None:
    """Share of the dwelling stock that changes hands in a year (section 13A)."""
    return _safe_div(annual_transactions, total_dwellings)


def visible_turnover(
    annual_transactions: float | None, avg_active_unique_listings: float | None
) -> float | None:
    """How many times the *observed* online stock is absorbed per year (13B)."""
    return _safe_div(annual_transactions, avg_active_unique_listings)


def active_supply_ratio(
    avg_active_unique_listings: float | None, total_dwellings: float | None
) -> float | None:
    """market-visible supply = observed listings / total dwellings (13C).

    This is deliberately named *market-visible supply*, NOT total supply.
    """
    return _safe_div(avg_active_unique_listings, total_dwellings)


def monthly_absorption(
    monthly_closed_sales: float | None, avg_active_unique_listings: float | None
) -> float | None:
    return _safe_div(monthly_closed_sales, avg_active_unique_listings)


def months_of_supply(
    avg_active_unique_listings: float | None, avg_monthly_closed_sales: float | None
) -> float | None:
    return _safe_div(avg_active_unique_listings, avg_monthly_closed_sales)


def annualized_inventory_turnover(
    annual_closed_sales: float | None, avg_active_unique_listings: float | None
) -> float | None:
    return _safe_div(annual_closed_sales, avg_active_unique_listings)


def average_active_listings(monthly_active_counts: list[int] | None) -> float | None:
    """Average of end-of-month active listing counts (time-weighted proxy)."""
    if not monthly_active_counts:
        return None
    return sum(monthly_active_counts) / len(monthly_active_counts)


def percentile(sorted_values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile on an already sorted list.

    ``q`` in [0, 1]. Duplicated here (instead of numpy) so depth metrics have no
    heavy dependency.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    if q <= 0:
        return float(sorted_values[0])
    if q >= 1:
        return float(sorted_values[-1])
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo]) * (1 - frac) + float(sorted_values[hi]) * frac


@dataclass(frozen=True)
class TimeOnMarketSummary:
    """Distribution of *observed* time on market (section 17)."""

    count: int
    median: float | None
    p25: float | None
    p75: float | None
    kind: str  # TimeOnMarketKind value

    @property
    def iqr(self) -> float | None:
        if self.p25 is None or self.p75 is None:
            return None
        return self.p75 - self.p25


def time_on_market_summary(
    days_observed: list[int] | None,
    kind: str = "observed_time_on_market",
) -> TimeOnMarketSummary:
    """Summarize days-on-market. Defaults to *observed* time on market.

    The caller must pass ``kind`` explicitly if it is estimating time-to-sale
    from a survey, so we never mislabel an observed removal as a confirmed sale.
    """
    vals = sorted(float(d) for d in (days_observed or []) if d is not None and d >= 0)
    return TimeOnMarketSummary(
        count=len(vals),
        median=percentile(vals, 0.5),
        p25=percentile(vals, 0.25),
        p75=percentile(vals, 0.75),
        kind=kind,
    )
