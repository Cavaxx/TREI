"""Offline tests for the real-data API (stores pointed at tmp fixtures)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.realdata.LISTINGS_PATH", tmp_path / "listings.jsonl")
    monkeypatch.setattr("app.api.realdata.OBSERVATIONS_PATH", tmp_path / "observations.jsonl")
    return TestClient(app)


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_listings_filter_and_price_m2(client, tmp_path):
    _write(tmp_path / "listings.jsonl", [
        {"key": "s:1", "source": "s", "native_id": "1", "comune": "Arco", "price": 200000, "surface_m2": 100, "typology": "appartamenti", "observed_at": "2026-10-29T00:00:00+00:00", "url": "https://x/1"},
        {"key": "s:2", "source": "s", "native_id": "2", "comune": "Trento", "price": 100000, "surface_m2": None, "typology": "appartamenti", "observed_at": "2026-10-29T00:00:00+00:00", "url": "https://x/2"},
    ])
    r = client.get("/api/v1/real/listings", params={"comune": "Arco"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["price_m2"] == 2000.0
    assert body["demo_data"] is False

    r = client.get("/api/v1/real/listings", params={"surface_min": "50"})
    assert r.json()["count"] == 1  # only the listing with surface


def test_statistics_excludes_superseded(client, tmp_path):
    _write(tmp_path / "observations.jsonl", [
        {"key": "k1", "indicator": "households", "period": "2015", "value": 100.0, "definition": "d", "definition_version": "I.26", "unit": "count", "source": "s", "series": "S", "geo_level": "province", "territory_code": "ITH20"},
        {"key": "k2", "indicator": "mutui_residenziali", "period": "2015", "value": 1.0, "definition": "d", "definition_version": "V.07 (Fonte: Istat)", "unit": "count", "source": "s", "series": "S", "geo_level": "province", "territory_code": "ITH20", "superseded": True},
    ])
    r = client.get("/api/v1/real/statistics")
    body = r.json()
    assert body["count"] == 1
    assert [i["indicator"] for i in body["indicators"]] == ["households"]


def test_export_csv_escapes_formula(client, tmp_path):
    _write(tmp_path / "listings.jsonl", [
        {"key": "s:1", "source": "s", "native_id": "1", "comune": "Arco", "title": "=SUM(A1)", "price": 100, "surface_m2": 50, "typology": "a", "observed_at": "2026-10-29T00:00:00+00:00", "url": "https://x/1"},
    ])
    r = client.get("/api/v1/real/export", params={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "'=SUM(A1)" in r.text  # formula injection neutralized


def test_listing_detail_and_history(client, tmp_path):
    _write(tmp_path / "listings.jsonl", [
        {"key": "s:1", "source": "s", "native_id": "1", "comune": "Arco", "price": 200000, "surface_m2": 100, "observed_at": "2026-10-28T00:00:00+00:00", "url": "https://x/1"},
        {"key": "s:1", "source": "s", "native_id": "1", "comune": "Arco", "price": 190000, "surface_m2": 100, "observed_at": "2026-10-29T00:00:00+00:00", "url": "https://x/1"},
    ])
    r = client.get("/api/v1/real/listings/1")
    assert r.status_code == 200
    body = r.json()
    assert body["snapshots"] == 2
    assert [h["price"] for h in body["history"]] == [200000, 190000]
