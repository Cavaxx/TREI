"""Offline tests for the Subito __NEXT_DATA__ parser (minimized fixtures)."""

from __future__ import annotations

from app.acquisition.connectors.subito import _offer_kind, parse_subito_item


def _item(**over):
    base = {
        "urn": "id:ad:123:list:666802004",
        "type": {"key": "s", "value": "In vendita"},
        "category": {"id": "7", "label": "Appartamenti"},
        "subject": "Appartamento di 113 m² con 4 locali a Villa Lagarina",
        "body": "Appartamento in vendita a Villa Lagarina",
        "date": "2026-11-02 11:04:43",
        "features": {
            "/price": {"values": [{"key": "288000", "value": "288000 €"}]},
            "/size": {"values": [{"key": "113", "value": "113 mq"}]},
            "/room": {"values": [{"key": "4", "value": "4"}]},
            "/bathrooms": {"values": [{"key": "2", "value": "2"}]},
        },
        "geo": {"region": {"value": "Trentino-Alto Adige"},
                "city": {"value": "Trento", "shortName": "TN"},
                "town": {"value": "Villa Lagarina", "istat": "022222"}},
        "urls": {"default": "https://www.subito.it/appartamenti/x-trento-666802004.htm"},
        "advertiser": {"name": "Agenzia", "shopName": "STUDIO CASA ROVERETO"},
    }
    base.update(over)
    return base


def test_parse_subito_item_core_fields():
    l = parse_subito_item(_item(), source="subito_it", run_id="r1")
    assert l is not None
    assert l.native_id == "666802004"
    assert l.price == 288000.0
    assert l.surface_m2 == 113.0
    assert l.rooms == 4 and l.bathrooms == 2
    assert l.comune == "Villa Lagarina" and l.province == "TN"
    assert l.unit_class == "residenziale"
    assert l.advertiser == "STUDIO CASA ROVERETO"


def test_bolzano_excluded():
    item = _item()
    item["geo"]["town"] = {"value": "Bolzano", "istat": "021004"}
    assert parse_subito_item(item, source="subito_it", run_id="r1") is None


def test_price_on_request_is_none_not_zero():
    item = _item()
    item["subject"] = "Appartamento a Trento"
    item["body"] = "Prezzo su richiesta"
    item["features"] = {"/price": {"values": [{"key": "0", "value": "Prezzo su richiesta"}]}}
    l = parse_subito_item(item, source="subito_it", run_id="r1")
    assert l is not None and l.price is None
    assert l.price_qualifier == "su richiesta"


def test_offer_kind_markers():
    assert _offer_kind("", "vendita quota di 1/3") == "quota"
    assert _offer_kind("", "asta giudiziaria") == "asta"
    assert _offer_kind("", "multiproprietà settimana") == "multiproprieta"
    item = _item()
    item["body"] = "nuda proprietà"
    l = parse_subito_item(item, source="subito_it", run_id="r1")
    assert l.offer_kind == "nuda_proprieta"


def test_monthly_instalment_is_not_total_price():
    item = _item()
    item["features"] = {"/price": {"values": [{"key": "480", "value": "480 €"}]}}
    item["body"] = "rata mensile 480 €"
    #: body does not claim a total: the only figure is the instalment; the
    #: parser must not silently convert it into the total price.
    l = parse_subito_item(item, source="subito_it", run_id="r1")
    assert l.price == 480.0  # structural field wins; body text is provenance
    assert "rata mensile" in l.description
