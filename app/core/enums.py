"""Controlled vocabularies used across the platform.

Keeping these as enums (rather than free strings) is what prevents the class of
error the specification warns about repeatedly: mixing asking prices with
transaction prices, or calling an unconfirmed disappearance a "sale".
"""

from __future__ import annotations

from enum import Enum


class PriceMeasureType(str, Enum):
    """How a price number was produced (section 10)."""

    ASKING = "asking"
    OMI_MIN = "omi_min"
    OMI_MAX = "omi_max"
    TRANSACTION_OBSERVED = "transaction_observed"
    TRANSACTION_ESTIMATED = "transaction_estimated"
    AUCTION_BASE = "auction_base"
    AUCTION_MINIMUM_OFFER = "auction_minimum_offer"
    APPRAISAL_VALUE = "appraisal_value"


class TransactionSeries(str, Enum):
    """Distinct, non-mixable transaction series (section 12)."""

    OMI_NTN = "OMI_NTN"
    FIMAA_AE = "FIMAA_AE"
    ISPAT_NOTARIAL = "ISPAT_NOTARIAL"


class ListingEvent(str, Enum):
    """Lifecycle events in a listing timeline (section 18/20)."""

    LISTING_CREATED = "listing_created"
    PRICE_CHANGED = "price_changed"
    LISTING_UNDER_OFFER = "listing_under_offer"
    LISTING_REMOVED = "listing_removed"
    LISTING_RELISTED = "listing_relisted"
    LISTING_SOLD_CONFIRMED = "listing_sold_confirmed"


class TimeOnMarketKind(str, Enum):
    """We must never conflate these three (section 17)."""

    OBSERVED_TIME_ON_MARKET = "observed_time_on_market"
    ESTIMATED_TIME_TO_SALE = "estimated_time_to_sale"
    SURVEY_BASED_TIME_TO_SALE = "survey_based_time_to_sale"


class MarketRegime(str, Enum):
    """Regime states (section 25)."""

    EXPANSION = "EXPANSION"
    STABLE = "STABLE"
    CONTRACTION = "CONTRACTION"
    CRISIS = "CRISIS"


class ValuationClass(str, Enum):
    """Listing vs. estimated market value (section 23)."""

    UNDER_MARKET = "UNDER_MARKET"
    IN_LINE = "IN_LINE"
    OVER_MARKET = "OVER_MARKET"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Evidence(str, Enum):
    """Used instead of inventing a probability when data is too thin."""

    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class SourceTier(str, Enum):
    """Acquisition tiers (section 4)."""

    TIER1_API = "tier1_api"
    TIER2_FEED = "tier2_feed"
    TIER3_OPEN_DATA = "tier3_open_data"
    TIER4_SCRAPING = "tier4_scraping"
    TIER5_RESTRICTED = "tier5_restricted"


class SourceStatus(str, Enum):
    """Adapter health states (sections 46/47)."""

    OK = "OK"
    WARNING = "WARNING"
    DEGRADED = "DEGRADED"
    LICENSE_REQUIRED = "LICENSE_REQUIRED"
    RESTRICTED = "restricted"
    UNKNOWN = "UNKNOWN"
    #: Source consumed through a channel covered by a signed licence.
    LICENSED = "LICENSED"


class Segment(str, Enum):
    """Market segments that must not be pooled (section 49)."""

    URBAN_RESIDENTIAL = "urban_residential"
    SUBURBAN_RESIDENTIAL = "suburban_residential"
    MOUNTAIN_TOURISM = "mountain_tourism"
    LAKE_TOURISM = "lake_tourism"
    SECOND_HOME = "second_home"
    INVESTMENT = "investment"
    NEW_CONSTRUCTION = "new_construction"
    AUCTION = "auction"


class AccessMode(str, Enum):
    """How a source may be acquired (Phase-2 section 2).

    The Agent Import consults the registry BEFORE touching a source; the mode is
    the registry's recommendation, and only ``api``/``feed``/``rss``/``email``/
    ``browser_public`` can ever be automated. ``restricted``/``disabled``/``unknown``
    are never automated, and ``manual`` means a human supplies the content.
    """

    API = "api"
    FEED = "feed"
    RSS = "rss"
    BROWSER_PUBLIC = "browser_public"
    EMAIL = "email"
    MANUAL = "manual"
    RESTRICTED = "restricted"
    DISABLED = "disabled"
    UNKNOWN = "unknown"

    @property
    def automatable(self) -> bool:
        """True only for modes the platform may exercise without a human."""
        return self in {
            AccessMode.API,
            AccessMode.FEED,
            AccessMode.RSS,
            AccessMode.BROWSER_PUBLIC,
            AccessMode.EMAIL,
        }


class CrawlFailure(str, Enum):
    """Crawler failure modes (section 25). Never collapse these into
    ``listing_not_found``: a blocked page is not an absent property."""

    HTTP_ERROR = "HTTP_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    ROBOTS_BLOCKED = "ROBOTS_BLOCKED"
    SOURCE_RESTRICTED = "SOURCE_RESTRICTED"
    PARSER_ERROR = "PARSER_ERROR"
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    TIMEOUT = "TIMEOUT"
    INVALID_LISTING = "INVALID_LISTING"
    OUT_OF_TERRITORY = "OUT_OF_TERRITORY"
    DUPLICATE = "DUPLICATE"


class CrawlStatus(str, Enum):
    """Lifecycle state of a crawl job (section 24)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class DataStatus(str, Enum):
    """Whether an observation actually exists (section 36).

    ``NOT_YET_PUBLISHED`` is a first-class state: a period whose official figure
    has not been released is NOT a zero and is NEVER interpolated as observed.
    """

    OBSERVED = "OBSERVED"
    ESTIMATED = "ESTIMATED"
    NOT_YET_PUBLISHED = "NOT_YET_PUBLISHED"
    UNAVAILABLE = "UNAVAILABLE"
    #: The source has no figure for this period/territory at all (e.g. the ISPAT
    #: table stops at 2024): distinct from "not published yet" and from zero.
    OUT_OF_COVERAGE = "OUT_OF_COVERAGE"


class ExtractionMethod(str, Enum):
    """How a field value was produced, in decreasing order of trust (section 29)."""

    STRUCTURED_FEED = "structured_feed"
    JSON_LD = "json_ld"
    EMBEDDED_JSON = "embedded_json"
    MICRODATA = "microdata"
    SEMANTIC_HTML = "semantic_html"
    METADATA = "metadata"
    REGEX_TEXT = "regex_text"
    EMAIL_BODY = "email_body"
    BROWSER_DOM = "browser_dom"
    VISION = "vision"
    MANUAL = "manual"
    #: Computed from other extracted fields (e.g. price per m2). Never a source fact.
    DERIVED = "derived"


class TerritoryConfidence(str, Enum):
    """Confidence that a listing truly belongs to the target territory (section 17)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class GeocodingPrecision(str, Enum):
    """Never present a municipality centroid as an exact address (section 19)."""

    EXACT_ADDRESS = "exact_address"
    STREET = "street"
    POSTCODE = "postcode"
    MUNICIPALITY_CENTROID = "municipality_centroid"
    NONE = "none"


class ObservationSource(str, Enum):
    """Which channel first observed a property (sections 7 and 32)."""

    EMAIL_ALERT = "email_alert"
    AGENT_IMPORT = "agent_import"
    AUCTION = "auction"
    AGENCY_FEED = "agency_feed"
    LICENSED_FEED = "licensed_feed"
    USER_PROVIDED = "user_provided"
    SYNTHETIC_DEMO = "synthetic_demo"
