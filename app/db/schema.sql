-- Trentino Real Estate Intelligence - core schema (PostgreSQL + PostGIS)
-- Version: 1.0.0  (see app/core/versions.py SCHEMA_VERSION)
--
-- Design notes:
--   * Raw snapshots live in object storage (MinIO/S3); only their metadata and
--     content_hash are stored here (section 33).
--   * price_measure_type and transaction_series are explicit columns, never
--     inferred (sections 10/12).
--   * Every analytical table carries a version + data-cutoff column so lineage
--     is auditable (section 34).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Geographic hierarchy: Province -> Comunita di Valle -> Comune -> microzona
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo_area (
    geo_area_id     BIGSERIAL PRIMARY KEY,
    level           TEXT NOT NULL CHECK (level IN
                        ('province','comunita','comune','microzona','omi_zone')),
    code            TEXT UNIQUE,             -- ISTAT code / OMI zone code
    name            TEXT NOT NULL,
    parent_id       BIGINT REFERENCES geo_area(geo_area_id),
    geom            geometry(MultiPolygon, 4326),
    centroid        geometry(Point, 4326),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_geo_area_geom ON geo_area USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_geo_area_parent ON geo_area (parent_id, level);

-- ---------------------------------------------------------------------------
-- Raw objects (snapshots in object storage)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_object (
    raw_object_id   BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    source_url      TEXT,
    s3_key          TEXT NOT NULL,
    content_type    TEXT,
    content_hash    TEXT NOT NULL,
    byte_size       BIGINT,
    parser_version  TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    retrieved_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (content_hash, parser_version)
);
CREATE INDEX IF NOT EXISTS idx_raw_object_hash ON raw_object (content_hash);

-- ---------------------------------------------------------------------------
-- Property master (a physical unit) and its source listings
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_master (
    property_master_id BIGSERIAL PRIMARY KEY,
    commune_id      BIGINT REFERENCES geo_area(geo_area_id),
    microzona_id    BIGINT REFERENCES geo_area(geo_area_id),
    omi_zone_code   TEXT,
    geohash         TEXT,
    geom            geometry(Point, 4326),
    typology        TEXT,                    -- appartamento, villa, rustico...
    surface_m2      NUMERIC,
    rooms           SMALLINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_property_master_geom ON property_master USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_property_master_comune ON property_master (commune_id);

CREATE TABLE IF NOT EXISTS listing_source (
    listing_source_id BIGSERIAL PRIMARY KEY,
    source            TEXT NOT NULL,
    source_listing_id TEXT NOT NULL,
    property_master_id BIGINT REFERENCES property_master(property_master_id),
    url               TEXT,
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    raw_object_id     BIGINT REFERENCES raw_object(raw_object_id),
    UNIQUE (source, source_listing_id)
);
CREATE INDEX IF NOT EXISTS idx_listing_source_master ON listing_source (property_master_id);

CREATE TABLE IF NOT EXISTS listing_episode (
    listing_episode_id BIGSERIAL PRIMARY KEY,
    property_master_id BIGINT NOT NULL REFERENCES property_master(property_master_id),
    started_at         TIMESTAMPTZ NOT NULL,
    ended_at           TIMESTAMPTZ,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    reason_ended       TEXT                      -- sold_confirmed | removed | unknown
);
CREATE INDEX IF NOT EXISTS idx_listing_episode_master ON listing_episode (property_master_id);

-- ---------------------------------------------------------------------------
-- Price timeline (asking vs OMI vs transacted never mixed), section 10/18
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_observation (
    price_observation_id BIGSERIAL PRIMARY KEY,
    listing_source_id   BIGINT REFERENCES listing_source(listing_source_id),
    property_master_id  BIGINT REFERENCES property_master(property_master_id),
    measure_type        TEXT NOT NULL,     -- asking | omi_min | omi_max | auction_base | ...
    price               NUMERIC,
    price_m2            NUMERIC,
    currency            TEXT NOT NULL DEFAULT 'EUR',
    observed_at         TIMESTAMPTZ NOT NULL,
    is_asking           BOOLEAN NOT NULL GENERATED ALWAYS AS (measure_type = 'asking') STORED,
    kind                TEXT NOT NULL,     -- publisher_asking | portal_asking | omi_quote
    source              TEXT,
    raw_object_id       BIGINT REFERENCES raw_object(raw_object_id)
);
CREATE INDEX IF NOT EXISTS idx_price_obs_master_time
    ON price_observation (property_master_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_price_obs_measure ON price_observation (measure_type);

-- ---------------------------------------------------------------------------
-- Transaction series (kept separately by source), section 12
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_series_point (
    id                 BIGSERIAL PRIMARY KEY,
    series             TEXT NOT NULL,       -- OMI_NTN | FIMAA_AE | ISPAT_NOTARIAL
    geo_area_id        BIGINT REFERENCES geo_area(geo_area_id),
    period             TEXT NOT NULL,       -- 2024 | 2024_Q1 | 2024_YTD | ...
    period_status      TEXT NOT NULL,       -- observed | estimated | annualized
    value              NUMERIC,
    unit               TEXT,                -- count | per_100_dwellings | eur_m2
    data_cutoff        TEXT,
    source             TEXT,
    UNIQUE (series, geo_area_id, period, unit)
);
CREATE INDEX IF NOT EXISTS idx_txn_series ON transaction_series_point (series, period);

-- ---------------------------------------------------------------------------
-- OMI quotations (specific period semantics: 1st/2nd semester), section 8
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS omi_quotation (
    id              BIGSERIAL PRIMARY KEY,
    omi_zone_code   TEXT NOT NULL,
    comune_istat    TEXT,
    period          TEXT NOT NULL,          -- e.g. 2025_S1
    tipologia       TEXT NOT NULL,
    stato           TEXT,
    min_price_m2    NUMERIC,
    max_price_m2    NUMERIC,
    source          TEXT NOT NULL DEFAULT 'OMI',
    retrieved_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Auctions (own model), sections 29-31
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auction_lot (
    master_auction_id  TEXT PRIMARY KEY,
    pvp_id             TEXT,                 -- source of truth when present
    tribunal           TEXT,
    procedure_number   TEXT,
    lot_number         TEXT,
    address            TEXT,
    comune_istat       TEXT,
    cadastral_ref      TEXT,
    auction_date       DATE,
    base_price         NUMERIC,
    minimum_offer      NUMERIC,
    appraisal_value    NUMERIC,
    occupancy_status   TEXT,
    has_surface_right  BOOLEAN,
    has_building_abuse BOOLEAN,
    risk_level         TEXT,
    document_hash      TEXT,
    sources            TEXT[],
    geom               geometry(Point, 4326),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auction_geom ON auction_lot USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_auction_tribunal ON auction_lot (tribunal, procedure_number);

-- ---------------------------------------------------------------------------
-- Macro panel & regime, sections 14-16 / 24
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_indicator (
    id            BIGSERIAL PRIMARY KEY,
    indicator     TEXT NOT NULL,             -- mortgage_rate | cpi | income | employment ...
    geo_area_id   BIGINT REFERENCES geo_area(geo_area_id),
    period        TEXT NOT NULL,
    value         NUMERIC,
    unit          TEXT,
    source        TEXT,
    UNIQUE (indicator, geo_area_id, period)
);

CREATE TABLE IF NOT EXISTS regime_snapshot (
    id             BIGSERIAL PRIMARY KEY,
    geo_area_id    BIGINT REFERENCES geo_area(geo_area_id),
    as_of          DATE NOT NULL,
    regime         TEXT NOT NULL,
    score          NUMERIC,
    confidence     NUMERIC,
    coverage       NUMERIC,
    contributions  JSONB,
    model_version  TEXT NOT NULL,
    data_cutoff    TEXT
);

-- ---------------------------------------------------------------------------
-- Model registry & prediction log (sections 27/36)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_registry (
    model_version   TEXT PRIMARY KEY,
    model_name      TEXT NOT NULL,
    features_version TEXT NOT NULL,
    trained_at      TIMESTAMPTZ,
    data_cutoff     TEXT,
    metrics         JSONB,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS prediction_log (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    property_master_id BIGINT,
    model_version   TEXT NOT NULL,
    data_cutoff     TEXT,
    payload         JSONB NOT NULL
);

