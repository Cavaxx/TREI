# Phase 2 — Acquisition channels

How TREI obtains real observations, and the controls that make every channel
legitimate by construction.

```
                 ISPAT / ISTAT / OMI / BCE / Banca d'Italia
                                   │
                           ┌───────────────┐
                           │ MARKET ENGINE │  app/market_engine/{macro,depth,regime,prices}.py
                           └───────┬───────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
       EMAIL ALERTS         AGENT IMPORT          AUCTION / RSS
     app/email_alert/     app/agent_import/     app/auction_fetcher/
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   ▼
                         ┌──────────────────┐
                         │ PROPERTY MASTER  │  app/property_master/
                         └────────┬─────────┘
                                  ▼
                         ┌──────────────────┐
                         │ VALUATION ENGINE │  app/models/valuation.py
                         └────────┬─────────┘
                                  ▼
                         ┌──────────────────┐
                         │  TREI DASHBOARD  │  app/api/main.py
                         └──────────────────┘
```

## 1. The three identities (never conflated)

| Identifier | Meaning | Where |
|---|---|---|
| `property_master_id` | one **physical unit**, however many portals advertise it | `property_master/master.py` |
| `listing_source_id` | one listing on one source | `ListingRecord.listing_source_id` |
| `listing_episode_id` | one **time segment** (remove → relist does not reset days-on-market) | `ListingRecord.listing_episode_id` |

Master ids are **stable**: a component that already contains a known listing keeps
its id, so a portal joining later never orphans the history.

## 2. Channel C — email alerts (`app/email_alert/`)

```
mailbox → EmailFetcher → EmailParser → ListingNormalizer → Property Master
```

