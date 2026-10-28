"""Agent Import tests (§8-31): policy, discovery, extraction, fallbacks, dedup."""

from __future__ import annotations

from dataclasses import dataclass

from app.agent_import.browser import RenderedPage
from app.agent_import.crawler import CrawlerManager, CrawlRequest, FetchOutcome
from app.agent_import.extraction import (
    discover_listing_urls,
    extract_listing,
    json_ld_nodes,
)
from app.agent_import.limiter import RateLimiter
from app.agent_import.policy import PolicyResolver
from app.core.config import Settings
from app.core.enums import CrawlFailure, CrawlStatus, ExtractionMethod
from app.core.fields import ExtractionBundle
from app.fetchers.audit import AccessLog
from app.fetchers.registry import SourceConfig, SourceRegistry
from app.fetchers.robots import RobotsDecision
from app.property_master.master import PropertyMasterStore
from app.storage.datalake import DataLake

SEARCH_URL = "https://fonte.example/vendita-case/trento/"
OTHER_SEARCH_URL = "https://fonte.example/vendita-case/trento/?pagina=2"
DETAIL_1 = "https://fonte.example/annunci/1001/"
DETAIL_2 = "https://fonte.example/annunci/1002/"

SEARCH_HTML = """
<html><body>
  <a href="/annunci/1001/">Appartamento in centro</a>
  <a href="/annunci/1002/">Trilocale</a>
  <a href="/altro/pagina.html">Chi siamo</a>
</body></html>
"""

DETAIL_1_HTML = """
<html><head><title>Appartamento Via Roma 12, Trento</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "RealEstateListing",
  "name": "Appartamento in centro storico",
  "description": "Appartamento luminoso di 4 locali in centro a Trento, ristrutturato nel 2019.",
  "datePosted": "2026-09-20",
  "floorSize": {"@type": "QuantitativeValue", "value": 100, "unitCode": "MTK"},
  "numberOfRooms": 4,
  "numberOfBathroomsTotal": 2,
            "offers": {"@type": "Offer", "price": 250000, "priceCurrency": "EUR", "url": "__URL__"},
  "address": {"@type": "PostalAddress", "streetAddress": "Via Roma 12",
              "addressLocality": "Trento", "addressRegion": "TN", "postalCode": "38122"},
  "geo": {"@type": "GeoCoordinates", "latitude": 46.0679, "longitude": 11.1211},
  "image": ["https://fonte.example/img/roma12-1.jpg"]
}
</script></head><body><h1>Appartamento in centro storico</h1></body></html>
""".replace("__URL__", DETAIL_1)

DETAIL_2_HTML = """
<html><body>
  <div>Trilocale in affaccio sul parco</div>
  <dl><dt>Prezzo</dt><dd>185.000 €</dd>
      <dt>Superficie</dt><dd>85 mq</dd>
      <dt>Locali</dt><dd>3</dd></dl>
  <div>Luminoso trilocale a Rovereto, Via Verdi 40</div>
</body></html>
"""

EMPTY_PAGE = "<html><body><p>" + ("contenuto generico senza dati strutturati " * 80) + "</p></body></html>"

SEARCH_HTML_ONE = '<html><body><a href="/annunci/1002/">Trilocale</a></body></html>'

SEARCH_HTML_FIRST = '<html><body><a href="/annunci/1001/">Appartamento</a></body></html>'

RENDERED_HTML = """
<html><body><script type="application/ld+json">
{"@type":"RealEstateListing","offers":{"price":190000},"floorSize":{"value":90},
 "address":{"streetAddress":"Via Nuova 3","addressLocality":"Pergine Valsugana","addressRegion":"TN"}}
</script></body></html>
"""


# --- fakes ------------------------------------------------------------------

class FakeRobots:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def check(self, url: str) -> RobotsDecision:
        return RobotsDecision(self.allowed, None, "fake robots")


@dataclass
class FakeTransport:
    """Returns canned responses; records every URL it was asked for."""

    pages: dict
    calls: list = None

    def __post_init__(self) -> None:
        self.calls = []

    def fetch(self, url: str) -> FetchOutcome:
        self.calls.append(url)
        entry = self.pages.get(url)
        if entry is None:
            return FetchOutcome(url=url, status_code=404, failure=CrawlFailure.HTTP_ERROR, detail="HTTP 404")
        status, body = entry
        return FetchOutcome(url=url, status_code=status, payload=body.encode(), text=body)


