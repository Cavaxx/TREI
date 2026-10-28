"""Scenario engine and affordability (sections 56-57).

The scenario engine propagates user-defined shocks (rates, households, supply,
transactions, inflation, income) through documented *elasticities* to produce
estimated impacts on price, transactions and absorption, plus a regime
probability vector.

IMPORTANT: the elasticities shipped here are conservative priors, not estimates
from Trentino data. They are exposed explicitly (and versioned) so that, once
the macro panel exists, they can be replaced by estimated coefficients. Outputs
carry an uncertainty band and are never presented as certainties.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.enums import MarketRegime

# Elasticity of each target to each driver (percent change per unit shock).
# Units: rate/inflation/income shocks are in percentage points; supply/
# transaction/household shocks are in percent.
DEFAULT_ELASTICITIES: dict[str, dict[str, float]] = {
    # d(log price) per unit
    "price": {
        "mortgage_rate_pp": -0.030,   # +1pp rate -> -3.0% price (prior)
        "household_growth_pct": +0.30,
        "active_supply_pct": -0.25,
        "inflation_pp": +0.20,        # nominal prices rise with inflation
        "income_growth_pct": +0.25,
    },
    "transactions": {
        "mortgage_rate_pp": -0.045,   # volumes adjust before prices (section 51E)
        "household_growth_pct": +0.40,
        "active_supply_pct": +0.10,
        "inflation_pp": -0.10,
        "income_growth_pct": +0.20,
    },
    "absorption": {
        "mortgage_rate_pp": -0.040,
        "household_growth_pct": +0.45,
        "active_supply_pct": -0.60,   # more supply -> lower absorption rate
        "inflation_pp": -0.05,
        "income_growth_pct": +0.15,
    },
}


@dataclass
class ScenarioInput:
    mortgage_rate_pp: float = 0.0
    household_growth_pct: float = 0.0
    transactions_pct: float = 0.0
    active_supply_pct: float = 0.0
    inflation_pp: float = 0.0
    income_growth_pct: float = 0.0

    def shocks(self) -> dict[str, float]:
        return {
            "mortgage_rate_pp": self.mortgage_rate_pp,
            "household_growth_pct": self.household_growth_pct,
            "active_supply_pct": self.active_supply_pct,
            "inflation_pp": self.inflation_pp,
            "income_growth_pct": self.income_growth_pct,
        }


@dataclass
class ScenarioOutput:
    price_impact_pct: float
    transaction_impact_pct: float
    absorption_impact_pct: float
    regime_probabilities: dict[str, float] = field(default_factory=dict)
    uncertainty_pct: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "price_impact_pct": round(self.price_impact_pct, 4),
            "transaction_impact_pct": round(self.transaction_impact_pct, 4),
            "absorption_impact_pct": round(self.absorption_impact_pct, 4),
            "price_impact_band_pct": [
                round(self.price_impact_pct - self.uncertainty_pct, 4),
                round(self.price_impact_pct + self.uncertainty_pct, 4),
            ],
            "regime_probabilities": {k: round(v, 3) for k, v in self.regime_probabilities.items()},
            "notes": self.notes,
        }


def _impact(target: str, shocks: dict[str, float], elasticities: dict) -> float:
    return sum(elasticities[target].get(k, 0.0) * v for k, v in shocks.items())


def run_scenario(
    scenario: ScenarioInput,
    elasticities: dict[str, dict[str, float]] | None = None,
    uncertainty_pct: float = 0.03,
) -> ScenarioOutput:
    """Propagate shocks into estimated impacts (as fractions, e.g. 0.03 = +3%)."""
    elas = elasticities or DEFAULT_ELASTICITIES
    shocks = scenario.shocks()
    price = _impact("price", shocks, elas)
    tx = _impact("transactions", shocks, elas)
    absorb = _impact("absorption", shocks, elas)

    # A very simple, transparent mapping from the price impact to a regime
    # probability vector via a logistic softmax-like squashing. It is a
    # heuristic, labelled as such, and replaced by the statistical classifier
    # once the macro panel exists.
    probs = _regime_probabilities(price, tx)
    notes = [
        "Elasticities are conservative priors, not fitted coefficients.",
        "Regime probabilities are a heuristic mapping of the simulated impacts.",
    ]
    return ScenarioOutput(
        price_impact_pct=price,
        transaction_impact_pct=tx,
        absorption_impact_pct=absorb,
        regime_probabilities=probs,
        uncertainty_pct=uncertainty_pct,
        notes=notes,
    )


def _regime_probabilities(price_impact: float, tx_impact: float) -> dict[str, float]:
    import math

    composite = 0.6 * price_impact * 10 + 0.4 * tx_impact * 10  # scale ~[-3,3]
    # softmax over fixed anchors EXPANSION>STABLE>CONTRACTION>CRISIS
    anchors = {
        MarketRegime.EXPANSION.value: 1.0,
        MarketRegime.STABLE.value: 0.0,
        MarketRegime.CONTRACTION.value: -0.6,
        MarketRegime.CRISIS.value: -1.6,
    }
    weights = {k: math.exp(-0.9 * abs(composite - a)) for k, a in anchors.items()}
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}
