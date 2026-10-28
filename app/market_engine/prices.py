"""Price analytics: nominal vs real, YoY, and period-value semantics.

Section 11 is explicit: never compare nominal 2015 with nominal 2026 without
also showing the real value. Section 9 is explicit: never silently turn a
partial year into a full-year annual figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ValueStatus(str, Enum):
    OBSERVED = "observed"
    ESTIMATED = "estimated"
    ANNUALIZED = "annualized"
    INTERPOLATED = "interpolated"


@dataclass(frozen=True)
class PeriodValue:
    """A single value tied to a period and an explicit status.

    ``period`` follows the convention ``YYYY``, ``YYYY_Qn``, ``YYYY_YTD``,
    ``YYYY_full_year_estimate`` (section 9).
    """

    period: str
    value: float | None
    status: ValueStatus
    unit: str = ""
    notes: str = ""


def real_price(
    nominal_price: float | None,
    cpi_t: float | None,
    cpi_base: float | None,
    base_label: str = "2025",
) -> float | None:
    """Deflate a nominal price to ``base_label`` euros.

        real_price_2025_eur = nominal_t * CPI_2025 / CPI_t
    """
    if nominal_price is None or cpi_t is None or cpi_base is None or cpi_t == 0:
        return None
    return nominal_price * (cpi_base / cpi_t)


def deflate_series(
    periods: list[str],
    nominal: list[float | None],
    cpi_by_period: dict[str, float | None],
    cpi_base: float | None,
    base_label: str = "2025",
) -> list[PeriodValue]:
    """Return a list of real (deflated) PeriodValues."""
    out: list[PeriodValue] = []
    for period, nom in zip(periods, nominal):
        rp = real_price(nom, cpi_by_period.get(period), cpi_base, base_label)
        out.append(
            PeriodValue(
                period=period,
                value=rp,
                status=ValueStatus.OBSERVED if rp is not None else ValueStatus.ESTIMATED,
                unit=f"real_eur_{base_label}_per_m2",
            )
        )
    return out


def yoy_change(current: float | None, year_ago: float | None) -> float | None:
    """Year-over-year relative change, None-safe."""
    if current is None or year_ago is None or year_ago == 0:
        return None
    return current / year_ago - 1


def momentum(series: list[float | None], window: int = 3) -> float | None:
    """Trailing-window average of period-over-period changes.

    Returns None when fewer than ``window`` comparable consecutive pairs exist.
    """
    diffs = [
        series[i] - series[i - 1]
        for i in range(1, len(series))
        if series[i] is not None and series[i - 1] is not None
    ]
    if len(diffs) < window:
        return None
    tail = diffs[-window:]
    return sum(tail) / len(tail)


def aggregate_price_per_m2(prices_m2: list[float], weights: list[float] | None = None) -> dict:
    """Compute median/mean €/m² from a set of listings.

    Median is reported as the headline because asking-price distributions are
    fat-tailed. Both are returned so the caller can explain the choice.
    """
    vals = [p for p in prices_m2 if p is not None and p > 0]
    if not vals:
        return {"count": 0, "median": None, "mean": None}
    vals_sorted = sorted(vals)
    n = len(vals_sorted)
    if n % 2 == 1:
        median = vals_sorted[n // 2]
    else:
        median = (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
    mean = sum(vals_sorted) / n
    return {"count": n, "median": median, "mean": mean}
