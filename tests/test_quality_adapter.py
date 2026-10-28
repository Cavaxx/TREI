import json

from app.fetchers.adapters.opendata import OpenDataAdapter
from app.processors.quality import check_listing, in_trentino_bbox


def test_quality_flags_missing_and_outliers():
    rep = check_listing({"listing_source_id": "1"})
    codes = {i.code for i in rep.issues}
    assert "price_invalid" in codes
    assert "surface_invalid" in codes
    assert rep.has_error

    outlier = check_listing(
        {"listing_source_id": "2", "price": 5_000_000, "surface_m2": 100, "lat": 46.07, "lon": 11.12}
    )
    assert outlier.is_outlier  # 50,000 €/m² is implausible


def test_trentino_bbox():
    assert in_trentino_bbox(46.07, 11.12) is True
    assert in_trentino_bbox(41.90, 12.50) is False  # Rome
    assert in_trentino_bbox(None, 11.12) is None


def test_opendata_adapter_parses_list_and_csv():
    adapter = OpenDataAdapter(
        urls=[], fmt="json",
        record_mapper=lambda r: {"comune": r.get("territorio"), "value": r.get("valore")},
    )
    payload = json.dumps({"data": [{"territorio": "Trento", "valore": 123}]}).encode()
    rows = adapter.parse("https://example.com/data.json", payload)
    assert rows == [{"comune": "Trento", "value": 123}]

    csv_adapter = OpenDataAdapter(urls=[], fmt="csv")
    csv_payload = b"a,b\n1,2\n3,4\n"
    out = csv_adapter.parse("https://example.com/data.csv", csv_payload)
    assert out == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
