"""Seller-pressure evidence (§40).

This is deliberately *not* a diagnosis of the seller's intentions. It is a
transparent, weighted summary of observable facts that are consistent with price
pressure, reported with its own coverage so a thin listing never receives a
confident score.

Wording rule: the output never says "desperate seller". It says "elevated
evidence of price pressure", and it lists the contributing facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

#: Component weights, summing to 1.0. They describe *observable* evidence only.
DEFAULT_WEIGHTS: dict[str, float] = {
    "time_on_market": 0.26,
    "price_cuts": 0.20,
    "cut_depth": 0.16,
    "cut_recency": 0.10,
    "inventory": 0.14,
    "absorption": 0.08,
    "regime": 0.06,
}

#: How much pressure each market regime implies (higher = more pressure).
_REGIME_PRESSURE = {"CRISIS": 1.0, "CONTRACTION": 0.7, "STABLE": 0.4, "EXPANSION": 0.1}

_LEVELS = ((0.80, "HIGH"), (0.60, "ELEVATED"), (0.40, "MODERATE"), (0.18, "LOW"))

_NEUTRAL_DESCRIPTION = {
    "HIGH": "Very high evidence of price pressure on this listing.",
    "ELEVATED": "Elevated evidence of price pressure on this listing.",
    "MODERATE": "Moderate evidence of price pressure on this listing.",
    "LOW": "Limited evidence of price pressure on this listing.",
    "NONE": "No evidence of price pressure on this listing.",
}


def _clamp01(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, value))


@dataclass
class SellerPressure:
    score: float
    level: str
    coverage: float
    components: dict[str, float] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["score"] = round(self.score, 3)
        data["coverage"] = round(self.coverage, 3)
        data["components"] = {k: round(v, 3) for k, v in self.components.items()}
        return data


def seller_pressure_score(
    *,
    days_on_market: int | None = None,
    price_cut_count: int | None = None,
    cumulative_discount: float | None = None,
    days_since_last_cut: int | None = None,
    months_of_supply: float | None = None,
    monthly_absorption: float | None = None,
    market_regime: str | None = None,
    weights: dict[str, float] | None = None,
) -> SellerPressure:
    """Weighted evidence summary in [0, 1] over the components that are present."""
    weights = weights or DEFAULT_WEIGHTS
    raw: dict[str, float | None] = {
        "time_on_market": _clamp01((days_on_market or 0) / 365) if days_on_market is not None else None,
        "price_cuts": _clamp01(price_cut_count / 3) if price_cut_count is not None else None,
        "cut_depth": _clamp01((cumulative_discount or 0) / 0.15) if cumulative_discount is not None else None,
        "cut_recency": (
            _clamp01(1 - (days_since_last_cut or 0) / 180) if days_since_last_cut is not None else None
        ),
        "inventory": _clamp01((months_of_supply or 0) / 18) if months_of_supply is not None else None,
        "absorption": (
            _clamp01(1 - monthly_absorption * 12) if monthly_absorption is not None else None
        ),
        "regime": _REGIME_PRESSURE.get(str(market_regime).upper()) if market_regime else None,
    }

    components: dict[str, float] = {}
    weight_used = 0.0
    for name, value in raw.items():
        if value is None:
            continue
        w = weights.get(name, 0.0)
        components[name] = w * value
        weight_used += w

    total_weight = sum(weights.values()) or 1.0
    if weight_used == 0:
        return SellerPressure(
            score=0.0,
            level="NONE",
            coverage=0.0,
            components={},
            evidence=["no observable price-pressure evidence available"],
            description=_NEUTRAL_DESCRIPTION["NONE"],
        )

    score = sum(components.values()) / weight_used
    level = next((label for threshold, label in _LEVELS if score >= threshold), "NONE")
    evidence = _evidence_lines(
        days_on_market=days_on_market,
        price_cut_count=price_cut_count,
        cumulative_discount=cumulative_discount,
        days_since_last_cut=days_since_last_cut,
        months_of_supply=months_of_supply,
        monthly_absorption=monthly_absorption,
        market_regime=market_regime,
    )
    return SellerPressure(
        score=score,
        level=level,
        coverage=weight_used / total_weight,
        components=components,
        evidence=evidence,
        description=_NEUTRAL_DESCRIPTION[level],
    )


def _evidence_lines(**kw) -> list[str]:
    lines: list[str] = []
    if kw.get("days_on_market") is not None:
        lines.append(f"observed on the market for {kw['days_on_market']} days")
    if kw.get("price_cut_count"):
        lines.append(f"{kw['price_cut_count']} asking-price reduction(s) observed")
    if kw.get("cumulative_discount"):
        lines.append(f"cumulative discount {kw['cumulative_discount'] * 100:.1f}% from first asking price")
    if kw.get("days_since_last_cut") is not None:
        lines.append(f"last reduction {kw['days_since_last_cut']} days ago")
    if kw.get("months_of_supply") is not None:
        lines.append(f"local months of supply {kw['months_of_supply']:.1f}")
    if kw.get("monthly_absorption") is not None:
        lines.append(f"monthly absorption {kw['monthly_absorption'] * 100:.1f}%")
    if kw.get("market_regime"):
        lines.append(f"local market regime {kw['market_regime']}")
    return lines or ["no observable price-pressure evidence available"]
