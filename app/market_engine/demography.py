"""Household-formation analytics (section 15).

We keep population change and household change separate, because household
formation can outpace population growth and still drive housing demand.
"""

from __future__ import annotations

from dataclasses import dataclass


def deltas(series: list[float | None]) -> list[float | None]:
    """First difference with the same length as the input; index 0 is None."""
    out: list[float | None] = [None]
    for i in range(1, len(series)):
        prev, cur = series[i - 1], series[i]
        out.append(None if prev is None or cur is None else cur - prev)
    return out


def deltas2(series: list[float | None]) -> list[float | None]:
    """Second difference (acceleration of change), same length as input."""
    first = deltas(series)
    return deltas(first)


def growth_rate(prev: float | None, cur: float | None) -> float | None:
    if prev is None or cur is None or prev == 0:
        return None
    return (cur - prev) / prev


@dataclass(frozen=True)
class HouseholdAnalytics:
    """Household block for a single period t (relative to t-1, t-2)."""

    households: float | None
    household_change: float | None          # ΔH_t
    household_growth_rate: float | None     # ΔH_t / H_{t-1}
    household_acceleration: float | None    # Δ²H_t
    two_year_net_household_change: float | None   # H_t - H_{t-2}
    two_year_average_annual_change: float | None  # (H_t - H_{t-2}) / 2
    population: float | None = None
    population_change: float | None = None
    household_growth_minus_population_growth: float | None = None


def household_analytics(
    households_t: float | None,
    households_t1: float | None,
    households_t2: float | None = None,
    population_t: float | None = None,
    population_t1: float | None = None,
) -> HouseholdAnalytics:
    dh = None
    dh2 = None
    gr = None
    if households_t is not None and households_t1 is not None:
        dh = households_t - households_t1
        gr = growth_rate(households_t1, households_t)
        if households_t2 is not None:
            dh_prev = households_t1 - households_t2
            dh2 = dh - dh_prev

    net2 = None
    avg2 = None
    if households_t is not None and households_t2 is not None:
        net2 = households_t - households_t2
        avg2 = net2 / 2

    pop_change = None
    if population_t is not None and population_t1 is not None:
        pop_change = population_t - population_t1

    diff = None
    if dh is not None and pop_change is not None:
        diff = dh - pop_change

    return HouseholdAnalytics(
        households=households_t,
        household_change=dh,
        household_growth_rate=gr,
        household_acceleration=dh2,
        two_year_net_household_change=net2,
        two_year_average_annual_change=avg2,
        population=population_t,
        population_change=pop_change,
        household_growth_minus_population_growth=diff,
    )
