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


def test_listing_store_idempotent_and_history(tmp_path):
    store = ListingStore(tmp_path / "listings.jsonl")
    a = Listing(source="tecnocasa_it", native_id="1", price=208000.0, content_hash="h1")
    b = Listing(source="tecnocasa_it", native_id="1", price=208000.0, content_hash="h1")
    assert store.observe(a)["new"] is True
    assert store.observe(b)["stored"] is False  # identical -> no-op
    assert len(store.current()) == 1

    c = Listing(source="tecnocasa_it", native_id="1", price=198000.0, content_hash="h2")
    res = store.observe(c)
    assert res["price_changed"] is True
    assert len(store.history("1")) == 2  # history preserved, not overwritten


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


def test_observe_content_hash_change_does_not_duplicate(tmp_path):
    store = ListingStore(tmp_path / "l.jsonl")
    a = Listing(source="s", native_id="1", price=100.0, content_hash="h1")
    b = Listing(source="s", native_id="1", price=100.0, content_hash="h2")  # different raw hash, same fields
    assert store.observe(a)["new"] is True
    assert store.observe(b)["stored"] is False  # signature unchanged -> no-op
    assert len(store.current()) == 1


def test_listing_store_parser_version_change_reobserves(tmp_path):
    store = ListingStore(tmp_path / "listings.jsonl")
    a = Listing(source="tecnocasa_it", native_id="1", price=208000.0, content_hash="h1", parser_version="v1")
    b = Listing(source="tecnocasa_it", native_id="1", price=208000.0, content_hash="h1", parser_version="v2")
    assert store.observe(a)["new"] is True
    assert store.observe(b)["stored"] is True  # parser change re-stores
