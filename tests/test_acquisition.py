"""Offline tests for the acquisition layer (router, contract, store, env loader)."""

from __future__ import annotations

import os

from app.acquisition import Observation, ObservationStore, route_acquisition
from app.core.config import Settings
from app.core.envfile import load_env_file
from app.fetchers.registry import SourceConfig


def _source(**overrides) -> SourceConfig:
    base = dict(
        name="src",
        domain="example.org",
        kind="statistics",
        tier="tier3_open_data",
        status="OK",
        recommended_mode="official datasets",
    )
    base.update(overrides)
    return SourceConfig(**base)


def _pilot() -> Settings:
    return Settings(kernel_api_key="x", acquisition_mode="pilot")


# --- router ------------------------------------------------------------------

def test_route_prefers_licensed_feed():
    decision = route_acquisition(_source(tier="tier1_api"), _pilot())
    assert decision.mode == "feed"


def test_route_public_api():
    decision = route_acquisition(_source(api_public=True), _pilot())
    assert decision.mode == "api"


def test_route_direct_files_use_http():
    decision = route_acquisition(_source(extra={"acquisition": {"direct_files": ["https://e/f.csv"]}}), _pilot())
    assert decision.mode == "http"


def test_route_browser_requires_pilot_mode():
    src = _source(extra={"acquisition": {"requires_browser": True, "urls": ["https://e/"]}})
    assert route_acquisition(src, _pilot()).mode == "browser"
    off = Settings(kernel_api_key="x", acquisition_mode="off")
    assert route_acquisition(src, off).mode == "blocked"


def test_route_restricted_is_blocked():
    src = _source(status="license_required")
    assert route_acquisition(src, _pilot()).mode == "blocked"


def test_http_failure_does_not_escalate_to_browser():
    # A source with no "requires_browser" flag never routes to the browser even
    # though pilot mode is on: browser is a method choice, not a retry fallback.
    src = _source(tier="tier4_scraping", scraping_allowed=False)
    assert route_acquisition(src, _pilot()).mode == "manual"


# --- contract ----------------------------------------------------------------

def test_observation_validation():
    obs = Observation(source="s", period="2024", indicator="i", territory_code="ITH20", value=1.0)
    assert obs.validate() == []


def test_observation_observed_requires_value():
    obs = Observation(source="s", period="2024", indicator="i", territory_code="ITH20", value=None)
    assert "OBSERVED requires" in obs.validate()[0]


def test_observation_key_separates_series_and_level():
    a = Observation(source="s", indicator="i", territory_code="t", period="2024", series="ISPAT_NOTARIAL", geo_level="province")
    b = Observation(source="s", indicator="i", territory_code="t", period="2024", series="OMI_NTN", geo_level="province")
    c = Observation(source="s", indicator="i", territory_code="t", period="2024", series="ISPAT_NOTARIAL", geo_level="comune")
    assert a.key() != b.key()
    assert a.key() != c.key()


# --- store -------------------------------------------------------------------

def test_store_idempotent_and_revision_preserving(tmp_path):
    store = ObservationStore(tmp_path / "obs.jsonl")
    obs = Observation(
        source="ispat_provincia_tn",
        period="2024",
        indicator="households",
        territory_code="ITH20",
        value=240000.0,
        unit="count",
        content_hash="abc",
        run_id="r1",
    )
    first = store.upsert(obs)
    assert first["new"] is True and first["revision"] == 1
    same = store.upsert(obs)
    assert same["stored"] is False  # identical content -> no-op
    obs.value = 241000.0
    obs.content_hash = "def"
    revised = store.upsert(obs)
    assert revised["stored"] is True and revised["revised"] is True and revised["revision"] == 2
    assert len(store.all()) == 2  # history preserved, not overwritten


def test_store_rejects_synthetic(tmp_path):
    store = ObservationStore(tmp_path / "obs.jsonl")
    result = store.upsert({"source": "synthetic_demo", "synthetic_demo": True, "indicator": "x"})
    assert result["rejected"] is True
    assert store.all() == []


# --- env loader --------------------------------------------------------------

def test_envfile_loads_only_allowed_keys_and_does_not_override(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "KERNEL_API_KEY=secret-from-file\nTREI_TARGET_START_YEAR=2020\nOTHER_SECRET=nope\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TREI_TARGET_START_YEAR", "2015")  # existing value wins
    monkeypatch.delenv("KERNEL_API_KEY", raising=False)
    load_env_file(path)
    assert os.environ["KERNEL_API_KEY"] == "secret-from-file"
    assert os.environ["TREI_TARGET_START_YEAR"] == "2015"  # not overridden
    assert "OTHER_SECRET" not in os.environ