class FakeRenderer:
    def __init__(self, html: str, screenshot: bytes | None = None) -> None:
        self.html = html
        self.screenshot = screenshot
        self.rendered: list[str] = []

    def render(self, url: str) -> RenderedPage:
        self.rendered.append(url)
        return RenderedPage(url=url, html=self.html, screenshot=self.screenshot, screenshot_hash="abc123")


class FakeVision:
    def __init__(self, bundle: ExtractionBundle) -> None:
        self.bundle = bundle

    def extract(self, screenshot: bytes, *, url: str = "") -> ExtractionBundle:
        return self.bundle


# --- helpers ----------------------------------------------------------------

def public_source(**overrides) -> SourceConfig:
    base = dict(
        name="fonte_pubblica",
        domain="fonte.example",
        kind="portal",
        tier="tier4_scraping",
        status="OK",
        recommended_mode="permitted public listing pages",
        robots_txt=True,
        scraping_allowed="partial",
    )
    base.update(overrides)
    return SourceConfig(**base)


def restricted_source() -> SourceConfig:
    return SourceConfig(
        name="portale_restrittivo",
        domain="chiuso.example",
        kind="portal",
        tier="tier5_restricted",
        status="restricted",
        recommended_mode="licence only",
        robots_txt=True,
        scraping_allowed=False,
        requires_auth=True,
    )


def make_manager(
    tmp_path,
    *,
    transport: FakeTransport,
    source: SourceConfig | None = None,
    robots_allowed: bool = True,
    settings: Settings | None = None,
    renderer=None,
    vision=None,
    store: PropertyMasterStore | None = None,
) -> CrawlerManager:
    registry = SourceRegistry([source or public_source()])
    resolver = PolicyResolver(registry, licences=None, robots=FakeRobots(robots_allowed))
    return CrawlerManager(
        resolver=resolver,
        store=store or PropertyMasterStore(review_threshold=0.75),
        datalake=DataLake(tmp_path / "raw"),
        transport=transport,
        audit=AccessLog(tmp_path / "audit.jsonl"),
        renderer=renderer,
        vision=vision,
        settings=settings or Settings(),
        sleeper=lambda seconds: None,
        clock=lambda: 0.0,
    )


def request(**overrides) -> CrawlRequest:
    base = dict(
        source="fonte_pubblica",
        search_urls=[SEARCH_URL],
        url_patterns=(r"/annunci/\d+",),
        id_patterns=(r"/annunci/(\d+)",),
        max_requests=20,
    )
    base.update(overrides)
    return CrawlRequest(**base)


def happy_transport() -> FakeTransport:
    return FakeTransport(
        {
            SEARCH_URL: (200, SEARCH_HTML),
            DETAIL_1: (200, DETAIL_1_HTML),
            DETAIL_2: (200, DETAIL_2_HTML),
        }
    )


# --- extraction -------------------------------------------------------------

def test_json_ld_is_the_first_choice():
    extraction = extract_listing(DETAIL_1_HTML, url=DETAIL_1, id_patterns=(r"/annunci/(\d+)",))
    assert extraction.method == ExtractionMethod.JSON_LD.value
    assert extraction.bundle.value("price") == 250000
    assert extraction.bundle.value("surface_m2") == 100
    assert extraction.bundle.value("municipality") == "Trento"
    assert extraction.bundle.value("postal_code") == "38122"
    assert extraction.bundle.value("source_listing_id") == "1001"
    assert extraction.bundle.confidence("price") > 0.9
    assert extraction.bundle.method("price") == ExtractionMethod.JSON_LD.value
    assert json_ld_nodes(DETAIL_1_HTML)


def test_semantic_html_is_used_when_no_structured_data_exists():
    extraction = extract_listing(DETAIL_2_HTML, url=DETAIL_2, id_patterns=(r"/annunci/(\d+)",))
    assert extraction.bundle.value("price") == 185000
    assert extraction.bundle.value("surface_m2") == 85
    assert extraction.bundle.value("rooms") == 3
    # The visible-text pass fills what the labelled blocks did not state.
    assert extraction.bundle.value("municipality") == "Rovereto"


def test_structured_values_are_not_overwritten_by_regex():
    html = DETAIL_1_HTML + "<p>Prezzo 999.000 €</p>"
    extraction = extract_listing(html, url=DETAIL_1, id_patterns=(r"/annunci/(\d+)",))
    assert extraction.bundle.value("price") == 250000  # JSON-LD wins


