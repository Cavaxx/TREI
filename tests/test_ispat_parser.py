"""Offline tests for the ISPAT parser (no network, no Kernel).

The V.07 fixture reproduces the real two-row header (colspan/rowspan) and the
2015 row published in the annuario, used as a control value.
"""

from __future__ import annotations

from app.acquisition.connectors.ispat import _parse_it_number, grid_from_html, parse_i26, parse_v07

#: Real header + 2015 control row from tav. V.07 (Fonte: Istat).
V07_GRID = [
    ["Anni", "Compravendite", "Mutui con costituzione di ipoteca immobiliare"],
    ["Unita' immobiliari ad uso abitazione ed accessori", "Unita' immobiliari ad uso economico"],
    ["2000", "7.959", "670", "3.778"],
    ["2015", "8.129", "452", "6.365"],
    ["2023", "9.350", "584", "4.126"],
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


def test_parse_v07_2015_control_row():
    """2015: compravendite abitazione/accessori 8.129, uso economico 452, mutui 6.365."""
    observations = parse_v07(V07_GRID, source="s", run_id="r")
    assert len(observations) == 9  # 3 years x 3 indicators
    by = {(o.indicator, o.period): o for o in observations}

    res = by[("compravendite_residenziali", "2015")]
    assert res.value == 8129.0
    assert res.unit == "unita_immobiliari"
    assert res.series == "ISPAT_COMPRAVENDITE"

    eco = by[("compravendite_economiche", "2015")]
    assert eco.value == 452.0

    mut = by[("mutui", "2015")]
    assert mut.value == 6365.0
    assert mut.series == "ISPAT_MUTUI"

    # The indicators are three distinct series, never pooled with the others.
    assert {o.indicator for o in observations} == {
        "compravendite_residenziali",
        "compravendite_economiche",
        "mutui",
    }


def test_parse_i26_uses_only_rendered_year_columns():
    observations = parse_i26(I26_GRID, source="s", run_id="r")
    assert len(observations) == 12  # 2 territories x 6 years
    val = next(o for o in observations if o.territory_code == "Val di Fiemme" and o.period == "2015")
    assert val.value == 8683.0
    assert val.geo_level == "comunita_di_valle"
    periods = {o.period for o in observations}
    assert periods == {"1990", "2010", "2015", "2020", "2023", "2024"}
    assert "2014" not in periods


def test_grid_from_html_reconstructs_real_table():
    html = (
        "<table><tbody>"
        "<tr><td rowspan='2'>Anni</td><td colspan='2'>Compravendite</td><td rowspan='2'>Mutui con costituzione di<br>ipoteca immobiliare</td></tr>"
        "<tr><td>Unita' immobiliari ad uso abitazione ed accessori</td><td>Unita' immobiliari ad uso economico</td></tr>"
        "<tr><td>2015</td><td>8.129</td><td>452</td><td>6.365</td></tr>"
        "</tbody></table>"
    )
    grid = grid_from_html(html)
    observations = parse_v07(grid, source="s", run_id="r")
    assert len(observations) == 3
    assert observations[0].value == 8129.0
    assert observations[1].value == 452.0
    assert observations[2].value == 6365.0

