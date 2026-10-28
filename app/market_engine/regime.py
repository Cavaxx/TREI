"""Market Regime Index and classification (sections 24-25).

Design principles:
  * never classify on a single indicator;
  * standardize each component against its own history (rolling z-score);
  * orient every component so that "positive" means "market-supportive";
  * return a *confidence* and the per-component contributions, so the UI can
    always explain *why* a classification was produced (section 37).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean, pstdev

from app.core.enums import MarketRegime

# Component orientation: +1 means "higher value is more supportive of the
# market", -1 means "higher value is a headwind" (e.g. supply, mortgage rates).
COMPONENT_ORIENTATION: dict[str, int] = {
    "price_momentum": +1,
    "transaction_momentum": +1,
    "liquidity": +1,
    "supply": -1,
    "credit": -1,          # higher mortgage rate => headwind
    "demography": +1,
    "employment": +1,
    "income": +1,
    "inflation_real_rate": -1,
}

# Default weighting; interpretable model = weighted average of oriented z-scores.
DEFAULT_WEIGHTS: dict[str, float] = {
    "price_momentum": 0.20,
    "transaction_momentum": 0.18,
    "liquidity": 0.18,
    "supply": 0.14,
    "credit": 0.12,
    "demography": 0.10,
    "employment": 0.05,
    "income": 0.03,
}


def zscore(value: float | None, history: list[float | None]) -> float | None:
    """Standardize ``value`` against the mean/stdev of ``history``.

    Uses population stdev. Returns None if there is not enough history.
    """
    clean = [h for h in history if h is not None]
    if value is None or len(clean) < 3:
        return None
    mean = fmean(clean)
    sd = pstdev(clean)
    if sd == 0:
        return 0.0
    return (value - mean) / sd


def percentile_rank(value: float | None, history: list[float | None]) -> float | None:
    """Fraction of history <= value, in [0, 1]. None-safe."""
    clean = sorted(h for h in history if h is not None)
    if value is None or not clean:
        return None
    less_eq = sum(1 for h in clean if h <= value)
    return less_eq / len(clean)


@dataclass
class RegimeResult:
    regime: MarketRegime
    score: float
    confidence: float
    contributions: dict[str, float] = field(default_factory=dict)
    standardized: dict[str, float | None] = field(default_factory=dict)
    coverage: float = 1.0  # share of weight actually backed by data

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 3),
            "coverage": round(self.coverage, 3),
            "contributions": {k: round(v, 3) for k, v in self.contributions.items()},
            "standardized": {
                k: (None if v is None else round(v, 3)) for k, v in self.standardized.items()
            },
        }


def classify(
    components: dict[str, float | None],
    history: dict[str, list[float | None]] | None = None,
    weights: dict[str, float] | None = None,
) -> RegimeResult:
    """Classify the market regime from oriented z-scored components.

    ``components`` are *current* raw values keyed by component name.
    ``history`` supplies the trailing history used for standardization; when a
    component has no history it is dropped and ``coverage`` reflects that.
    """
    weights = weights or DEFAULT_WEIGHTS
    history = history or {}

    standardized: dict[str, float | None] = {}
    contributions: dict[str, float] = {}
    weight_used = 0.0
    weight_total = sum(weights.values())

    for name, raw in components.items():
        orient = COMPONENT_ORIENTATION.get(name, +1)
        z = zscore(raw, history.get(name, []))
        if z is None:
            standardized[name] = None
            continue
        oriented = orient * z
        standardized[name] = oriented
        w = weights.get(name, 0.0)
        contributions[name] = w * oriented
        weight_used += w

    if weight_used == 0:
        return RegimeResult(
            regime=MarketRegime.STABLE,
            score=0.0,
            confidence=0.0,
            contributions=contributions,
            standardized=standardized,
            coverage=0.0,
        )

    # Renormalize so a partial-coverage index is comparable across time.
    score = sum(contributions.values()) / weight_used
    coverage = weight_used / weight_total if weight_total else 0.0

    regime = _score_to_regime(score)
    confidence = _confidence(score, coverage, standardized)
    return RegimeResult(
        regime=regime,
        score=score,
        confidence=confidence,
        contributions=contributions,
        standardized=standardized,
        coverage=coverage,
    )


def _score_to_regime(score: float) -> MarketRegime:
    if score >= 0.6:
        return MarketRegime.EXPANSION
    if score <= -1.2:
        return MarketRegime.CRISIS
    if score <= -0.35:
        return MarketRegime.CONTRACTION
    return MarketRegime.STABLE


def _confidence(score: float, coverage: float, standardized: dict[str, float | None]) -> float:
    """Confidence in [0, 1].

    Combines (a) how far the score is from the neutral boundary, (b) how many
    components were actually available, and (c) agreement among components
    (low dispersion of signs => higher confidence).
    """
    distance = min(abs(score) / 1.5, 1.0)  # 0 at neutral, 1 at +/-1.5 sigma
    agreement = _sign_agreement(score, standardized)
    conf = 0.5 * distance + 0.3 * coverage + 0.2 * agreement
    return max(0.0, min(1.0, conf))


def _sign_agreement(score: float, standardized: dict[str, float | None]) -> float:
    vals = [v for v in standardized.values() if v is not None]
    if not vals:
        return 0.0
    if score == 0:
        return 0.5
    same = sum(1 for v in vals if (v >= 0) == (score >= 0))
    return same / len(vals)
