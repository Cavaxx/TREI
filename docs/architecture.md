# Architecture

## 1. Principles

- **Modular containers**, each with a single responsibility (section 59).
- **PostgreSQL + PostGIS** is the primary store. **Redis** is only a queue/cache/
  lock (never the primary DB). **MinIO/S3** holds raw snapshots.
- **Compliance is enforced in code**: the fetcher base class consults the source
  registry and robots.txt on every request.
- **Everything is traceable**: lineage + versions on every record and estimate.

## 2. Container topology (docker-compose)

```
                      +-------------------+
  browser  ─────────► │ frontend (…)      │  static demo served by API today;
                      +---------┬---------+  Next.js app scaffolded
                                │
                      +---------▼---------+
                      │ api (FastAPI)     │  read/aggregate/valuation endpoints
                      +----┬───────┬──────+
                           │        │
             +-------------▼--+  +--▼------------------+
             │ postgres+postgis│  │ redis (queue/cache)│
             └--------┬--------┘  └----------┬----------┘
                      │                      │
             +--------▼--------+   +---------▼---------+
             │ minio (S3 raw)  │   │ worker (fetchers) │
             └-----------------┘   └-------------------┘
```

| Service | Role |
|---------|------|
| `postgres` | PostGIS primary datastore (`app/db/schema.sql`) |
| `redis` | Celery/RQ broker, rate-limit locks, caches (never source of truth) |
| `minio` | Raw snapshot object storage (`raw_object` metadata in Postgres) |
| `api` | FastAPI app (`app/api/main.py`) |
| `worker` | Scheduled fetch/normalize/dedup/aggregate jobs |
| `frontend` | Next.js scaffold (dashboard); the API also serves a static demo page |

## 3. Repository map

```
app/
  core/        config, versions, enums, lineage            (no I/O)
  db/          schema.sql, repository, demo seed
  fetchers/    registry, robots, base adapter, adapters/, cli
  processors/  normalize, dedup, quality
  market_engine/ depth, demography, prices, regime
  auction_engine/ valuation, anomaly, dedup
  models/      hedonic, comparables, valuation, forecast, scenarios, affordability
  api/         FastAPI app, schemas, deps
  frontend/    static/Next.js scaffold
config/        source_registry.yaml (single source of truth for sources)
docs/          this document and the methodology specs
tests/         pytest suite (runs with no external services)
scripts/       operational helpers
docker/        docker-compose.yml + Dockerfiles
```

## 4. Data flow

```
fetch (registry+robots gated)
  → raw snapshot to MinIO + raw_object row (content_hash, parser_version)
  → parse to raw records (JSON-LD / embedded JSON preferred)
  → normalize (units, comune aliases, categories)  [normalization_version]
  → deduplicate → property_master_id
  → reconstruct episodes → listing_episode_id
  → append price observations (measure_type explicit)
  → aggregates (price/m², depth metrics)
  → models (hedonic/comparables valuation, regime, forecast)  [model_version]
  → API + dashboard
```

## 5. Geo hierarchy

Province → Comunità di Valle → Comune → microzona → OMI zone → geohash
(`geo_area` self-referencing table + `geohash`/`geometry` columns). Local regime
is computable independently of the provincial aggregate.

## 6. Separation of concerns that must never be violated

- Asking ≠ OMI ≠ transacted ≠ auction prices (separate `measure_type`).
- `OMI_NTN` ≠ `FIMAA_AE` ≠ `ISPAT_NOTARIAL` (separate series ids).
- Observed ≠ estimated ≠ survey time-to-sale.
- Auction lots have their own valuation model and a separate `auction_lot` table.
- Redis/MinIO never become the primary datastore.

## 7. Running locally

```bash
make install       # create .venv and install deps
make test          # run the pytest suite (no services needed)
make lint          # ruff
make api           # run the FastAPI app (seeded with synthetic demo data)
make up            # docker compose up (postgres+postgis, redis, minio, api)
make cli-list      # show the source registry and legal status
```
