"""Offline tests for the ISPAT parser (no network, no Kernel)."""

from __future__ import annotations

from app.acquisition.connectors.ispat import _parse_it_number, parse_i26, parse_v07

V07_GRID = [
    ["Anni", "Compravendite", "Mutui con costituzione di ipoteca immobiliare"],
    ["", "Unita' immobiliari ad uso abitazione ed accessori", "Unita' immobiliari ad uso economico"],
    ["2000", "7.959", "670", "3.778"],
    ["2015", "8.129", "452", "6.365"],
    ["2023", "9.350", "584", "4.126"],
    ["Fonte: Istat", "", "", ""],
]

I26_GRID = [
    ["Comunita' di Valle", "1990", "2010", "2015", "2020", "2023", "2024"],
    ["Val di Fiemme", "6.652", "8.445", "8.683", "9.056", "9.125", "9.162"],
    ["Provincia", "172.105", "225.316", "233.001", "240.756", "244.117", "245.950"],
]


def test_parse_it_number_italian_thousands():
    assert _parse_it_number("6.652") == 6652.0
    assert _parse_it_number("1.234,5") == 1234.5
    assert _parse_it_number("") is None
    assert _parse_it_number("n.d.") is None


def test_parse_v07_separates_three_indicators():
    observations = parse_v07(V07_GRID, source="s", run_id="r")
    assert len(observations) == 9  # 3 years x 3 indicators
    indicators = {o.indicator for o in observations}
    assert indicators == {"compravendite_residenziali", "mutui_residenziali", "mutui_economici"}
    comp_2015 = next(o for o in observations if o.indicator == "compravendite_residenziali" and o.period == "2015")
    assert comp_2015.value == 8129.0
    assert comp_2015.series == "ISPAT_NOTARIAL"
    assert comp_2015.geo_level == "province"
    # The "Fonte: Istat" row is not treated as a year.
    assert all(o.period in {"2000", "2015", "2023"} for o in observations)


def test_parse_i26_uses_only_rendered_year_columns():
    observations = parse_i26(I26_GRID, source="s", run_id="r")
    assert len(observations) == 12  # 2 territories x 6 years
    val = next(o for o in observations if o.territory_code == "Val di Fiemme" and o.period == "2015")
    assert val.value == 8683.0
    assert val.geo_level == "comunita_di_valle"
    # Only the rendered years are present; no interpolation of missing years.
    periods = {o.period for o in observations}
    assert periods == {"1990", "2010", "2015", "2020", "2023", "2024"}
    assert "2014" not in periods
