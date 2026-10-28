# Trentino Real Estate Intelligence (TREI)

A containerised market-intelligence platform for the **real-estate market of the
Autonomous Province of Trento**: ingestion, cross-portal deduplication, price
history, €/m² dashboard, hedonic/comparable valuation, auction analysis and a
market-regime engine — built **compliance-first**.

> **This repository is an MVP foundation.** No real listings have been ingested
> yet: the API is seeded with clearly-flagged **synthetic** demo data so the
> pipeline is demonstrable end-to-end while the legally-verified data sources are
> onboarded (see `docs/data_sources.md`).

## Guiding principles

- **Audit before automation.** Every source is verified (robots.txt, ToS, API,
  licence, rate limits) before an adapter exists — see `config/source_registry.yaml`.
- **Never bypass protections.** Sources behind anti-bot/auth/paywalls are Tier 5
  (manual import + link only). The only automated route into such a source is an
  **authorized feed**: a contract, its named endpoints, and credentials the
  operator legitimately holds — see `docs/authorized_feeds.md`.
- **No licence, no request.** `config/source_licences.yaml` is a hard gate:
  without an active record covering the exact endpoint, the fetcher refuses and
  writes the refusal to an append-only evidence log.
- **Three identifiers, never conflated:** `property_master_id` (physical unit),
  `listing_source_id` (one listing), `listing_episode_id` (time segment).
- **Asking ≠ OMI ≠ transacted ≠ auction prices**, kept in separate series.
- **No invented data.** Missing ⇒ `unavailable`/`estimated`, never imputed; every
  estimate carries a prediction interval, a confidence and a provenance block.
- **Leakage-free validation** via walk-forward only.
- **PostgreSQL+PostGIS** is the primary store; **Redis** is queue/cache only;
  **MinIO/S3** holds raw snapshots.

## Quickstart

```bash
make install          # create .venv and install dependencies
make test             # 169 tests, no external services required
make lint             # ruff
make api              # http://localhost:8000  (synthetic demo data)
make cli-list         # source registry + legal status
make cli-licences     # licence register vs. sources (nothing licensed yet)
make cli-agent-status # what the Agent Import is allowed to touch (no network)
make cli-schedule     # the acquisition timetable, built from the policy
```

Container stack (Postgres+PostGIS, Redis, MinIO, API, worker, and the Phase-2
channels: `email-ingest`, `agent-import`, `auction-fetcher`, `scheduler`):

```bash
make up               # docker compose -f docker/docker-compose.yml up --build
```

## Layout

```
app/
  core/           config, versioning, enums, lineage, per-field extraction primitives
  storage/        raw-object data lake (§45)
  db/             PostGIS schema, in-memory repository, synthetic seed
  fetchers/       source registry, robots.txt, licence register, credentials,
                  audit log, adapters (open-data + authorized feed), CLI
  email_alert/    Channel C: mailbox readers, portal profiles, parser, ingest pipeline, CLI
  agent_import/   Channel D: policy resolver, rate limiter, extractors, browser/
                  vision fallbacks, CrawlerManager, crawl state, CLI
  auction_fetcher/ auctions + RSS + licensed PVP export, deduplication, CLI
  property_master/ identity resolution, snapshots, price history, coverage, seller pressure
  processors/     normalize, deduplicate, territory filter, shared text extraction
  market_engine/  depth, demography, prices, regime, macro panel
  auction_engine/ valuation, anomaly detection, lot dedup
  models/         hedonic, comparables, valuation, forecast, scenarios, affordability
  scheduler/      policy-aware acquisition timetable (§47)
  api/            FastAPI app + schemas
  frontend/       static demo (served by API) + Next.js scaffold notes
config/           source_registry.yaml (sources + crawl config), source_licences.yaml (contracts)
docs/             audit, legal policy, data dictionary, architecture, model spec, methodology
tests/            pytest suite
var/audit/        append-only access evidence log (generated, git-ignored)
data/raw/         raw-object data lake (generated, git-ignored)
```

## API (selected)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | liveness + counts |
| GET | `/api/v1/meta` | versions + provenance |
| GET | `/api/v1/sources` | source registry, legal status & licence status |
| GET | `/api/v1/licences` | licence register: what we may automatically fetch |
| GET | `/api/v1/agent/sources` | what the Agent Import is allowed to touch (policy verdict) |
| GET | `/api/v1/coverage` | what TREI observes, through which sources (never a completeness claim) |
| GET | `/api/v1/review-queue` | low-confidence extractions awaiting a human |
| GET | `/api/v1/crawl/metrics` | crawler health per source |
| GET | `/api/v1/property/{property_master_id}` | listing page: fair value, history, source coverage |
| GET | `/api/v1/market/summary` | asking €/m² by comune |
| GET | `/api/v1/market/depth` | depth metrics (stock vs visible turnover) |
| POST | `/api/v1/market/macro/context` | macro panel → Market Engine inputs (NOT_YET_PUBLISHED aware) |
| GET | `/api/v1/regime/demo` | regime classification + contributions |
| GET | `/api/v1/listings` | listing explorer |
| POST | `/api/v1/valuation` | value a property (interval + confidence) |
| GET | `/api/v1/auctions` | auction lots with risk flags |
| POST | `/api/v1/auctions/value` | risk-adjusted auction value |
| POST | `/api/v1/forecast/directional` | P(up/flat/down) or INSUFFICIENT_EVIDENCE |
| POST | `/api/v1/scenarios/run` | shock propagation |
| POST | `/api/v1/affordability` | mortgage affordability |

## Documentation

- [`docs/data_sources.md`](docs/data_sources.md) — Phase-1 source audit
- [`docs/legal_data_access.md`](docs/legal_data_access.md) — binding access policy
- [`docs/authorized_feeds.md`](docs/authorized_feeds.md) — licensed API/export runbook
- [`docs/phase2_channels.md`](docs/phase2_channels.md) — email / agent import / auctions / macro, and the controls on each
- [`docs/data_dictionary.md`](docs/data_dictionary.md) — field & enum reference
- [`docs/architecture.md`](docs/architecture.md) — containers & data flow
- [`docs/model_spec.md`](docs/model_spec.md) — models, validation, intervals
- [`docs/methodology_market_depth.md`](docs/methodology_market_depth.md)
- [`docs/methodology_valuation.md`](docs/methodology_valuation.md)
- [`docs/methodology_auctions.md`](docs/methodology_auctions.md)

## Roadmap (next milestones)

1. Wire the first **Tier-3 open dataset** (OMI quotations, ISTAT/IPAB) into the
   macro panel with full lineage.
2. Verify the remaining `UNKNOWN` sources and onboard any Tier-1/2 partner via
   `config/source_licences.yaml` (see `docs/authorized_feeds.md`).
3. Fill `crawl.search_urls` / `crawl.feeds` in the registry for the sources whose
   robots.txt and ToS have been confirmed, so the Agent Import and the auction
   channel start running for real.
4. Persist the Property Master, crawl state, snapshots and auctions to
   PostGIS (replace the in-memory stores) and add the Redis-backed worker.
5. Confirm a geocoding provider and wire `geocoding_precision` so address-level
   coordinates are never confused with a municipality centroid.
6. Build the Next.js map/dashboard against real data (listing page, coverage and
   crawler-health views already have their endpoints).
