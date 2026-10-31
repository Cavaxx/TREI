"""Offline tests for the authorized-portal Kernel connector (no sessions)."""

from __future__ import annotations

from app.acquisition.connectors.portals import parse_portal_detail


def test_parse_portal_detail_from_jsonld():
    payload = {
        "jsonld": [{
            "@type": "RealEstateListing",
            "name": "Appartamento in vendita a Trento",
            "address": {"addressLocality": "Trento", "streetAddress": "Via Roma 1"},
            "offers": {"price": "235000", "priceCurrency": "EUR"},
            "floorSize": {"value": "98"},
            "numberOfRooms": "4",
        }],
        "meta": {},
        "title": "Appartamento in vendita a Trento",
        "text": "",
    }
    listing = parse_portal_detail(payload, url="https://www.idealista.it/immobile/123456/", source="idealista_it", run_id="p-r1")
    assert listing is not None
    assert listing.price == 235000.0
    assert listing.surface_m2 == 98.0
    assert listing.rooms == 4
    assert listing.comune == "Trento"
    assert listing.contract == "vendita"
    assert listing.unit_class == "residenziale"
    assert listing.native_id == "123456"


def test_parse_portal_detail_from_meta_text():
    payload = {
        "jsonld": [],
        "meta": {"og:title": "Casa in vendita a Riva del Garda", "og:description": "120 m², 5 locali, 2 bagni, 320.000 €"},
        "title": "Casa in vendita a Riva del Garda",
        "text": "Casa in vendita a Riva del Garda (TN) 120 m², 5 locali, 2 bagni, 320.000 €",
    }
    listing = parse_portal_detail(payload, url="https://www.subito.it/annunci-italia/vendita/immobili/998877", source="subito_it", run_id="p-r2")
    assert listing is not None
    assert listing.price == 320000.0
    assert listing.surface_m2 == 120.0
    assert listing.rooms == 5
    assert listing.bathrooms == 2
    assert listing.comune == "Riva del Garda"
    assert listing.province == "TN"


def test_parse_portal_detail_returns_none_without_fields():
    listing = parse_portal_detail({"jsonld": [], "meta": {}, "title": "Pagina generica", "text": "nessun dato"},
                                  url="https://x/1", source="immobiliare_it", run_id="r")
    assert listing is None
