"""Home redirect, health states, ordering and convenience tests (tmp fixtures)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.realdata import value_scores


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.realdata.LISTINGS_PATH", tmp_path / "listings.jsonl")
    monkeypatch.setattr("app.api.realdata.OBSERVATIONS_PATH", tmp_path / "observations.jsonl")
    return TestClient(app)


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _rec(i, *, comune="Trento", zone="Centro", typology="appartamenti", price=200000.0,
         surface=100.0, source="s", unit_class="residenziale", offer_kind=""):
    return {"key": f"{source}:{i}", "source": source, "native_id": str(i),
            "comune": comune, "zone": zone, "typology": typology, "price": price,
            "surface_m2": surface, "unit_class": unit_class, "offer_kind": offer_kind,
            "observed_at": "2026-11-02T00:00:00+00:00", "url": f"https://x/{i}"}


def test_home_redirects_to_real(client, tmp_path):
    _write(tmp_path / "listings.jsonl", [_rec(1)])
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/real"
    assert client.get("/demo").status_code == 200


def test_health_missing_dir(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.realdata.LISTINGS_PATH", tmp_path / "nope" / "listings.jsonl")
    body = client.get("/api/v1/real/health").json()
    assert body["status"] == "missing_dir"


def test_health_empty_archive(client, tmp_path):
    _write(tmp_path / "listings.jsonl", [])
    body = client.get("/api/v1/real/health").json()
    assert body["status"] == "empty"


def test_value_scores_known_vantage():
    # listing at 2400 €/m², 10 comparables at 3000 €/m² (same comune/typology,
    # surfaces within the band) -> +20% vantage
    recs = [_rec(0, price=240000.0, surface=100.0)]  # 2400 €/m²
    for i in range(1, 11):
        recs.append(_rec(i, price=300000.0, surface=100.0))  # 3000 €/m²
    scores = value_scores(recs)
    s = scores["s:0"]
    assert s["vantage_pct"] == 20.0
    assert s["median_eur_m2"] == 3000.0
    assert s["comparables"] == 10
    assert s["level"] == "zona"


def test_value_scores_insufficient_sample_is_null_with_reason():
    recs = [_rec(0), _rec(1), _rec(2)]  # only 2 comparables < 10
    scores = value_scores(recs)
    s = scores["s:0"]
    assert s["vantage_pct"] is None
    assert "campione insufficiente" in s["reason"]


def test_value_scores_excludes_offer_kind_and_self():
    recs = [_rec(0), _rec(1, offer_kind="asta")]
    recs += [_rec(i) for i in range(2, 12)]
    scores = value_scores(recs)
    assert scores["s:1"]["vantage_pct"] is None  # asta fuori dal confronto
    assert scores["s:0"]["comparables"] == 10  # l'annuncio non conta se stesso


def test_listings_sort_missing_last_both_directions(client, tmp_path):
    _write(tmp_path / "listings.jsonl", [
        _rec(1, price=100.0), _rec(2, price=300.0), _rec(3, price=None),
    ])
    for direction in ("asc", "desc"):
        body = client.get("/api/v1/real/listings", params={"sort": "price", "direction": direction}).json()
        prices = [i.get("price") for i in body["items"]]
        assert prices[-1] is None  # missing always last
        vals = [p for p in prices[:-1]]
        expected = sorted(vals) if direction == "asc" else sorted(vals, reverse=True)
        assert vals == expected


def test_listings_sort_surface_maps_field_and_missing_last(client, tmp_path):
    # sort=surface must map to the `surface_m2` field (not `surface`).
    _write(tmp_path / "listings.jsonl", [
        _rec(1, surface=60.0), _rec(2, surface=120.0), _rec(3, surface=None),
    ])
    asc = client.get("/api/v1/real/listings",
                     params={"sort": "surface", "direction": "asc"}).json()
    assert [i.get("surface_m2") for i in asc["items"]] == [60.0, 120.0, None]
    desc = client.get("/api/v1/real/listings",
                      params={"sort": "surface", "direction": "desc"}).json()
    assert [i.get("surface_m2") for i in desc["items"]] == [120.0, 60.0, None]


def test_listings_sort_zone_alphabetical(client, tmp_path):
    _write(tmp_path / "listings.jsonl", [
        _rec(1, comune="Trento", zone="Centro"),
        _rec(2, comune="Ala", zone="Zona sud"),
        _rec(3, comune="Trento", zone="Borgo"),
        _rec(4, comune=None, zone=None),
    ])
    asc = client.get("/api/v1/real/listings",
                     params={"sort": "zone", "direction": "asc"}).json()
    comunes = [(i.get("comune"), i.get("zone")) for i in asc["items"]]
    assert comunes == [("Ala", "Zona sud"), ("Trento", "Borgo"), ("Trento", "Centro"), (None, None)]


def test_export_reflects_sort_and_exceeds_500(client, tmp_path):
    _write(tmp_path / "listings.jsonl",
           [_rec(i, price=float(1000 - i)) for i in range(1, 520)])
    r = client.get("/api/v1/real/export", params={"format": "csv", "sort": "price", "direction": "asc"})
    rows = [line for line in r.text.splitlines() if line.strip()]
    assert len(rows) == 520  # header + all 519 matching rows (no 500 cap)
    prices = []
    import csv as _csv
    from io import StringIO
    for row in _csv.DictReader(StringIO(r.text)):
        prices.append(float(row["price"]))
    assert prices == sorted(prices)


def test_analysis_endpoint(client, tmp_path):
    _write(tmp_path / "listings.jsonl", [
        _rec(0, price=240000.0, surface=100.0)] + [_rec(i, price=300000.0, surface=100.0) for i in range(1, 11)])
    _write(tmp_path / "observations.jsonl", [
        {"key": "k", "indicator": "mortgage_rate_new_loans", "period": "2026-08", "value": 2.9,
         "source": "ECB", "unit": "percent_per_annum", "definition": "d", "definition_version": "v",
         "series": "S", "geo_level": "national", "territory_code": "IT"},
    ])
    body = client.get("/api/v1/real/listings/0/analysis").json()
    assert body["convenience"]["vantage_pct"] == 20.0
    assert body["context"]["mortgage_rates"]["last_period"] == "2026-08"
