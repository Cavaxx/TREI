# Documentation Index

| Document | What it covers |
|----------|----------------|
| [`data_sources.md`](data_sources.md) | Phase-1 source audit (robots.txt, ToS, APIs, licences, rate limits) |
| [`legal_data_access.md`](legal_data_access.md) | Binding data-access policy and tier decisions |
| [`authorized_feeds.md`](authorized_feeds.md) | Runbook for licensed APIs/exports (licence register, credentials, evidence log) |
| [`data_dictionary.md`](data_dictionary.md) | Field/enum reference, identifiers, price semantics |
| [`architecture.md`](architecture.md) | Containers, storage, data flow, repo map |
| [`model_spec.md`](model_spec.md) | Features, models, validation, intervals, confidence |
| [`methodology_market_depth.md`](methodology_market_depth.md) | Depth metrics, demography, regime, forecasting |
| [`methodology_valuation.md`](methodology_valuation.md) | Property valuation & listing classification |
| [`methodology_auctions.md`](methodology_auctions.md) | Auction valuation, risk flags, lot dedup |

The machine-readable source of truth for sources is
[`../config/source_registry.yaml`](../config/source_registry.yaml); the field
definitions there are enforced at runtime by `app/fetchers/registry.py`.
