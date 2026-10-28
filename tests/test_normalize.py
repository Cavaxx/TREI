from app.processors.normalize import (
    normalize_comune,
    normalize_energy_class,
    normalize_listing,
    parse_number,
    parse_price,
    parse_surface,
    price_per_m2,
)


def test_parse_italian_number_formats():
    assert parse_number("1.234,5") == 1234.5
    assert parse_number("85") == 85
    assert parse_number("85 m²") == 85
    assert parse_number("€ 250.000") == 250000
    assert parse_number(None) is None
    assert parse_number("no digits here") is None


def test_parse_price_and_surface():
    assert parse_price("250.000 €") == 250000
    assert parse_price("0") is None
    assert parse_surface("120 mq") == 120
    assert parse_surface("-5") is None


def test_normalize_comune_aliases_and_accents():
    assert normalize_comune("TRENTO") == "Trento"
    assert normalize_comune("Riva del Garda (TN)") == "Riva del Garda"
    assert normalize_comune("Pérgine Valsugana") == "Pergine Valsugana"
    assert normalize_comune("Unknownville") is None


def test_normalize_energy_class():
    assert normalize_energy_class("classe A4") == "A4"
    assert normalize_energy_class("G") == "G"
    assert normalize_energy_class(None) is None


def test_price_per_m2_none_safe():
    assert price_per_m2(200000, 100) == 2000
    assert price_per_m2(200000, None) is None
    assert price_per_m2(None, 100) is None
    assert price_per_m2(200000, 0) is None


def test_normalize_listing_shape_and_passthrough():
    raw = {"city": "trento", "asking_price": "300.000 €", "surface": "110 mq", "locali": "4"}
    out = normalize_listing(raw)
    assert out["comune"] == "Trento"
    assert out["price"] == 300000
    assert out["surface_m2"] == 110
    assert out["rooms"] == 4
    assert out["price_m2"] == 300000 / 110
    assert out["measure_type"] == "asking"
    assert out["is_asking"] is True
