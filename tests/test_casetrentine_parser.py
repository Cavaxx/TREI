"""Offline tests for the CaseTrentine detail parser (minimized fixture)."""

from __future__ import annotations

from app.acquisition.connectors.casetrentine import parse_casetrentine_detail

DETAIL = """
<html><head><title>Appartamento in vendita Mori (ID 13281) | CaseTrentine.it</title></head>
<body>
<div class="box_top_data">
  <dl><dt>Codice</dt><dd>FP-115</dd></dl>
  <dl><dt>Mq</dt><dd>55</dd><dt>Locali</dt><dd>2</dd><dt>Camere</dt><dd>-</dd><dt>Bagni</dt><dd>-</dd></dl>
  <p class="price">&euro; 147.000,00</p>
  <p class="location">Mori</p>
</div>
<div class="box_description"><p>Classe energetica: F - MORI, FP-115: PERSONALRE propone appartamento in vendita</p></div>
</body></html>
"""

MAGAZZINO = """
<html><head><title>Magazzino in vendita Trento (ID 9) | CaseTrentine.it</title></head>
<body><p class="price">&euro; 10.000,00</p><p class="location">Trento</p></body></html>
"""


def test_parse_detail_core_fields():
    listing = parse_casetrentine_detail(DETAIL, url="https://www.casetrentine.it/case-in-vendita/view/13281/Appartamento-a-Mori",
                                  source="casetrentine_it", run_id="r1")
    assert listing is not None
    assert listing.native_id == "13281"
    assert listing.price == 147000.0
    assert listing.surface_m2 == 55.0
    assert listing.rooms == 2
    assert listing.bedrooms is None  # '-' is NOT zero
    assert listing.bathrooms is None
    assert listing.comune == "Mori" and listing.province == "TN"
    assert listing.energy_class == "F"
    assert "FP-115" in listing.advertiser
    assert listing.unit_class == "residenziale"


def test_non_residential_excluded():
    listing = parse_casetrentine_detail(MAGAZZINO, url="https://x/view/9/m", source="casetrentine_it", run_id="r1")
    assert listing is not None
    assert listing.unit_class == ""  # magazzino: outside residential perimeter
    assert listing.typology == "magazzino"


def test_offer_kind_marker():
    html = DETAIL.replace("appartamento in vendita", "vendita quota di 1/2 di appartamento")
    listing = parse_casetrentine_detail(html, url="https://x/view/1/a", source="casetrentine_it", run_id="r1")
    assert listing is not None and listing.offer_kind == "quota"
