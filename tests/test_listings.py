"""Offline tests for the Tecnocasa listing parser and store."""

from __future__ import annotations

from app.acquisition.listings import (
    Listing,
    ListingStore,
    _euro_of,
    _int_of,
    parse_tecnocasa_detail,
)

DETAIL_HTML = """
<estate-show-v1 :estate='{"ad_type":"estate","id":61025552,
"detail_url":"https://www.tecnocasa.it/vendita/appartamenti/trento/arco/61025552.html",
"title":"Monolocale in vendita","type":{"title":"appartamenti"},"contract":{"slug":"vendita"},
"numeric_price":208000,"price":"€ 208.000","previous_price":"€ 218.000","discount":"€ 10.000",
"numeric_surface":"62.00","surface":"62 Mq",
"city":{"title":"Arco"},"province":{"id":"TN","title":"Trento"},"district":{"title":"Arco"},
"address":"Vicolo Vigna","latitude":45.9203,"longitude":10.8589,
"rooms":"1 locale","bathrooms":"1 bagno",
"features":{"floor":"3 di 2 (piano alto)","elevator_immo":"No","build_year":1996},
"energy_data":{"label":"f"},
"last_published_at":"2025-11-13 10:52:03","description":"<p>Descrizione</p>"}'></estate-show-v1>
"""


def test_euro_and_int_parsers():
    assert _euro_of("€ 208.000") == 208000.0
    assert _int_of("1 locale") == 1
    assert _int_of("3 bagni") == 3


def test_parse_tecnocasa_detail_maps_fields():
    listing = parse_tecnocasa_detail(DETAIL_HTML, url="https://x/1.html", source="tecnocasa_it", run_id="r")
    assert listing is not None
    assert listing.native_id == "61025552"
    assert listing.price == 208000.0
    assert listing.previous_price == 218000.0
    assert listing.surface_m2 == 62.0
    assert listing.comune == "Arco"
    assert listing.province == "TN"
    assert listing.rooms == 1
    assert listing.bathrooms == 1
    assert listing.floor == "3 di 2 (piano alto)"
    assert listing.elevator == "No"
    assert listing.energy_class == "f"
    assert listing.published_at == "2025-11-13 10:52:03"
    assert listing.contract == "vendita"


def test_same_run_is_idempotent(tmp_path):
    store = ListingStore(tmp_path / "l.jsonl")
    a = Listing(source="s", native_id="1", price=100.0, run_id="r1")
    b = Listing(source="s", native_id="1", price=100.0, run_id="r1")
    assert store.observe(a)["new"] is True
    assert store.observe(b)["stored"] is False  # same run, same listing -> no-op
    assert len(store.current()) == 1


def test_revisit_without_change_is_recorded(tmp_path):
    store = ListingStore(tmp_path / "l.jsonl")
    a = Listing(source="s", native_id="1", price=100.0, run_id="r1")
    b = Listing(source="s", native_id="1", price=100.0, run_id="r2")
    assert store.observe(a)["new"] is True
    res = store.observe(b)
    assert res["stored"] is True and res["revisit"] is True  # rilevazione kept, no new state
    assert len(store.current()) == 1
    assert store.visits() == 1
    assert store.snapshots() == 1


def test_price_a_to_b_to_a_preserves_history(tmp_path):
    store = ListingStore(tmp_path / "l.jsonl")
    assert store.observe(Listing(source="s", native_id="1", price=100.0, run_id="r1"))["new"] is True
    assert store.observe(Listing(source="s", native_id="1", price=200.0, run_id="r2"))["price_changed"] is True
    res = store.observe(Listing(source="s", native_id="1", price=100.0, run_id="r3"))
    assert res["price_changed"] is True  # A -> B -> A: returning to A is an event
    assert [h["price"] for h in store.history("1")] == [100.0, 200.0, 100.0]


GABETTI_HTML = """<html><head>
<meta property="og:title" content="Immobile Appartamento in Viale dei Tigli  14, Riva del Garda (TN) 695.000&nbsp;€, 160 m², 4 locali, 2 bagni | Gabetti"/>
<meta name="description" content="Appartamento in Riva del Garda"/>
</head><body></body></html>"""


def test_parse_gabetti_detail_from_meta():
    from app.acquisition.listings import parse_gabetti_detail

    listing = parse_gabetti_detail(GABETTI_HTML, url="https://www.gabetti.it/vendita/riva-del-garda/appartamento/3337645", source="gabetti_it", run_id="r")
    assert listing is not None
    assert listing.native_id == "3337645"
    assert listing.price == 695000.0
    assert listing.surface_m2 == 160.0
    assert listing.rooms == 4
    assert listing.bathrooms == 2
    assert listing.comune == "Riva del Garda"
    assert listing.province == "TN"
    assert listing.typology == "appartamento"


def test_content_hash_change_does_not_duplicate_state(tmp_path):
    store = ListingStore(tmp_path / "l.jsonl")
    a = Listing(source="s", native_id="1", price=100.0, content_hash="h1", run_id="r1")
    b = Listing(source="s", native_id="1", price=100.0, content_hash="h2", run_id="r2")
    assert store.observe(a)["new"] is True
    assert store.observe(b)["revisit"] is True  # same fields, different raw hash -> revisit, not new state
    assert len(store.current()) == 1


def test_normalize_comune():
    from app.acquisition.listings import normalize_comune

    assert normalize_comune("RIVA DEL GARDA") == "Riva del Garda"
    assert normalize_comune("riva del garda") == "Riva del Garda"
    assert normalize_comune("Pergine Valsugana") == "Pergine Valsugana"
    assert normalize_comune("") == ""


def test_group_candidates_conservative():
    from app.api.realdata import group_candidates

    listings = [
        {"source": "a", "native_id": "1", "comune": "Arco", "typology": "appartamento", "surface_m2": 100.0, "url": "u1"},
        {"source": "b", "native_id": "2", "comune": "Arco", "typology": "appartamento", "surface_m2": 105.0, "url": "u2"},
        {"source": "b", "native_id": "3", "comune": "Arco", "typology": "appartamento", "surface_m2": 200.0, "url": "u3"},
        {"source": "a", "native_id": "4", "comune": "Arco", "typology": "appartamento", "surface_m2": 101.0, "url": "u4"},
    ]
    groups = group_candidates(listings)
    assert len(groups) == 1  # only the 100/105 cross-source pair groups
    assert groups[0]["confidence"] == "provisional"
    ids = {m["native_id"] for m in groups[0]["listings"]}
    assert ids == {"1", "2"}


def test_listing_store_parser_version_change_does_not_reobserve(tmp_path):
    store = ListingStore(tmp_path / "listings.jsonl")
    a = Listing(source="tecnocasa_it", native_id="1", price=208000.0, parser_version="v1", run_id="r1")
    b = Listing(source="tecnocasa_it", native_id="1", price=208000.0, parser_version="v2", run_id="r2")
    assert store.observe(a)["new"] is True
    # Content identity depends on the extracted fields ONLY: a parser-version
    # bump that maps the same fields identically must not create a new version.
    assert store.observe(b)["revisit"] is True
    assert store.snapshots() == 1
    assert store.visits() == 1
