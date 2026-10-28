import httpx

from app.fetchers.registry import SourceConfig, SourceRegistry
from app.fetchers.robots import RobotsCache, RobotsDisallowed, _origin


def test_registry_loads_and_reports_restricted_sources():
    reg = SourceRegistry.load()
    names = {s.name for s in reg.all()}
    assert {"immobiliare_it", "idealista_it", "casa_it", "istat", "omi_agenzia_entrate"} <= names

    restricted = {s.name for s in reg.restricted()}
    assert "idealista_it" in restricted       # LICENSE_REQUIRED
    assert "subito_it" in restricted          # restricted
    assert "wikicasa_it" in restricted        # UNKNOWN

    auto = {s.name for s in reg.auto_enabled()}
    assert "istat" in auto
    assert "idealista_it" not in auto


def test_auto_acquisition_policy_logic():
    license_required = SourceConfig(
        name="x", domain="d", kind="portal", tier="tier5_restricted",
        status="license_required", recommended_mode="license",
    )
    assert license_required.auto_acquisition_allowed is False

    open_data = SourceConfig(
        name="y", domain="d", kind="statistics", tier="tier3_open_data",
        status="OK", recommended_mode="api",
    )
    assert open_data.auto_acquisition_allowed is True

    scraping_no_robots = SourceConfig(
        name="z", domain="d", kind="portal", tier="tier4_scraping",
        status="OK", recommended_mode="scrape", robots_txt=False, scraping_allowed=True,
    )
    assert scraping_no_robots.auto_acquisition_allowed is False  # no robots => not allowed


def test_origin_helper():
    assert _origin("https://www.example.com/a/b?x=1") == "https://www.example.com"


def test_robots_404_means_allowed(monkeypatch):
    def fake_get(url, **kwargs):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    cache = RobotsCache("trei-bot/1")
    decision = cache.check("https://example.com/listings")
    assert decision.allowed is True


def test_robots_disallow_all_on_403(monkeypatch):
    def fake_get(url, **kwargs):
        return httpx.Response(403, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    cache = RobotsCache("trei-bot/1")
    decision = cache.check("https://example.com/listings")
    assert decision.allowed is False


def test_robots_parses_rules(monkeypatch):
    body = "User-agent: *\nDisallow: /private/\nAllow: /public/"

    def fake_get(url, **kwargs):
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    cache = RobotsCache("trei-bot/1")
    assert cache.check("https://example.com/public/x").allowed is True
    assert cache.check("https://example.com/private/x").allowed is False
    try:
        cache.ensure("https://example.com/private/x")
        raise AssertionError("should have raised")
    except RobotsDisallowed:
        pass
