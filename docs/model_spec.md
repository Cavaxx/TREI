# Model Specification

Implements sections 20-28 (features, models, validation, intervals, depth).
All models are interpretable-first; every estimate ships with an interval and a
confidence level.

## 1. Features (MVP, `app/models/hedonic.py`)

| Feature | Kind | Notes |
|---------|------|-------|
| `surface_m2` | numeric | standardised |
| `rooms`, `bathrooms`, `floor` | numeric | standardised |
| `has_elevator`, `has_garage`, `has_garden`, `has_terrace` | numeric (0/1) | |
| `comune` | categorical | one-hot, last level as reference |
| `condition` | categorical | `new`…`to_renovate`, `rustic` |
| `energy_class` | categorical | A4…G |

Target: `log(price)`. `FEATURES_VERSION` tags the feature set.

## 2. Valuation model

### 2.1 Hedonic regression (interpretable baseline)
- Ridge (L2) on `log(price)` via normal equations `(XᵀX + αI)β = Xᵀy`,
  intercept unpenalised. Ridge keeps one-hot categories stable.
- Coefficients are reported on the log scale (≈ relative price effects).
- Residual sigma ⇒ an approximate **prediction interval**
  `exp(logŷ ± z·σ)` (z = 1.96).

### 2.2 Comparable engine (`app/models/comparables.py`)
- Weighted similarity: same comune (0.30), geo proximity (0.20), surface within
  20 % (0.22), condition (0.12), rooms (0.08), energy class (0.08).
- Returns the top-N with per-comparable differences, so a valuation is explainable.
- Deliberately refuses the "average of the comune" fallback.

### 2.3 Blending & classification (`app/models/valuation.py`)
- Comparable estimate = robust median €/m² × surface; dispersion = MAD.
- When the hedonic model is fitted and ≥ 8 comparables exist, blend 50/50.
- Classification thresholds are derived from the **prediction interval**:
  `UNDER_MARKET` if `deviation < low_dev`, `OVER_MARKET` if `> high_dev`, else
  `IN_LINE`.
- Confidence from comparable count and interval width.

## 3. Forecasting (`app/models/forecast.py`)

- **Walk-forward validation only** (expanding window, `train[0..t-1] → test[t]`).
  No shuffling, no future leakage.
- Metrics: MAE, RMSE, MAPE, directional accuracy, Brier; every model is compared
  against a **random-walk naive** baseline and only kept if it improves.
- Directional probabilities are **distribution-free empirical frequencies** over
  overlapping historical windows. If fewer than `min_obs` windows exist, we return
  `INSUFFICIENT_EVIDENCE` rather than a number.

## 4. Regime (`app/market_engine/regime.py`)

- Components: price momentum, transaction momentum, liquidity, supply, credit,
  demography, employment, income. Each is oriented then z-scored against its own
  history.
- Composite = weighted mean of oriented z-scores (weights configurable).
- Classification thresholds: `score ≥ 0.6` EXPANSION; `≤ −1.2` CRISIS;
  `≤ −0.35` CONTRACTION; else STABLE.
- `confidence` blends distance-from-neutral, coverage and sign agreement;
  `coverage` exposes how much weight is backed by data.

## 5. Scenarios & affordability (`app/models/scenarios.py`, `affordability.py`)

- Scenario engine propagates shocks through **documented elasticities** (priors,
  clearly labelled, replaceable by fitted coefficients) to price/transactions/
  absorption and a regime-probability vector.
- Affordability uses the standard annuity formula; outputs monthly payment, total
  interest and debt-service ratio. It **never recommends** an option — it only
  shows the arithmetic and the rate/price sensitivity.

## 6. Anti-leakage & anti-invention rules

1. Never train on data chronologically after the test point.
2. Never mix measures (asking/OMI/transacted/auction) in one model.
3. Never annualize a partial period silently (mark `annualized`).
4. Missing ⇒ `unavailable`/`estimated`; never imputed silently.
5. Every estimate carries `provenance` = `{model_version, features_version,
   data_cutoff}` (and `training_period` where relevant).

## 7. Comparison protocol

Before any model is promoted to `model_registry.is_active`:
- it must beat the naive baseline on walk-forward MAE for its target;
- intervals must be calibrated (empirical coverage ≈ nominal);
- it must be explainable (coefficients or comparable list available).
