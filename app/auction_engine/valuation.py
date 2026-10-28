"""Effective economic value of an auction lot (section 30).

Auctions must NOT be valued with the ordinary asking-price model. What matters
is the *total* acquisition cost and the risk-adjusted discount versus market
value, not the naive ``minimum_offer / appraisal_value``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuctionCostInputs:
    auction_purchase_price: float
    estimated_market_value: float
    taxes: float = 0.0
    renovation: float = 0.0
    regularization: float = 0.0
    arrears: float = 0.0
    transaction_costs: float = 0.0
    possession_cost: float = 0.0
    risk_adjustment: float = 0.0
    other_costs: float = 0.0


@dataclass
class AuctionValuation:
    estimated_total_acquisition_cost: float
    risk_adjusted_discount: float | None
    naive_discount: float | None
    cost_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "estimated_total_acquisition_cost": round(self.estimated_total_acquisition_cost, 0),
            "risk_adjusted_discount": None
            if self.risk_adjusted_discount is None
            else round(self.risk_adjusted_discount, 4),
            "naive_discount": None if self.naive_discount is None else round(self.naive_discount, 4),
            "cost_breakdown": {k: round(v, 0) for k, v in self.cost_breakdown.items()},
        }


def value_auction(
    inputs: AuctionCostInputs, minimum_offer: float | None = None, appraisal_value: float | None = None
) -> AuctionValuation:
    breakdown = {
        "auction_purchase_price": inputs.auction_purchase_price,
        "taxes": inputs.taxes,
        "renovation": inputs.renovation,
        "regularization": inputs.regularization,
        "arrears": inputs.arrears,
        "transaction_costs": inputs.transaction_costs,
        "possession_cost": inputs.possession_cost,
        "risk_adjustment": inputs.risk_adjustment,
        "other_costs": inputs.other_costs,
    }
    total = sum(breakdown.values())
    mv = inputs.estimated_market_value
    rad = (1 - total / mv) if mv else None
    naive = (
        (1 - minimum_offer / appraisal_value)
        if (minimum_offer and appraisal_value)
        else None
    )
    return AuctionValuation(
        estimated_total_acquisition_cost=total,
        risk_adjusted_discount=rad,
        naive_discount=naive,
        cost_breakdown=breakdown,
    )
