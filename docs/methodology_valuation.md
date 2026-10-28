# Methodology: Valuation

Implements sections 20-23. Goal: an explainable estimate of market value for a
property, with an interval and a confidence, and a classification of a listing
as `UNDER_MARKET` / `IN_LINE` / `OVER_MARKET`.

## 1. Pipeline

```
subject property
   ├─ comparable search (app/models/comparables.py)
   │     → similarity-weighted median €/m², dispersion (MAD)
   │     → comparables-only estimate + interval
   └─ hedonic model (if fitted & ≥ 8 comparables)
         → log-price estimate + residual-based interval
   → blend 50/50
   → classification from the prediction interval
   → confidence from comparable count + interval width
```

## 2. Comparable selection

Weighted similarity (`app/models/comparables.py`):

| Criterion | Weight | Rule |
|-----------|--------|------|
| same comune | 0.30 | exact match |
| geo proximity | 0.20 | `1 − d/1000 m` decay |
| surface within 20 % | 0.22 | linear decay to 0 at 20 % |
| same condition | 0.12 | exact |
| same rooms | 0.08 | exact |
| same energy class | 0.08 | exact |

Minimum similarity to be used: 0.45. The subject's own master is excluded.
Each returned comparable carries `differences` (distance, surface delta, …) so a
valuation is always explainable.

## 3. Estimate & interval

- **Comparables-only**: median €/m² × surface; interval `±1.96·MAD` (or ±20 %
  when MAD is unavailable).
- **Hedonic**: ridge on `log(price)`; interval `exp(logŷ ± 1.96σ)`.
- **Blend**: 50/50 when both are available and comparables ≥ 8.

## 4. Classification (not arbitrary)

`deviation = asking / estimate − 1`. The band is the prediction interval
re-expressed as a deviation:

```
low_dev  = interval_low  / estimate − 1
high_dev = interval_high / estimate − 1
UNDER_MARKET  if deviation < low_dev
OVER_MARKET   if deviation > high_dev
IN_LINE       otherwise
```

This ties the classification to the uncertainty the data can support: with thin
data the interval is wide and fewer listings are called over/under-priced.

## 5. Confidence

| Level | Condition |
|-------|-----------|
| HIGH | ≥ 20 comparables and interval width < 35 % |
| MEDIUM | ≥ 8 comparables and interval width < 60 % |
| LOW | otherwise (including comparable-free valuations) |

## 6. Hard rules

- Asking prices are never treated as transacted prices.
- No estimate is produced without comparables; the API returns
  `method = insufficient_comparables` instead of inventing a value.
- Every response includes `provenance = {model_version, features_version,
  data_cutoff}`.
- The hedonic model is refit per (blocking) area; province-wide fits are not used
  to value an individual unit.