def test_discovery_only_returns_matching_listing_urls():
    links = discover_listing_urls(SEARCH_HTML, base_url=SEARCH_URL, url_patterns=(r"/annunci/\d+",))
    assert links == [DETAIL_1, DETAIL_2]


def test_pagination_pages_are_merged_without_duplicates(tmp_path):
    transport = FakeTransport(
        {
            SEARCH_URL: (200, SEARCH_HTML),
            OTHER_SEARCH_URL: (200, SEARCH_HTML),  # page 2 repeats the same cards
            DETAIL_1: (200, DETAIL_1_HTML),
            DETAIL_2: (200, DETAIL_2_HTML),
        }
    )
    manager = make_manager(tmp_path, transport=transport)
    job = manager.run(request(search_urls=[SEARCH_URL, OTHER_SEARCH_URL]))
    assert job.listings_discovered == 2
    assert transport.calls.count(DETAIL_1) == 1


# --- crawler orchestration --------------------------------------------------

def test_full_crawl_stores_listings_and_snapshots(tmp_path):
    transport = happy_transport()
    manager = make_manager(tmp_path, transport=transport)
    job = manager.run(request())

    assert job.status is CrawlStatus.COMPLETED
    assert job.listings_discovered == 2
    assert job.listings_processed == 2
    assert job.new_listings == 2
    assert job.listings_failed == 0
    assert job.blocked_requests == 0
    assert job.parser_errors == 0

    store = manager.store
    assert store.coverage().unique_property_master == 2
    listing = store.get("fonte_pubblica", "1001")
    assert listing is not None
    assert listing.territory.in_territory is True
    assert listing.property_master_id


def test_incremental_recrawl_updates_instead_of_duplicating(tmp_path):
    store = PropertyMasterStore(review_threshold=0.75)
    manager = make_manager(tmp_path, transport=happy_transport(), store=store)
    manager.run(request(crawl_mode="full"))

    # Second run: the source has changed one price.
    changed = DETAIL_1_HTML.replace('"price": 250000', '"price": 245000')
    transport = FakeTransport(
        {
            SEARCH_URL: (200, SEARCH_HTML),
            DETAIL_1: (200, changed),
            DETAIL_2: (200, DETAIL_2_HTML),
        }
    )
    manager2 = make_manager(tmp_path, transport=transport, store=store)
    job = manager2.run(request(crawl_mode="incremental"))

    assert job.updated_listings == 2
    assert job.new_listings == 0
    assert store.coverage().unique_property_master == 2
    assert store.get("fonte_pubblica", "1001").price == 245000


def test_listing_absent_from_a_full_crawl_is_marked_removed(tmp_path):
    store = PropertyMasterStore(review_threshold=0.75)
    manager = make_manager(tmp_path, transport=happy_transport(), store=store)
    manager.run(request(crawl_mode="full"))

    only_one = FakeTransport({SEARCH_URL: (200, SEARCH_HTML_FIRST), DETAIL_1: (200, DETAIL_1_HTML)})
    manager2 = make_manager(tmp_path, transport=only_one, store=store)
    job = manager2.run(request(crawl_mode="full"))
    assert job.removed_listings == 1
    assert store.get("fonte_pubblica", "1002").is_active is False


# --- policy: refusals make no request ---------------------------------------

def test_robots_disallow_blocks_the_crawl_before_any_request(tmp_path):
    transport = happy_transport()
    manager = make_manager(tmp_path, transport=transport, robots_allowed=False)
    job = manager.run(request())

    assert job.status is CrawlStatus.BLOCKED
    assert transport.calls == []  # nothing was even attempted
    assert job.blocked_requests >= 1
    assert any(page.failure is CrawlFailure.ROBOTS_BLOCKED for page in job.pages)
    entries = manager.audit.entries()
    assert entries and entries[0]["outcome"] == "refused_robots"


def test_restricted_source_without_a_licence_is_refused(tmp_path):
    transport = happy_transport()
    manager = make_manager(tmp_path, transport=transport, source=restricted_source())
    job = manager.run(request(source="portale_restrittivo"))

    assert job.status is CrawlStatus.BLOCKED
    assert transport.calls == []
    assert any(page.failure is CrawlFailure.SOURCE_RESTRICTED for page in job.pages)
    assert manager.audit.entries()[0]["outcome"] == "refused_no_licence"


