"""Runtime configuration.

Reads from environment variables (12-factor). Falls back to sane local
defaults so the package can be imported and tested without a full stack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "config" / "source_registry.yaml"
DEFAULT_LICENCE_PATH = PROJECT_ROOT / "config" / "source_licences.yaml"
DEFAULT_AUDIT_LOG_PATH = PROJECT_ROOT / "var" / "audit" / "access.jsonl"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Comma-separated list with no empty entries (used for allowed domains)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    """Application settings. Frozen so it can be safely shared."""

    postgres_dsn: str = field(
        default_factory=lambda: os.environ.get(
            "TREI_POSTGRES_DSN",
            "postgresql+psycopg://trei:trei@postgres:5432/trei",
        )
    )
    redis_url: str = field(
        default_factory=lambda: os.environ.get("TREI_REDIS_URL", "redis://redis:6379/0")
    )
    s3_endpoint: str = field(
        default_factory=lambda: os.environ.get("TREI_S3_ENDPOINT", "http://minio:9000")
    )
    s3_bucket_raw: str = field(
        default_factory=lambda: os.environ.get("TREI_S3_BUCKET_RAW", "trei-raw")
    )
    s3_access_key: str = field(
        default_factory=lambda: os.environ.get("TREI_S3_ACCESS_KEY", "minioadmin")
    )
    s3_secret_key: str = field(
        default_factory=lambda: os.environ.get("TREI_S3_SECRET_KEY", "minioadmin")
    )

    registry_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TREI_REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH))
        )
    )
    #: Human-authored licence register (authorized feeds). See app/fetchers/licence.py
    licence_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TREI_LICENCE_REGISTRY", str(DEFAULT_LICENCE_PATH))
        )
    )
    #: Append-only evidence log of every access attempt (compliance due diligence)
    audit_log_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TREI_AUDIT_LOG", str(DEFAULT_AUDIT_LOG_PATH))
        )
    )
    #: Honour robots.txt even for contract-named endpoints unless the licence
    #: record explicitly sets robots_applies: false.
    feed_respect_robots: bool = field(
        default_factory=lambda: _env_bool("TREI_FEED_RESPECT_ROBOTS", True)
    )

    # Data lake ---------------------------------------------------------------
    #: Root of the raw-object tree (§45). Layout is
    #: <root>/<source>/<YYYY-MM-DD>/<crawl|crawl_id>/<response|html|json|screenshot>
    datalake_root: Path = field(
        default_factory=lambda: Path(os.environ.get("TREI_DATALAKE_ROOT", str(PROJECT_ROOT / "data" / "raw")))
    )

    # Email alerts (Channel C, §4-7) -----------------------------------------
    #: Folder of .eml files used when no IMAP account is configured. This is the
    #: "user legitimately provided the content" route and works offline.
    email_drop_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("TREI_EMAIL_DROP_DIR", str(PROJECT_ROOT / "data" / "email" / "inbox")))
    )
    imap_host: str = field(default_factory=lambda: os.environ.get("TREI_IMAP_HOST", ""))
    imap_port: int = field(default_factory=lambda: int(os.environ.get("TREI_IMAP_PORT", "993")))
    imap_user: str = field(default_factory=lambda: os.environ.get("TREI_IMAP_USER", ""))
    imap_folder: str = field(default_factory=lambda: os.environ.get("TREI_IMAP_FOLDER", "INBOX"))
    #: OAuth2 access token (preferred). The password is never stored by TREI.
    imap_oauth_token: str = field(default_factory=lambda: os.environ.get("TREI_IMAP_OAUTH_TOKEN", ""))
    #: IMAP password, injected at runtime only, and never persisted by TREI (§5).
    imap_password: str = field(default_factory=lambda: os.environ.get("TREI_IMAP_PASSWORD", ""))

    # Agent Import (Channel D, §8-31) ----------------------------------------
    #: Fields whose extraction confidence falls below this are queued for review.
    review_confidence_threshold: float = field(
        default_factory=lambda: float(os.environ.get("TREI_REVIEW_CONFIDENCE", "0.75"))
    )
    #: Hard ceiling on requests per crawl job, whatever the source config says.
    crawl_max_requests: int = field(
        default_factory=lambda: int(os.environ.get("TREI_CRAWL_MAX_REQUESTS", "500"))
    )
    #: Wall-clock ceiling on a single crawl job (seconds). Budget deferral is
    #: recorded in the run ledger so the queue resumes instead of restarting.
    crawl_max_seconds: float = field(
        default_factory=lambda: float(os.environ.get("TREI_CRAWL_MAX_SECONDS", "1800"))
    )
    #: Incremental crawl interval (minutes) per source when not overridden.
    crawl_incremental_minutes: int = field(
        default_factory=lambda: int(os.environ.get("TREI_CRAWL_INCREMENTAL_MIN", "180"))
    )
    #: Full re-discovery interval (hours) per source when not overridden.
    crawl_full_hours: int = field(
        default_factory=lambda: int(os.environ.get("TREI_CRAWL_FULL_HOURS", "168"))
    )
    #: Rendering/vision fallbacks are OFF unless explicitly enabled AND the
    #: source registry allows the corresponding access mode.
    allow_browser: bool = field(default_factory=lambda: _env_bool("TREI_ALLOW_BROWSER", False))
    allow_vision: bool = field(default_factory=lambda: _env_bool("TREI_ALLOW_VISION", False))

    # Geocoding (§19) ---------------------------------------------------------
    #: Provider is deliberately unconfigured until one is legally confirmed.
    geocoding_provider: str = field(default_factory=lambda: os.environ.get("TREI_GEOCODING_PROVIDER", ""))

    # Ingestion policy -------------------------------------------------------
    # A descriptive User-Agent is mandatory: sources are more likely to allow a
    # well-identified crawler, and it is the honest thing to do.
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "TREI_USER_AGENT",
            "TREI-bot/0.1 (+https://example.org/trentino-rei; contact: data@example.org)",
        )
    )
    respect_robots: bool = field(default_factory=lambda: _env_bool("TREI_RESPECT_ROBOTS", True))
    request_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("TREI_REQUEST_TIMEOUT", "20"))
    )
    # Global per-domain politeness delay applied by the fetcher base class.
    default_min_delay_s: float = field(
        default_factory=lambda: float(os.environ.get("TREI_MIN_DELAY", "2.0"))
    )

    # Data quality -----------------------------------------------------------
    # Plausible €/m² band for the province; used only to *flag* outliers, never
    # to silently drop rows (section 48).
    price_m2_plausible_min: float = 200.0
    price_m2_plausible_max: float = 20000.0

    # Kernel browser provider (§browser) ---------------------------------------
    #: The single credential Kernel needs. Missing => browser jobs fail with
    #: ``configuration_missing`` before any session is opened. Never logged.
    kernel_api_key: str = field(default_factory=lambda: os.environ.get("KERNEL_API_KEY", ""))
    #: Which provider handles jobs that need a browser. Only "kernel" for now.
    browser_provider: str = field(default_factory=lambda: os.environ.get("TREI_BROWSER_PROVIDER", "kernel"))
    #: Acquisition mode: "pilot" runs are explicit and bounded; anything else
    #: keeps live acquisition off by default.
    acquisition_mode: str = field(default_factory=lambda: os.environ.get("TREI_ACQUISITION_MODE", "pilot"))
    #: Requested analysis window. This is a *target*, never a coverage guarantee.
    target_start_year: int = field(default_factory=lambda: _env_int("TREI_TARGET_START_YEAR", 2015))
    target_end_year: int = field(default_factory=lambda: _env_int("TREI_TARGET_END_YEAR", 2026))

    #: Concurrent browsers for the whole run (run-wide, not per worker).
    kernel_max_concurrency: int = field(default_factory=lambda: _env_int("TREI_KERNEL_MAX_CONCURRENCY", 1))
    #: Session/creation attempts allowed per run, retries included.
    kernel_max_sessions_per_run: int = field(default_factory=lambda: _env_int("TREI_KERNEL_MAX_SESSIONS_PER_RUN", 5))
    #: Application-level wall-clock deadline for a single session (seconds).
    kernel_max_session_wall_seconds: int = field(
        default_factory=lambda: _env_int("TREI_KERNEL_MAX_SESSION_WALL_SECONDS", 180)
    )
    #: Cumulative browser budget shared by the whole run (seconds).
    kernel_max_total_browser_seconds: int = field(
        default_factory=lambda: _env_int("TREI_KERNEL_MAX_TOTAL_BROWSER_SECONDS", 900)
    )
    #: Value passed to the provider's standby timeout (NOT the job duration).
    kernel_standby_timeout_seconds: int = field(
        default_factory=lambda: _env_int("TREI_KERNEL_STANDBY_TIMEOUT_SECONDS", 60)
    )
    browser_navigation_timeout_ms: int = field(
        default_factory=lambda: _env_int("TREI_BROWSER_NAVIGATION_TIMEOUT_MS", 30_000)
    )
    browser_action_timeout_ms: int = field(
        default_factory=lambda: _env_int("TREI_BROWSER_ACTION_TIMEOUT_MS", 15_000)
    )
    browser_max_pages_per_source: int = field(
        default_factory=lambda: _env_int("TREI_BROWSER_MAX_PAGES_PER_SOURCE", 5)
    )
    browser_max_records_per_source: int = field(
        default_factory=lambda: _env_int("TREI_BROWSER_MAX_RECORDS_PER_SOURCE", 100)
    )
    browser_min_navigation_interval_seconds: float = field(
        default_factory=lambda: _env_float("TREI_BROWSER_MIN_NAVIGATION_INTERVAL_SECONDS", 3.0)
    )
    browser_max_retries: int = field(default_factory=lambda: _env_int("TREI_BROWSER_MAX_RETRIES", 2))
    browser_retry_backoff_seconds: float = field(
        default_factory=lambda: _env_float("TREI_BROWSER_RETRY_BACKOFF_SECONDS", 5.0)
    )
    browser_max_download_mb: int = field(default_factory=lambda: _env_int("TREI_BROWSER_MAX_DOWNLOAD_MB", 25))
    #: Hosts the pilot may visit (in addition to the source registry). A URL on
    #: this list is a *technical* allow, not an authorisation to collect.
    browser_allowed_domains: tuple[str, ...] = field(
        default_factory=lambda: _env_csv("TREI_BROWSER_ALLOWED_DOMAINS", ("statweb.provincia.tn.it",))
    )

    api_title: str = "Trentino Real Estate Intelligence API"
    api_version: str = "v1"
    allow_live_fetch: bool = field(default_factory=lambda: _env_bool("TREI_ALLOW_LIVE_FETCH", False))


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