* **Transports**: `FileMailbox` (`.eml` drop — the "user legitimately provided the
  content" route, no credentials) and `ImapMailbox` (IMAPS, OAuth2 preferred).
  TREI never stores a mailbox password.
* **Parsing** is profile-driven (`profiles.py`) and deterministic: price, surface,
  rooms, bathrooms, floor, energy class, address, municipality, amenities,
  description (raw + normalized), images, agency, published date.
* **Block splitting**: each listing URL owns the segment from the end of the
  previous link to the end of its own, so one card's price cannot leak into its
  neighbour.
* **Three distinct timestamps**: `first_seen_at` (when TREI saw it),
  `email_received_at` (when the alert arrived), `source_published_at` (what the
  portal claimed). The email channel is often the *first* observation.
* **Deduplication**: on `raw_message_hash` **and** `provider_message_id`, because
  the same alert is frequently re-delivered with a different transport header.
* **Absent fields stay `null`** — never invented.

CLI: `python -m app.email_alert.cli ingest [--mailbox DIR] [--json]`

## 3. Channel D — agent import (`app/agent_import/`)

```
SourceRegistry → PolicyResolver → Robots → AccessDecision → CrawlerManager
     DiscoveryAgent → ListingNavigationAgent → ExtractionAgent
     → ValidationAgent → DeduplicationAgent → Property Master
```

### Policy first (§26)

`PolicyResolver.decide(source)` is asked **once**, before anything else. It
refuses when:

| Condition | Outcome |
|---|---|
| source not in the registry | refused (`UNKNOWN`) |
| `restricted` / `license_required` / `tier5` and no active licence | `REFUSED_NO_LICENCE` |
| status `UNKNOWN` | refused (never auto-enable an unverified source) |
| `tier4` without `scraping_allowed` **and** robots | disabled |
| robots.txt disallows a requested URL | `REFUSED_ROBOTS` |

A refusal costs **zero requests** and is written to `var/audit/access.jsonl`.
No evasion exists anywhere in this package: no CAPTCHA solving, no Cloudflare
bypass, no fingerprint spoofing, no proxy rotation, no fake accounts, no login or
paywall bypass, no rate-limit circumvention.

### Extraction ladder (§11, §29-30)

```
JSON-LD → embedded JSON state → microdata → semantic HTML
        → metadata / OpenGraph → visible text
        → (rendered DOM) → (screenshot / vision)     ← only if enabled AND thin
```

Every value is a `FieldValue { value, method, confidence, evidence }`.
Visible-text patterns only fill fields the structured passes left **empty**. A
value that cannot be coerced is dropped: *a price that is not a number is not a
price*.

### Failure modes (§25)

`HTTP_ERROR · RATE_LIMITED · ROBOTS_BLOCKED · SOURCE_RESTRICTED · PARSER_ERROR ·
SCHEMA_CHANGED · TIMEOUT · INVALID_LISTING · OUT_OF_TERRITORY · DUPLICATE`

A blocked page is never recorded as a property that does not exist.

### Politeness (§27)

Per-source `requests_per_minute`, `concurrency`, `delay_s` come from the registry
(under `crawl:`). Exponential backoff on transient failures; `429`/`503` with
`Retry-After` **pauses the crawl** rather than working around it.

### Fallbacks (§12-13)

* Browser rendering (Playwright) is opt-in (`TREI_ALLOW_BROWSER=true`), sends our
  own descriptive User-Agent, and runs only when the deterministic ladder came
  back empty.
* Screenshots are stored as raw objects with their hash and capture timestamp and
  are *read* by an operator-supplied vision extractor. They are never a means of
  getting past access control.

### Review queue (§31)

Fields below `TREI_REVIEW_CONFIDENCE` (or required fields missing) mark the
listing `review_required` and surface it at `GET /api/v1/review-queue`.

CLI: `python -m app.agent_import.cli status | plan <src> | crawl <src>`

## 4. Auctions (`app/auction_fetcher/`)

```
Astalegale / RSS / licensed PVP export
   → AuctionFetcher → AuctionParser → AuctionNormalizer
   → AuctionDeduplicator → AuctionEngine
```

* Identity is `(tribunale, numero procedura, lotto)`; PVP wins the biographical
  fields, so a re-listing on another portal collapses to one `master_auction_id`.
* Prices are `auction_base` / `auction_minimum_offer` — never mixed with asking
  prices.
* Lot records carry the richer fields when published: cadastral data, appraisal
  date, previous auction dates and prices, building abuses, regularization costs,
  condominium arrears.
* A **licensed PVP export** can be read from a drop directory
  (`python -m app.auction_fetcher.cli pvp-export <dir>`), which is the supported
  route while direct PVP access is unverified.

## 5. Property Master (`app/property_master/`)

* `ListingSnapshot` per change (re-observing identical content adds nothing).
* Price history, observed time on market, price-cut count, cumulative discount.
* `seller_pressure_score` — a transparent weighted summary of observable facts
  (days on market, cuts, cut depth, recency, inventory, absorption, regime). The
  wording is always neutral: *"elevated evidence of price pressure"*, never
  "desperate seller".
* Coverage (§43): `unique_property_master`, `raw_listings`, `active_listings`,
  `coverage_by_source`, `duplicate_rate`, `coverage_confidence` — phrased as
  *"TREI observes X unique properties across Y sources"*, never as completeness.

## 6. Raw objects (§45)

```
data/raw/<source>/<YYYY-MM-DD>/<crawl_id>/{response,html,json,screenshot}
```

Content-addressed and immutable: re-crawling an unchanged page does not grow the
lake. Every structured record links back through `raw_object_id`.

## 7. Services (§46) and scheduling (§47)

`docker/docker-compose.yml` adds `email-ingest`, `agent-import`,
`auction-fetcher` and `scheduler` next to `postgres`, `redis`, `minio`, `api` and
`worker`. Their default commands are diagnostic (`status`, `sources`, `plan`):
nothing fetches a live source until the registry declares it.

`python -m app.scheduler.plan plan` builds the timetable from the registry and the
policy — incremental crawls, less frequent full re-crawls, email polling, at least
a daily auction check, macro refreshes at each source's cadence.

## 8. Macro data (§36-37)

`app/market_engine/macro.py` keeps the rule that shapes everything downstream:

> An unpublished figure is **not** zero and is **not** interpolated.

A period whose official value has not been released is stored with
`data_status = NOT_YET_PUBLISHED` and `value = None`; derived series propagate
`None` rather than bridging a gap. `POST /api/v1/market/macro/context` assembles
the panel into the Market Engine's inputs and reports the publication state of
every indicator.

## 9. Honest limits (what is deliberately NOT done)

* **No search URLs are configured for any portal.** `search_urls` and `feeds` in
  `config/source_registry.yaml` are empty on purpose: the operator adds the exact
  URLs robots.txt permits (and legal has cleared) before anything crawls.
* **No geocoding provider** is set (`TREI_GEOCODING_PROVIDER`): the seam exists
  (`geocoding_source`, `geocoding_precision`, `geocoded_at`) but a municipality
  centroid is never presented as an exact address.
* **PVP access is unverified**, so it is consumed through a licensed export drop.
* **The repository is still in-memory.** `app/property_master`,
  `app/agent_import` and `app/email_alert` are storage-agnostic and mirror the
  `app/db/schema.sql` vocabulary, so the Postgres/PostGIS swap is a repository
  implementation rather than a rewrite.
* `synthetic_demo` data is flagged on every endpoint and never mixed with real
  observations.
