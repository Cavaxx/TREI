"""Mortgage affordability and buy-now vs wait sensitivity (section 57).

Deliberately does NOT recommend a scenario. It only reports the arithmetic and
the sensitivity of the result to house-price growth and to the mortgage rate,
so the user can see how fragile any conclusion is.
"""

from __future__ import annotations

from dataclasses import dataclass


def monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    """Standard annuity (French) monthly payment.

    ``annual_rate`` is a fraction (0.035 = 3.5%). A zero rate degrades to a
    straight division. Returns 0 for a non-positive principal.
    """
    if principal <= 0 or years <= 0:
        return 0.0
    n = years * 12
    r = annual_rate / 12.0
    if r == 0:
        return principal / n
    factor = (1 + r) ** n
    return principal * r * factor / (factor - 1)


def total_interest(principal: float, annual_rate: float, years: int) -> float:
    return monthly_payment(principal, annual_rate, years) * years * 12 - principal


def debt_service_ratio(monthly_payment_value: float, monthly_net_income: float) -> float | None:
    if monthly_net_income <= 0:
        return None
    return monthly_payment_value / monthly_net_income


@dataclass
class AffordabilityResult:
    purchase_price: float
    down_payment: float
    loan_amount: float
    mortgage_rate: float
    duration_years: int
    monthly_payment: float
    total_interest: float
    debt_service_ratio: float | None

    def to_dict(self) -> dict:
        return {
            "purchase_price": self.purchase_price,
            "down_payment": self.down_payment,
            "loan_amount": self.loan_amount,
            "mortgage_rate": self.mortgage_rate,
            "duration_years": self.duration_years,
            "monthly_payment": round(self.monthly_payment, 2),
            "total_interest": round(self.total_interest, 2),
            "debt_service_ratio": None
            if self.debt_service_ratio is None
            else round(self.debt_service_ratio, 4),
        }


def affordability(
    purchase_price: float,
    down_payment: float,
    mortgage_rate: float,
    duration_years: int = 25,
    monthly_net_income: float | None = None,
) -> AffordabilityResult:
    loan = max(purchase_price - down_payment, 0.0)
    mp = monthly_payment(loan, mortgage_rate, duration_years)
    dsr = debt_service_ratio(mp, monthly_net_income) if monthly_net_income else None
    return AffordabilityResult(
        purchase_price=purchase_price,
        down_payment=down_payment,
        loan_amount=loan,
        mortgage_rate=mortgage_rate,
        duration_years=duration_years,
        monthly_payment=mp,
        total_interest=total_interest(loan, mortgage_rate, duration_years),
        debt_service_ratio=dsr,
    )


def compare_purchase_horizons(
    purchase_price: float,
    down_payment: float,
    mortgage_rate: float,
    duration_years: int,
    annual_price_growth: float,
    months: list[int] | None = None,
) -> list[dict]:
    """Cost of buying now vs waiting N months at a constant growth rate.

    Reports the changed purchase price and monthly payment at each horizon. It
    intentionally does not rank the options.
    """
    months = months or [0, 6, 12, 24]
    out: list[dict] = []
    for m in months:
        future_price = purchase_price * (1 + annual_price_growth) ** (m / 12.0)
        future_down = down_payment
        loan = max(future_price - future_down, 0.0)
        out.append(
            {
                "wait_months": m,
                "estimated_purchase_price": round(future_price, 0),
                "loan_amount": round(loan, 0),
                "monthly_payment": round(monthly_payment(loan, mortgage_rate, duration_years), 2),
            }
        )
    return out


def rate_price_sensitivity(
    purchase_price: float,
    down_payment: float,
    duration_years: int,
    rates: list[float] | None = None,
) -> list[dict]:
    """Monthly payment across a grid of mortgage rates (house price fixed)."""
    rates = rates or [0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05]
    loan = max(purchase_price - down_payment, 0.0)
    return [
        {"mortgage_rate": r, "monthly_payment": round(monthly_payment(loan, r, duration_years), 2)}
        for r in rates
    ]
