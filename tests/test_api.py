from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)


def test_health_reports_demo_data():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["listings"] > 0
    assert body["demo_data"] is True


def test_meta_has_provenance():
    r = client.get("/api/v1/meta")
    assert r.status_code == 200
    assert "provenance" in r.json()


def test_sources_endpoint_lists_registry():
    r = client.get("/api/v1/sources")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["sources"]}
    assert "idealista_it" in names
    # Every source carries its licence posture, so the UI cannot imply access
    # that no contract grants.
    assert all("licence_status" in s for s in r.json()["sources"])


def test_licences_endpoint_states_the_rule_and_is_empty_by_default():
    r = client.get("/api/v1/licences")
    assert r.status_code == 200
    body = r.json()
    assert body["licences"] == []
    assert "no automated request" in body["rule"]


def test_market_summary_has_asking_measure():
    r = client.get("/api/v1/market/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["measure_type"] == "asking"
    assert body["by_comune"]
    assert body["by_comune"][0]["median"] > 0


def test_listings_and_detail():
    r = client.get("/api/v1/listings?limit=3")
    assert r.status_code == 200
    assert r.json()["count"] <= 3
    first = r.json()["listings"][0]
    detail = client.get(f"/api/v1/listings/{first['listing_source_id']}")
    assert detail.status_code == 200
    assert detail.json()["listing_source_id"] == first["listing_source_id"]


def test_valuation_endpoint_returns_interval_and_confidence():
    payload = {
        "comune": "Trento",
        "surface_m2": 100,
        "rooms": 4,
        "condition": "good",
        "energy_class": "C",
        "price": 250000,
    }
    r = client.post("/api/v1/valuation", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["estimated_market_value"] > 0
    assert body["prediction_interval_low"] < body["prediction_interval_high"]
    assert body["classification"] in {"UNDER_MARKET", "IN_LINE", "OVER_MARKET"}
    assert body["confidence"] in {"HIGH", "MEDIUM", "LOW"}


def test_auctions_endpoint_includes_anomaly():
    r = client.get("/api/v1/auctions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] > 0
    assert "risk_level" in body["auctions"][0]["anomaly"]


def test_scenarios_and_affordability_endpoints():
    r = client.post("/api/v1/scenarios/run", json={"mortgage_rate_pp": 1.0})
    assert r.status_code == 200
    assert r.json()["price_impact_pct"] < 0

    r2 = client.post(
        "/api/v1/affordability",
        json={"purchase_price": 300000, "down_payment": 60000, "mortgage_rate": 0.035},
    )
    assert r2.status_code == 200
    assert r2.json()["monthly_payment"] > 0


def test_forecast_endpoint_insufficient_vs_sufficient():
    short = client.post("/api/v1/forecast/directional", json={"series": [1, 2, 3], "horizon": 1})
    assert short.status_code == 200
    assert short.json()["evidence"] == "INSUFFICIENT_EVIDENCE"

    series = [100 * (1.02 ** i) for i in range(120)]
    long = client.post(
        "/api/v1/forecast/directional", json={"series": series, "horizon": 12}
    )
    assert long.status_code == 200
    assert long.json()["evidence"] == "SUFFICIENT"


# --- Phase 2 endpoints ------------------------------------------------------

def test_agent_sources_exposes_the_access_verdict():
    r = client.get("/api/v1/agent/sources")
    assert r.status_code == 200
    body = r.json()
    by_name = {s["source"]: s for s in body["sources"]}
    assert by_name["idealista_it"]["allowed"] is False
    assert by_name["idealista_it"]["mode"] == "restricted"
    assert "no positive policy decision" in body["rule"]


def test_coverage_states_observations_not_completeness():
    r = client.get("/api/v1/coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["unique_property_master"] > 0
    assert body["raw_listings"] >= body["unique_property_master"]
    assert "does not claim" in body["statement"]
    assert 0.0 <= body["coverage_confidence"] <= 1.0


def test_property_detail_returns_intelligence_and_source_coverage():
    coverage = client.get("/api/v1/coverage").json()
    master_id = None
    listings = client.get("/api/v1/listings?limit=1").json()["listings"]
    assert listings
    # Find a master id from the property master, not the demo repository.
    from app.api.deps import get_property_master

    store = get_property_master()
    master_id = store.listings()[0].property_master_id

    r = client.get(f"/api/v1/property/{master_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["property_master_id"] == master_id
    assert body["listing_intelligence"]["time_on_market_kind"] == "observed_time_on_market"
    assert body["seller_pressure"]["level"] in {"NONE", "LOW", "MODERATE", "ELEVATED", "HIGH"}
    assert body["source_coverage"]["observed_from"]
    assert coverage["unique_property_master"] > 0


def test_property_detail_404_for_unknown_master():
    assert client.get("/api/v1/property/P-does-not-exist").status_code == 404


def test_review_queue_endpoint_shape():
    body = client.get("/api/v1/review-queue").json()
    assert "count" in body and "review_required" in body


def test_crawl_metrics_endpoint_shape():
    body = client.get("/api/v1/crawl/metrics").json()
    assert "sources" in body and "totals" in body


def test_macro_context_never_interpolates_unpublished_periods():
    payload = {
        "rows": [
            {"indicator": "price_index", "period": "2025-01", "value": 100.0},
            {"indicator": "price_index", "period": "2025-02", "value": 102.0},
            {"indicator": "price_index", "period": "2025-03", "status": "NOT_YET_PUBLISHED"},
            {"indicator": "households", "period": "2023", "value": 1000.0},
            {"indicator": "households", "period": "2024", "value": 1030.0},
        ]
    }
    r = client.post("/api/v1/market/macro/context", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["context"]["price_index"]["status"] == "NOT_YET_PUBLISHED"
    assert body["context"]["price_index"]["published"] is False
    assert body["context"]["derived"]["household_growth"] == [30.0]
    assert any("never interpolated" in note for note in body["context"]["notes"])
