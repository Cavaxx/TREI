"""Property Master tests: identity, history, coverage, pressure (§20-22, §40-43)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.property_master.coverage import coverage_report
from app.property_master.history import cumulative_discount, price_cut_count
from app.property_master.master import PropertyMasterStore
from app.property_master.pressure import seller_pressure_score

BASE = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def ts(days: int) -> str:
    return (BASE + timedelta(days=days)).isoformat()


def listing(**overrides) -> dict:
    base = {
        "comune": "Trento",
        "address": "Via Roma 12",
        "price": 250000.0,
        "surface_m2": 100.0,
        "rooms": 4,
        "lat": 46.0679,
        "lon": 11.1211,
        "description": "Appartamento luminoso in centro",
        "measure_type": "asking",
    }
    base.update(overrides)
    return base


def make_store() -> PropertyMasterStore:
    return PropertyMasterStore(review_threshold=0.75)


def test_same_property_on_two_portals_shares_one_master_id():
    store = make_store()
    store.observe(listing(), source="portal_a", source_listing_id="111", observed_at=ts(0))
    store.observe(
        listing(price=252000.0), source="portal_b", source_listing_id="999", observed_at=ts(1)
    )
    store.resolve()

    a = store.get("portal_a", "111")
    b = store.get("portal_b", "999")
    assert a is not None and b is not None
    assert a.property_master_id == b.property_master_id
    assert a.property_master_id.startswith("P")
    assert a.sources == ["portal_a", "portal_b"]
    # Listing identity is *not* merged: the ids stay distinct.
    assert a.listing_source_id != b.listing_source_id


def test_master_id_is_stable_when_a_new_portal_joins():
    store = make_store()
    store.observe(listing(), source="portal_a", source_listing_id="111", observed_at=ts(0))
    store.resolve()
    first = store.get("portal_a", "111").property_master_id

    store.observe(listing(), source="portal_b", source_listing_id="999", observed_at=ts(2))
    store.resolve()
    assert store.get("portal_a", "111").property_master_id == first
    assert store.get("portal_b", "999").property_master_id == first


def test_different_properties_do_not_merge():
    store = make_store()
    store.observe(listing(), source="portal_a", source_listing_id="1", observed_at=ts(0))
    store.observe(
        listing(address="Via Verdi 40", lat=46.1500, lon=11.2000, price=180000.0),
        source="portal_a",
        source_listing_id="2",
        observed_at=ts(0),
    )
    store.resolve()
    assert len({r.property_master_id for r in store.listings()}) == 2


def test_singleton_still_gets_a_master_id():
    store = make_store()
    store.observe(listing(), source="portal_a", source_listing_id="only", observed_at=ts(0))
    store.resolve()
    assert store.get("portal_a", "only").property_master_id is not None


def test_reobservation_of_unchanged_listing_does_not_add_a_snapshot():
    store = make_store()
    store.observe(listing(), source="portal_a", source_listing_id="1", observed_at=ts(0))
    store.observe(listing(), source="portal_a", source_listing_id="1", observed_at=ts(1))
    assert len(store.snapshots("portal_a::1")) == 1
    assert store.get("portal_a", "1").last_seen_at == ts(1)


def test_price_change_is_snapshotted_and_counted():
    store = make_store()
    for day, price in ((0, 300000.0), (10, 285000.0), (20, 270000.0)):
        store.observe(
            listing(price=price), source="portal_a", source_listing_id="1", observed_at=ts(day)
        )
    store.resolve()
    snapshots = store.snapshots("portal_a::1")
    assert len(snapshots) == 3
    assert price_cut_count(snapshots) == 2
    assert cumulative_discount(snapshots) == pytest.approx(0.10, abs=1e-6)
    intelligence = store.intelligence("portal_a::1", now=BASE + timedelta(days=30))
    assert intelligence["days_on_market"] == 20
    assert intelligence["availability"] == "active"
    assert intelligence["time_on_market_kind"] == "observed_time_on_market"


def test_removal_is_recorded_as_absent_not_as_a_sale():
    store = make_store()
    store.observe(listing(), source="portal_a", source_listing_id="1", observed_at=ts(0))
    store.observe(
        listing(),
        source="portal_a",
        source_listing_id="1",
        observed_at=ts(5),
        availability="removed",
    )
    store.resolve()
    record = store.get("portal_a", "1")
    assert record.is_active is False
    assert record.listing_episode_id is not None
    intelligence = store.intelligence("portal_a::1", now=BASE + timedelta(days=6))
    assert intelligence["availability"] == "removed"
    assert any("not a confirmed sale" in note for note in intelligence["notes"])


def test_relisting_keeps_the_master_and_reactivates():
    store = make_store()
    store.observe(listing(), source="portal_a", source_listing_id="1", observed_at=ts(0))
    store.observe(
        listing(),
        source="portal_a",
        source_listing_id="1",
        observed_at=ts(4),
        availability="removed",
    )
    store.observe(
        listing(),
        source="portal_a",
        source_listing_id="1",
        observed_at=ts(9),
        availability="active",
    )
    store.resolve()
    record = store.get("portal_a", "1")
    assert record.property_master_id is not None
    assert record.listing_episode_id is not None
    assert store.snapshots("portal_a::1")[-1].availability == "active"
    assert record.is_active is True


def test_missing_fields_stay_missing():
    store = make_store()
    payload = listing()
    payload["bathrooms"] = None
    payload["energy_class"] = None
    store.observe(payload, source="portal_a", source_listing_id="1", observed_at=ts(0))
    store.resolve()
    record = store.get("portal_a", "1")
    assert record.payload["bathrooms"] is None
    assert record.payload["energy_class"] is None
    assert record.first_seen_at == ts(0)


def test_review_queue_collects_low_confidence_fields():
    store = make_store()
    store.observe(
        listing(),
        source="portal_a",
        source_listing_id="1",
        observed_at=ts(0),
        review_fields=["bathrooms", "energy_class"],
    )
    store.resolve()
    queue = store.review_queue()
    assert len(queue) == 1
    assert queue[0]["review_fields"] == ["bathrooms", "energy_class"]


def test_coverage_report_never_claims_completeness():
    store = make_store()
    store.observe(listing(), source="portal_a", source_listing_id="1", observed_at=ts(0))
    store.observe(listing(), source="portal_b", source_listing_id="2", observed_at=ts(1))
    store.observe(
        listing(address="Via Verdi 40", lat=46.1500, lon=11.2000),
        source="portal_b",
        source_listing_id="3",
        observed_at=ts(1),
    )
    store.resolve()
    report = store.coverage()
    assert report.unique_property_master == 2
    assert report.raw_listings == 3
    assert "does not claim" in report.statement
    assert report.duplicate_rate is not None
    assert 0.0 <= report.coverage_confidence <= 1.0


def test_coverage_of_empty_store_is_zero_not_a_guess():
    report = coverage_report([])
    assert report.coverage_confidence == 0.0
    assert report.coverage_by_source == []
    assert report.unique_property_master == 0


def test_seller_pressure_is_neutral_wording_and_bounded():
    none = seller_pressure_score()
    assert none.level == "NONE"
    assert none.coverage == 0.0
    assert "no evidence" in none.description.lower()

    strong = seller_pressure_score(
        days_on_market=400,
        price_cut_count=3,
        cumulative_discount=0.15,
        days_since_last_cut=5,
        months_of_supply=20,
        monthly_absorption=0.001,
        market_regime="CRISIS",
    )
    assert strong.level == "HIGH"
    assert 0.0 <= strong.score <= 1.0
    assert "price pressure" in strong.description
    assert "disperat" not in strong.description.lower()
    assert strong.evidence and any("reduction" in line for line in strong.evidence)
