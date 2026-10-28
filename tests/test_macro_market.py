"""Macro panel tests (§36-37): publication status, derived series, absorption."""

from __future__ import annotations

import pytest

from app.core.enums import DataStatus
from app.market_engine.macro import (
    INDICATORS,
    absorption_rate,
    build_panel,
    growth_rate,
    household_acceleration,
    household_growth,
    market_context,
    not_yet_published,
    real_price,
)


def panel_rows() -> list[dict]:
    return [
        {"indicator": "price_index", "period": "2025-01", "value": 100.0, "unit": "index"},
        {"indicator": "price_index", "period": "2025-02", "value": 102.0, "unit": "index"},
        # The latest quarter is simply not out yet.
        {"indicator": "price_index", "period": "2025-03", "value": None,
         "status": DataStatus.NOT_YET_PUBLISHED.value},
        {"indicator": "households", "period": "2023", "value": 1000.0},
        {"indicator": "households", "period": "2024", "value": 1030.0},
        {"indicator": "households", "period": "2025", "value": 1065.0},
        {"indicator": "inflation", "period": "2025-01", "value": 1.10},
        {"indicator": "mortgage_rate", "period": "2025-01", "value": 3.4},
    ]


def test_unpublished_period_is_not_a_zero_and_not_interpolated():
    panel = build_panel(panel_rows())
    latest = panel.latest("price_index")
    assert latest.period == "2025-03"
    assert latest.value is None
    assert latest.status is DataStatus.NOT_YET_PUBLISHED
    assert latest.published is False
    assert panel.values("price_index") == [100.0, 102.0, None]
    assert [o.value for o in panel.published_series("price_index")] == [100.0, 102.0]


def test_missing_value_without_a_status_is_unavailable_not_observed():
    panel = build_panel([{"indicator": "transactions", "period": "2025-Q3", "value": None}])
    observation = panel.latest("transactions")
    assert observation.status is DataStatus.UNAVAILABLE
    assert observation.value is None


def test_not_yet_published_helper():
    observation = not_yet_published("transactions", "2025-Q4", source="OMI")
    assert observation.status is DataStatus.NOT_YET_PUBLISHED
    assert observation.value is None
    assert observation.to_dict()["published"] is False


def test_household_growth_and_acceleration():
    levels = [1000.0, 1030.0, 1065.0]
    growth = household_growth(levels)
    assert growth == [30.0, 35.0]
    acceleration = household_acceleration(levels)
    assert acceleration == [5.0]


def test_derived_series_never_bridge_a_gap():
    levels = [1000.0, None, 1065.0]
    assert household_growth(levels) == [None, None]
    assert household_acceleration(levels) == [None]
    assert growth_rate([100.0, None, 110.0]) == [None, None]


def test_real_price_and_absorption_rate():
    assert real_price(110.0, 1.10) == pytest.approx(100.0)
    assert real_price(None, 1.10) is None
    assert real_price(110.0, 0) is None
    assert absorption_rate(30.0, 300.0) == pytest.approx(0.1)
    assert absorption_rate(30.0, 0) is None
    assert absorption_rate(None, 300.0) is None


def test_market_context_reports_publication_state_for_every_indicator():
    panel = build_panel(panel_rows())
    context = market_context(panel)

    for indicator in INDICATORS:
        assert indicator in context
    assert context["price_index"]["status"] == DataStatus.NOT_YET_PUBLISHED.value
    assert context["price_index"]["published"] is False
    assert context["households"]["status"] == DataStatus.OBSERVED.value
    assert context["employment"]["status"] == DataStatus.UNAVAILABLE.value

    derived = context["derived"]
    assert derived["household_growth"] == [30.0, 35.0]
    assert derived["household_acceleration"] == [5.0]
    # price_growth stops at the gap rather than assuming 0.
    assert derived["price_growth"] == [pytest.approx(0.02), None]
    assert any("never interpolated" in note for note in context["notes"])


def test_panel_series_is_chronological():
    panel = build_panel(
        [
            {"indicator": "x", "period": "2025-03", "value": 3},
            {"indicator": "x", "period": "2025-01", "value": 1},
            {"indicator": "x", "period": "2025-02", "value": 2},
        ]
    )
    assert [o.period for o in panel.series("x")] == ["2025-01", "2025-02", "2025-03"]
    assert panel.at("x", "2025-02").value == 2