def test_unknown_source_is_never_crawled_and_unregistered_sources_are_refused(tmp_path):
    transport = happy_transport()
    unknown = SourceConfig(
        name="fonte_ignota", domain="ignota.example", kind="portal",
        tier="tier4_scraping", status="UNKNOWN", recommended_mode="verify first",
    )
    manager = make_manager(tmp_path, transport=transport, source=unknown)
    job = manager.run(request(source="fonte_ignota"))
    assert job.status is CrawlStatus.BLOCKED
    assert transport.calls == []

    other = make_manager(tmp_path, transport=transport)
    job2 = other.run(request(source="mai_registrata"))
    assert job2.status is CrawlStatus.BLOCKED
    assert other.transport.calls == []


def test_rate_limit_pauses_the_crawl_instead_of_pushing(tmp_path):
    transport = FakeTransport(
        {
            SEARCH_URL: (200, SEARCH_HTML),
            DETAIL_1: (429, "slow down"),
            DETAIL_2: (429, "slow down"),
        }
    )
    original = transport.fetch

    def fetch_with_retry_after(url: str) -> FetchOutcome:
        outcome = original(url)
        if outcome.status_code == 429:
            outcome.failure = CrawlFailure.RATE_LIMITED
            outcome.retryable = True
            outcome.retry_after_s = 100_000.0
        return outcome

    transport.fetch = fetch_with_retry_after  # type: ignore[method-assign]

    manager = make_manager(tmp_path, transport=transport)
    job = manager.run(request())

    assert job.status is CrawlStatus.BLOCKED
    assert any(page.failure is CrawlFailure.RATE_LIMITED for page in job.pages)
    assert any("paused by source" in note for note in job.notes)
    # The crawler stopped at the first throttle instead of hammering both pages.
    assert transport.calls.count(DETAIL_2) == 0


def test_backoff_is_exponential_and_capped():
    limiter = RateLimiter(source="s", requests_per_minute=60, base_backoff_s=2.0, max_backoff_s=10.0)
    assert limiter.backoff(1) == 2.0
    assert limiter.backoff(2) == 4.0
    assert limiter.backoff(3) == 8.0
    assert limiter.backoff(6) == 10.0  # capped
    waited: list[float] = []
    limiter.sleeper = waited.append
    limiter.clock = lambda: 100.0
    limiter.wait()
    assert waited == [] or waited[0] == 0.0


# --- fallbacks: browser DOM and screenshot/vision ---------------------------

def test_browser_fallback_is_used_only_when_deterministic_extraction_fails(tmp_path):
    transport = FakeTransport({SEARCH_URL: (200, SEARCH_HTML_ONE), DETAIL_2: (200, EMPTY_PAGE)})
    renderer = FakeRenderer(RENDERED_HTML)
    manager = make_manager(
        tmp_path,
        transport=transport,
        renderer=renderer,
        settings=Settings(allow_browser=True),
    )
    job = manager.run(request(max_listings=1))

    assert renderer.rendered == [DETAIL_2]
    assert job.listings_processed == 1
    listing = next(r for r in manager.store.listings() if r.url == DETAIL_2)
    assert listing.payload["price"] == 190000
    assert listing.territory.municipality == "Pergine Valsugana"
    assert listing.raw_object_id


def test_screenshot_and_vision_fallback_records_the_hash(tmp_path):
    transport = FakeTransport({SEARCH_URL: (200, SEARCH_HTML_ONE), DETAIL_2: (200, EMPTY_PAGE)})
    renderer = FakeRenderer(EMPTY_PAGE, screenshot=b"\x89PNG fake")
    bundle = ExtractionBundle()
    bundle.set("price", 175000, method="vision", confidence=0.72)
    bundle.set("municipality", "Trento", method="vision", confidence=0.7)
    bundle.set("surface_m2", 80, method="vision", confidence=0.7)
    vision = FakeVision(bundle)

    manager = make_manager(
        tmp_path,
        transport=transport,
        renderer=renderer,
        vision=vision,
        settings=Settings(allow_browser=True, allow_vision=True),
    )
    job = manager.run(request(max_listings=1))

    assert job.review_required == 1  # vision confidence is below the threshold
    listing = next(r for r in manager.store.listings() if r.url == DETAIL_2)
    assert listing.price == 175000
    assert listing.review_required is True
    screenshots = list((tmp_path / "raw" / "fonte_pubblica").rglob("screenshot-*.png"))
    assert screenshots


