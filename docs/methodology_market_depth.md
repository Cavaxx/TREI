# Methodology: Market Depth & Regime

## 1. Depth metrics (section 13)

For an area and period we compute, from clearly separated inputs:

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| `stock_turnover` | annual transactions / total dwellings | % of the physical stock traded per year |
| `visible_turnover` | annual transactions / avg active listings | how many times the *observed* stock is absorbed per year |
| `active_supply_ratio` | avg active listings / total dwellings | **market-visible** supply share |
| `monthly_absorption` | monthly closed sales / avg active listings | speed of absorption |
| `months_of_supply` | avg active listings / avg monthly closed sales | inventory horizon (months) |
| `annualized_inventory_turnover` | annual closed sales / avg active listings | annualised absorption |

All metrics are `None`-safe: a missing/zero input yields `unavailable`, never 0.

> **Key distinction:** `active_supply_ratio` is *market-visible* supply, computed
> from observed online listings. It is **not** total supply and must never be
> presented as such (section 13C).

## 2. Transaction inputs

- `stock_turnover` uses official transaction counts (series `OMI_NTN`,
  `FIMAA_AE`, `ISPAT_NOTARIAL` — kept separate) over the dwelling stock.
- `visible_turnover` uses the same transaction counts over observed listings.
- Because the two share the "annual transactions" numerator but differ in the
  denominator, they must be shown together, never blended.

## 3. Time on market (section 17)

- We default to **observed** time on market (days a listing was present).
- A disappearance is never recorded as a confirmed sale; `listing_episode`
  carries `reason_ended = removed | sold_confirmed | unknown`.
- Estimated time-to-sale (model) and survey-based time-to-sale are separate
  `kind` values and are never merged with the observed series.

## 4. Households & demography (section 15)

- We report `ΔH_t`, `Δ²H_t` (acceleration), `H_t − H_{t−2}`, and the two-year
  average annual change, and the difference between household growth and
  population growth.
- Household formation can outpace population change; both are shown.

## 5. Regime (sections 24-25)

- Multi-component, oriented, z-scored composite (see `docs/model_spec.md §4`).
- Never classified from a single indicator; the per-component contributions and
  `coverage` are always exposed so the user can see what drove the result.
- Confidence is reported alongside the label; low-coverage classifications are
  presented as provisional.

## 6. Forecasting (sections 26-28)

- Directional probabilities come from the empirical distribution of overlapping
  historical windows — a conditional frequency, not a point forecast.
- Point forecasts (when shown) come from walk-forward-validated models that beat
  a random-walk naive baseline; otherwise we show the naive expectation.
- With too little history we show `INSUFFICIENT_EVIDENCE` and no probability.
