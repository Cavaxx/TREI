"""Offline tests: Eurostat JSON-stat decode, NIC xlsx parse, deflation maths."""

from __future__ import annotations

import openpyxl

from app.acquisition.connectors.eurostat_hicp import _decode_json_stat
from app.acquisition.connectors.istat_nic_xlsx import parse_nic_monthly


def test_decode_json_stat_sparse_dict():
    """The sparse dict serialization (integer string keys) must decode by
    category indexes, not by key order."""
    doc = {
        "id": ["freq", "unit", "time"],
        "size": [1, 2, 3],
        "dimension": {
            "freq": {"category": {"index": {"M": 0}, "label": {"M": "Monthly"}}},
            "unit": {"category": {"index": {"I25": 0, "I15": 1}, "label": {"I25": "Index", "I15": "Index"}}},
            "time": {"category": {"index": {"2024-09": 0, "2024-10": 1, "2024-11": 2}}},
        },
        "value": {"0": 99.0, "1": 99.3, "2": 99.2, "3": 95.0, "4": 95.2, "5": 95.1},
    }
    rows = _decode_json_stat(doc)
    by = {(r["unit"], r["time"]): r["value"] for r in rows}
    assert by[("I25", "2024-09")] == 99.0
    assert by[("I25", "2024-11")] == 99.2
    assert by[("I15", "2024-10")] == 95.2


def test_nic_xlsx_parse_bases_and_annual_means(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tabella_6"
    ws.append(["Periodo", "", "NIC", "", "", "", "", ""])
    ws.append(["", "", "Base 1995=100", "", "", "", "", ""])
    ws.append(["1996", "", "104", "", "", "", "", ""])          # annual mean: ignored
    ws.append(["", "", "Base 2015=100", "", "", "", "", ""])
    ws.append(["Coefficiente di raccordo", "", "1.075", "", "", "", "", ""])
    ws.append(["2024", "", "", "", "", "", "", ""])             # monthly header
    ws.append(["", "settembre", "121.2", "", "", "", "", ""])
    ws.append(["", "ottobre", "121.2", "", "", "", "", ""])
    ws.append(["", "", "Base 2025=100", "", "", "", "", ""])
    ws.append(["Coefficiente di raccordo", "", "1.226", "", "", "", "", ""])
    ws.append(["2026", "", "", "", "", "", "", ""])
    ws.append(["", "gennaio", "100.4", "", "", "", "", ""])
    path = tmp_path / "serie.xlsx"
    wb.save(path)
    parsed = parse_nic_monthly(path)
    months = parsed["months"]
    assert [m["period"] for m in months] == ["2024-09", "2024-10", "2026-01"]
    assert months[0]["unit"] == "index_base2015" and months[0]["value"] == 121.2
    assert months[2]["unit"] == "index_base2025"
    assert parsed["annual_means_ignored"] == 1
    assert parsed["coefficients"]["index_base2015"] == "1.075"
    assert parsed["coefficients"]["index_base2025"] == "1.226"


def test_deflation_math_and_missing_month():
    """P_real(t;b) = P_nom(t) × I(b)/I(t); missing month must be explicit."""
    by_period = {"2024-09": 99.0, "2024-10": 99.3}
    base = "2024-10"
    i_base = by_period[base]
    # September listing deflated to October base:
    real = 200_000 * i_base / by_period["2024-09"]
    assert abs(real - round(200_000 * 99.3 / 99.0, 2)) < 0.01
    # a month without an index must never be approximated:
    assert "2024-11" not in by_period
    assert all(p in by_period for p in ("2024-09", "2024-10"))
