from app.auction_engine.anomaly import detect_anomalies
from app.auction_engine.dedup import (
    auction_identity,
    canonical_procedure_key,
    deduplicate_auctions,
    merge_auction_records,
)
from app.auction_engine.valuation import AuctionCostInputs, value_auction


def test_canonical_procedure_key_normalizes():
    k1 = canonical_procedure_key("Tribunale di Trento", "123/2025", "1")
    k2 = canonical_procedure_key("tribunale  di trento", "123/2025", "1")
    assert k1 == k2
    assert canonical_procedure_key(None, "123", "1") is None


def test_pvp_is_source_of_truth_on_merge():
    records = [
        {"source": "astalegale", "tribunal": "Trento", "procedure_number": "10/2025",
         "base_price": 99999, "lot_number": "1"},
        {"source": "pvp", "tribunal": "Trento", "procedure_number": "10/2025",
         "base_price": 80000, "lot_number": "1", "pvp_id": "PVP-1"},
    ]
    merged = merge_auction_records(records)
    assert merged["base_price"] == 80000  # PVP wins
    assert merged["pvp_id"] == "PVP-1"
    assert set(merged["sources"]) == {"astalegale", "pvp"}


def test_deduplicate_auctions_collapses_same_lot():
    records = [
        {"source": "pvp", "tribunal": "Trento", "procedure_number": "10/2025", "lot_number": "1",
         "pvp_id": "PVP-1", "base_price": 80000},
        {"source": "astalegale", "tribunal": "Trento", "procedure_number": "10/2025",
         "lot_number": "1", "base_price": 80000},
        {"source": "fallco", "tribunal": "Trento", "procedure_number": "11/2025", "lot_number": "1",
         "base_price": 90000},
    ]
    out = deduplicate_auctions(records)
    assert len(out) == 2  # 10/2025 collapsed, 11/2025 separate


def test_auction_identity_stable():
    ident1 = auction_identity({"pvp_id": "PVP-9"})
    ident2 = auction_identity({"pvp_id": "PVP-9"})
    assert ident1.master_auction_id == ident2.master_auction_id


def test_risk_adjusted_discount_differs_from_naive():
    inputs = AuctionCostInputs(
        auction_purchase_price=80000,
        estimated_market_value=150000,
        renovation=20000,
        arrears=5000,
        risk_adjustment=10000,
    )
    val = value_auction(inputs, minimum_offer=60000, appraisal_value=150000)
    assert val.estimated_total_acquisition_cost == 115000
    assert abs(val.naive_discount - 0.6) < 1e-9          # (1 - 60000/150000)
    assert abs(val.risk_adjusted_discount - (1 - 115000 / 150000)) < 1e-9
    assert val.risk_adjusted_discount < val.naive_discount  # costs erode the "bargain"


def test_anomaly_flags_and_risk_level():
    rec = {"occupancy_status": "occupied", "has_building_abuse": True, "has_undivided_share": True}
    rep = detect_anomalies(rec)
    assert rep.flags["occupied"] is True
    assert rep.risk_level == "HIGH"
    clean = detect_anomalies({"occupancy_status": "free"})
    assert clean.risk_level == "LOW"
