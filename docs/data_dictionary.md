# Data Dictionary

Canonical field definitions for the platform. Enum values live in
`app/core/enums.py`; storage in `app/db/schema.sql`.

## Identifiers (three distinct levels — never conflate)

| Field | Level | Meaning |
|-------|-------|---------|
| `property_master_id` | physical unit | One real dwelling, after cross-portal dedup. |
| `listing_source_id` | source record | One listing as published by one source. Many → one master. |
| `listing_episode_id` | time segment | One continuous period on market within a master; survives re-publishing. |
| `master_auction_id` | auction lot | One judicial lot after cross-portal dedup. |

## Price semantics

| Field | Type | Notes |
|-------|------|-------|
| `measure_type` | enum `PriceMeasureType` | `asking`, `omi_min`, `omi_max`, `transaction_observed`, `transaction_estimated`, `auction_base`, `auction_minimum_offer`, `appraisal_value`. |
| `price` | numeric | Currency amount in EUR. |
| `price_m2` | numeric | `price / surface_m2`. |
| `kind` | text | `publisher_asking` \| `portal_asking` \| `omi_quote` \| … |
| `is_asking` | bool | Derived; guards against mixing asking with transactions. |

> Asking, OMI and transacted prices are **separate series** and are never pooled.

## Transaction series (kept separate)

| Series id | Source | Unit |
|-----------|--------|------|
| `OMI_NTN` | Agenzia Entrate (NTN) | count |
| `FIMAA_AE` | FIMAA / Agenzia Entrate report | count |
| `ISPAT_NOTARIAL` | ISPAT notarial deeds | count |

## Period semantics

`period` ∈ `YYYY` \| `YYYY_Qn` \| `YYYY_YTD` \| `YYYY_S1` \| `YYYY_S2`.
`period_status` ∈ `observed` \| `estimated` \| `annualized` \| `interpolated`.
A partial period is **never** silently annualized (section 9).

## Market metrics

| Metric | Definition | Guard |
|--------|-----------|-------|
| `stock_turnover` | annual transactions / total dwellings | needs stock |
| `visible_turnover` | annual transactions / avg active listings | ≠ stock turnover |
| `active_supply_ratio` | avg active listings / total dwellings | *visible* supply only |
| `monthly_absorption` | monthly closed sales / avg active listings | — |
| `months_of_supply` | avg active listings / avg monthly closed sales | — |
| `annualized_inventory_turnover` | annual closed sales / avg active listings | — |

## Time on market

`kind` ∈ `observed_time_on_market` (portal data) \| `estimated_time_to_sale`
(model) \| `survey_based_time_to_sale` (surveys). These are never mixed.

## Regime

`regime` ∈ `EXPANSION` \| `STABLE` \| `CONTRACTION` \| `CRISIS`.
Each snapshot stores `score`, `confidence`, `coverage` and per-component
`contributions` (JSONB) so the classification is explainable.

## Valuation

| Field | Meaning |
|-------|---------|
| `estimated_market_value` | Point estimate of market value (EUR). |
| `estimated_market_price_m2` | Point estimate of €/m². |
| `prediction_interval_low/high` | Interval from residual sigma / comparable MAD. |
| `deviation` | `asking / estimate - 1`. |
| `classification` | `UNDER_MARKET` \| `IN_LINE` \| `OVER_MARKET` (band from the interval). |
| `confidence` | `HIGH` \| `MEDIUM` \| `LOW` (comparable count + interval width). |
| `method` | `comparables_only` \| `hedonic_plus_comparables` \| `insufficient_comparables`. |
| `provenance` | `{model_version, features_version, data_cutoff}`. |

## Auction fields

`base_price`, `minimum_offer`, `appraisal_value`, `occupancy_status`,
`has_surface_right`, `has_usufruct`, `has_building_abuse`, `has_cadastral_discrepancy`,
`has_mortgages`, `has_condominium_arrears`, `document_hash`, `sources[]`.
Derived: `estimated_total_acquisition_cost`, `risk_adjusted_discount`,
`naive_discount`, `risk_level` (`LOW`/`MEDIUM`/`HIGH`).

## Provenance / lineage columns

Every stored observation carries: `source`, `source_url`, `retrieved_at`,
`last_seen_at`, `content_hash`, `parser_version`, `schema_version`,
`normalization_version`, and (for estimates) `model_version`, `data_cutoff`.

## Versioning

`app/core/versions.py` defines `SCHEMA_VERSION`, `PARSER_VERSION`,
`NORMALIZATION_VERSION`, `MODEL_VERSION`, `FEATURES_VERSION`, `DEFAULT_DATA_CUTOFF`.
Bumping a constant invalidates cached features/models derived from it.
