from app.core.enums import MarketRegime
from app.market_engine.demography import household_analytics
from app.market_engine.depth import (
    active_supply_ratio,
    months_of_supply,
    stock_turnover,
    time_on_market_summary,
    visible_turnover,
)
from app.market_engine.prices import aggregate_price_per_m2, real_price, yoy_change
from app.market_engine.regime import classify, percentile_rank, zscore


def test_depth_metrics_and_none_safety():
    assert stock_turnover(2000, 100000) == 0.02
    assert visible_turnover(2000, 4000) == 0.5
    assert active_supply_ratio(4000, 100000) == 0.04
    assert months_of_supply(4000, 200) == 20
    # zero / missing denominators => None, never a ZeroDivisionError
    assert stock_turnover(100, 0) is None
    assert visible_turnover(None, 10) is None


def test_active_supply_is_visible_supply_not_total_supply():
    # A listing count far below the dwelling stock must yield a small ratio.
    assert active_supply_ratio(4000, 100000) < 0.1


def test_time_on_market_summary_is_none_safe():
    s = time_on_market_summary([10, 20, 30, 40])
    assert s.count == 4
    assert s.median == 25
    assert s.kind == "observed_time_on_market"
    empty = time_on_market_summary([])
    assert empty.count == 0 and empty.median is None


def test_real_price_deflation():
    # Nominal 2000 in a period with CPI 121, base CPI 110 (2025) -> lower real.
    rp = real_price(2000, cpi_t=121, cpi_base=110)
    assert rp is not None and rp < 2000
    assert abs(rp - 2000 * 110 / 121) < 1e-6
    assert real_price(2000, None, 121) is None


def test_yoy_and_aggregate():
    assert abs(yoy_change(110, 100) - 0.10) < 1e-9
    agg = aggregate_price_per_m2([1000, 2000, 3000])
    assert agg["median"] == 2000 and agg["mean"] == 2000
    assert aggregate_price_per_m2([])["median"] is None


def test_household_analytics():
    a = household_analytics(households_t=1050, households_t1=1000, households_t2=980)
    assert a.household_change == 50
    assert a.household_acceleration == 30  # 50 - 20
    assert a.two_year_net_household_change == 70
    assert a.two_year_average_annual_change == 35


def test_zscore_and_percentile_rank():
    hist = [1, 2, 3, 4, 5]
    assert zscore(5, hist) > 0
    assert zscore(3, hist) == 0
    assert zscore(None, hist) is None
    assert zscore(3, [1, 2]) is None  # not enough history
    assert percentile_rank(3, hist) == 0.6


def test_regime_classification_expansion_and_contraction():
    expanding = {
        "price_momentum": [0.01, 0.015, 0.02, 0.025, 0.03, 0.035],
        "transaction_momentum": [0.005, 0.01, 0.012, 0.018, 0.02, 0.025],
        "liquidity": [0.20, 0.22, 0.25, 0.28, 0.30, 0.34],
        "supply": [120, 118, 115, 110, 105, 100],   # falling supply = supportive
        "credit": [0.04, 0.038, 0.035, 0.032, 0.030, 0.028],  # falling rates
    }
    comps_in = {k: v[-1] for k, v in expanding.items()}
    res_in = classify(comps_in, expanding)
    assert res_in.regime == MarketRegime.EXPANSION
    assert res_in.confidence > 0.3

    contracting = {
        "price_momentum": [0.03, 0.02, 0.01, 0.0, -0.01, -0.03],
        "transaction_momentum": [0.02, 0.01, 0.0, -0.01, -0.02, -0.04],
        "liquidity": [0.33, 0.30, 0.28, 0.25, 0.22, 0.18],
        "supply": [100, 108, 116, 124, 132, 140],
        "credit": [0.02, 0.025, 0.03, 0.035, 0.04, 0.045],
    }
    comps_out = {k: v[-1] for k, v in contracting.items()}
    res_out = classify(comps_out, contracting)
    assert res_out.regime in (MarketRegime.CONTRACTION, MarketRegime.CRISIS)
    assert res_out.score < 0


def test_regime_insufficient_data_is_honest():
    res = classify({"price_momentum": 0.03}, {})
    assert res.coverage == 0.0
    assert res.confidence == 0.0
