from app.processors.dedup import (
    deduplicate,
    haversine_m,
    reconstruct_episodes,
    score_pair,
    token_jaccard,
)


def _listing(**kw):
    base = {
        "listing_source_id": kw.pop("listing_source_id", "X"),
        "source": kw.pop("source", "src"),
        "comune": "Trento",
        "address": "Via Roma 10",
        "surface_m2": 100.0,
        "price": 250000.0,
        "rooms": 4,
        "condition": "good",
        "lat": 46.0679,
        "lon": 11.1211,
        "description": "appartamento luminoso centro",
    }
    base.update(kw)
    return base


def test_haversine_sanity():
    d = haversine_m(46.0679, 11.1211, 46.0679, 11.1211)
    assert d == 0
    d2 = haversine_m(46.0679, 11.1211, 46.0679, 11.1221)
    assert 0 < d2 < 200


def test_token_jaccard():
    assert token_jaccard("a b c", "a b c") == 1.0
    assert token_jaccard("a b", "c d") == 0.0


def test_identical_listings_match():
    res = score_pair(_listing(), _listing(listing_source_id="Y"))
    assert res.is_match
    assert res.score > 0.9


def test_far_apart_listings_do_not_match():
    a = _listing()
    b = _listing(listing_source_id="Y", lat=46.5, lon=11.5)
    res = score_pair(a, b)
    assert not res.is_match
    assert res.evidence.geo_m > 200


def test_deduplicate_groups_identical():
    listings = [
        _listing(listing_source_id="A"),
        _listing(listing_source_id="B"),  # duplicate of A
        _listing(listing_source_id="C", surface_m2=300, price=900000, rooms=8, address="Via Verdi 5"),
    ]
    out = deduplicate(listings)
    masters = {r["listing_source_id"]: r["property_master_id"] for r in out}
    assert masters["A"] == masters["B"]
    assert masters["C"] != masters["A"]
    sizes = {r["property_master_id"]: r["dedup_group_size"] for r in out}
    assert sizes[masters["A"]] == 2


def test_episode_reconstruction_preserves_history():
    events = [
        {"property_master_id": "P1", "listing_source_id": "A", "timestamp": "2024-01-01", "event": "listing_created"},
        {"property_master_id": "P1", "listing_source_id": "A", "timestamp": "2024-02-01", "event": "price_changed"},
        {"property_master_id": "P1", "listing_source_id": "A", "timestamp": "2024-03-01", "event": "listing_removed"},
        {"property_master_id": "P1", "listing_source_id": "B", "timestamp": "2024-06-01", "event": "listing_relisted"},
    ]
    out = reconstruct_episodes(events)
    ep_first = out[0]["listing_episode_id"]
    assert out[0]["listing_episode_id"] == out[1]["listing_episode_id"]  # same episode
    assert out[3]["listing_episode_id"] != ep_first  # new episode after re-list
    assert out[1]["starts_new_episode"] is False