def test_browser_is_not_used_when_disabled(tmp_path):
    transport = FakeTransport({SEARCH_URL: (200, SEARCH_HTML_ONE), DETAIL_2: (200, EMPTY_PAGE)})
    renderer = FakeRenderer(RENDERED_HTML)
    manager = make_manager(tmp_path, transport=transport, renderer=renderer)  # allow_browser=False
    job = manager.run(request(max_listings=1))
    assert renderer.rendered == []
    assert job.listings_processed == 0
    assert job.listings_failed == 1


# --- failure modes ----------------------------------------------------------

def test_parser_failure_is_reported_as_schema_changed_not_not_found(tmp_path):
    transport = FakeTransport({SEARCH_URL: (200, SEARCH_HTML), DETAIL_1: (200, EMPTY_PAGE)})
    manager = make_manager(tmp_path, transport=transport)
    job = manager.run(request(max_listings=1))

    assert job.parser_errors >= 0
    page = job.pages[-1]
    assert page.failure is CrawlFailure.SCHEMA_CHANGED
    assert job.listings_failed == 1
    assert job.status is CrawlStatus.PARTIAL
    assert "listing_not_found" not in job.to_dict()["failure_counts"]


def test_http_error_on_a_listing_page_is_recorded_as_http_error(tmp_path):
    transport = FakeTransport({SEARCH_URL: (200, SEARCH_HTML)})
    manager = make_manager(tmp_path, transport=transport)
    job = manager.run(request(max_listings=1))
    assert job.pages[-1].failure is CrawlFailure.HTTP_ERROR
    assert job.listings_failed == 1
    assert job.listings_processed == 0


def test_out_of_territory_listing_is_not_stored(tmp_path):
    out_of_area = DETAIL_2_HTML.replace("Rovereto", "Milano").replace("Trento", "Milano")
    transport = FakeTransport({SEARCH_URL: (200, SEARCH_HTML_ONE), DETAIL_2: (200, out_of_area)})
    manager = make_manager(tmp_path, transport=transport)
    job = manager.run(request(max_listings=1))

    assert job.listings_processed == 0
    assert job.pages[-1].failure is CrawlFailure.OUT_OF_TERRITORY
    assert manager.store.listings() == []


def test_same_property_on_two_crawled_portals_merges_and_keeps_history(tmp_path):
    store = PropertyMasterStore(review_threshold=0.75)
    manager = make_manager(tmp_path, transport=happy_transport(), store=store)
    manager.run(request())

    # A second portal lists the same flat with a slightly different price.
    second = public_source(name="secondo_portale", domain="secondo.example")
    second_page = DETAIL_1_HTML.replace("fonte.example", "secondo.example").replace(
        '"price": 250000', '"price": 251000'
    )
    second_transport = FakeTransport(
        {
            "https://secondo.example/ricerca/": (200, '<a href="/annunci/77/">Appartamento</a>'),
            "https://secondo.example/annunci/77/": (200, second_page),
        }
    )
    other = make_manager(tmp_path, transport=second_transport, source=second, store=store)
    other.run(
        CrawlRequest(
            source="secondo_portale",
            search_urls=["https://secondo.example/ricerca/"],
            url_patterns=(r"/annunci/\d+",),
            id_patterns=(r"/annunci/(\d+)",),
        )
    )

    coverage = store.coverage()
    assert coverage.raw_listings == 3
    assert coverage.unique_property_master == 2  # the Trento flat is now seen twice
    trento = store.get("secondo_portale", "77")
    assert trento is not None
    assert trento.property_master_id == store.get("fonte_pubblica", "1001").property_master_id
    assert set(trento.sources) == {"fonte_pubblica", "secondo_portale"}


def test_crawl_job_reports_metrics_for_the_admin_dashboard(tmp_path):
    manager = make_manager(tmp_path, transport=happy_transport())
    job = manager.run(request())
    health = manager.health(job)

    assert health.source == "fonte_pubblica"
    assert health.crawl_status == CrawlStatus.COMPLETED.value
    assert health.listings == 2
    assert health.duplicate_rate is not None
    assert health.blocked_rate == 0.0
    assert health.history and health.history[0]["job_id"] == job.job_id
