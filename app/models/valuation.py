"""Property valuation (sections 21-23).

Combines a hedonic estimate (interpretable, fitted on local comparables) with a
direct comparable-based estimate. Classification thresholds are NOT arbitrary:
they are derived from the model's own prediction interval, so a listing is
"UNDER/IN/OVER" market relative to the uncertainty the data can actually
support.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.core.enums import Confidence, ValuationClass
from app.core.versions import model_metadata
from app.models.comparables import Comparable, find_comparables
from app.models.hedonic import HedonicModel


@dataclass
class Valuation:
    estimated_market_value: float
    estimated_market_price_m2: float | None
    prediction_interval_low: float
    prediction_interval_high: float
    deviation: float
    classification: ValuationClass
    confidence: Confidence
    comparables_count: int
    method: str
    comparables: list[Comparable] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "estimated_market_value": round(self.estimated_market_value, 0),
            "estimated_market_price_m2": None
            if self.estimated_market_price_m2 is None
            else round(self.estimated_market_price_m2, 1),
            "prediction_interval_low": round(self.prediction_interval_low, 0),
            "prediction_interval_high": round(self.prediction_interval_high, 0),
            "deviation": round(self.deviation, 4),
            "classification": self.classification.value,
            "confidence": self.confidence.value,
            "comparables_count": self.comparables_count,
            "method": self.method,
            "provenance": self.provenance,
        }


def _comparables_estimate(comps: list[Comparable]) -> tuple[float | None, float | None]:
    """Robust median €/m² and a robust dispersion (MAD)."""
    pairs = [c.price_m2 for c in comps if c.price_m2]
    if not pairs:
        return None, None
    vals = sorted(pairs)
    if len(vals) % 2:
        median = vals[len(vals) // 2]
    else:
        median = (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    mad = statistics.median([abs(v - median) for v in vals]) if len(vals) > 1 else 0.0
    return median, mad


def classify_deviation(deviation: float, low_dev: float, high_dev: float) -> ValuationClass:
    """Classify using the prediction-interval-implied deviation band."""
    if deviation < low_dev:
        return ValuationClass.UNDER_MARKET
    if deviation > high_dev:
        return ValuationClass.OVER_MARKET
    return ValuationClass.IN_LINE


def _confidence_from(n_comps: int, interval_rel_width: float) -> Confidence:
    """HIGH/MEDIUM/LOW from comparable count and interval width."""
    if n_comps >= 20 and interval_rel_width < 0.35:
        return Confidence.HIGH
    if n_comps >= 8 and interval_rel_width < 0.6:
        return Confidence.MEDIUM
    return Confidence.LOW


def value_property(
    subject: dict,
    pool: list[dict],
    model: HedonicModel | None = None,
    top_n: int = 12,
    min_comparables_for_full_model: int = 8,
) -> Valuation:
    """Estimate market value for ``subject`` from the candidate ``pool``.

    If a fitted ``model`` with enough comparables is available we blend the
    hedonic and comparable estimates; otherwise we fall back to the
    comparables-only estimate and flag the method accordingly.
    """
    comps = find_comparables(
        subject, pool, top_n=top_n, exclude_master=subject.get("property_master_id")
    )
    surface = subject.get("surface_m2")
    asking = subject.get("price")

    comp_pm2, comp_mad = _comparables_estimate(comps)
    comp_value = comp_pm2 * surface if comp_pm2 and surface else None

    if comp_pm2 and comp_mad:
        lo_pm2, hi_pm2 = comp_pm2 - 1.96 * comp_mad, comp_pm2 + 1.96 * comp_mad
    elif comp_pm2:
        lo_pm2, hi_pm2 = comp_pm2 * 0.8, comp_pm2 * 1.2
    else:
        lo_pm2 = hi_pm2 = None

    method = "comparables_only"
    value = comp_value
    lo = lo_pm2 * surface if (lo_pm2 and surface) else None
    hi = hi_pm2 * surface if (hi_pm2 and surface) else None

    if model is not None and model.coefficients is not None and len(comps) >= min_comparables_for_full_model:
        try:
            h_value = float(model.predict([subject])[0])
            h_lo, h_hi = model.prediction_interval([subject])
            if value is not None:
                value = 0.5 * value + 0.5 * h_value
                lo = 0.5 * (lo or float(h_lo[0])) + 0.5 * float(h_lo[0])
                hi = 0.5 * (hi or float(h_hi[0])) + 0.5 * float(h_hi[0])
            else:
                value = h_value
                lo, hi = float(h_lo[0]), float(h_hi[0])
            method = "hedonic_plus_comparables"
        except Exception:  # noqa: BLE001 - never fail a valuation on model edge cases
            pass

    if value is None:
        return Valuation(
            estimated_market_value=float("nan"),
            estimated_market_price_m2=None,
            prediction_interval_low=float("nan"),
            prediction_interval_high=float("nan"),
            deviation=float("nan"),
            classification=ValuationClass.IN_LINE,
            confidence=Confidence.LOW,
            comparables_count=len(comps),
            method="insufficient_comparables",
            comparables=comps,
            provenance=model_metadata(),
        )

    est_pm2 = value / surface if surface else None
    deviation = (asking / value - 1.0) if (asking and value) else float("nan")

    low_dev = (lo / value - 1.0) if lo else -0.10
    high_dev = (hi / value - 1.0) if hi else 0.10
    classification = classify_deviation(deviation, low_dev, high_dev)

    width = ((hi - lo) / value) if (lo and hi) else 0.5
    confidence = _confidence_from(len(comps), width)

    return Valuation(
        estimated_market_value=value,
        estimated_market_price_m2=est_pm2,
        prediction_interval_low=lo if lo else value * 0.85,
        prediction_interval_high=hi if hi else value * 1.15,
        deviation=deviation,
        classification=classification,
        confidence=confidence,
        comparables_count=len(comps),
        method=method,
        comparables=comps,
        provenance=model_metadata(),
    )

