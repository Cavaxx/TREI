from app.core.enums import Evidence
from app.models.affordability import affordability, monthly_payment
from app.models.forecast import (
    directional_probabilities,
    linear_trend_forecast,
    run_backtest,
    walk_forward_splits,
)
from app.models.scenarios import ScenarioInput, run_scenario


def test_walk_forward_never_trains_on_future():
    folds = list(walk_forward_splits(10, min_train=6))
    assert folds
    for f in folds:
        assert max(f.train_index) < f.test_index


def test_backtest_metrics_are_computed():
    series = [100, 102, 101, 105, 107, 106, 110, 112, 111, 115]
    res = run_backtest(series, linear_trend_forecast, model_name="linear", min_train=5)
    assert res.n_folds > 0
    assert res.mae >= 0
    assert res.mae <= 5 * res.naive_mae + 1  # sanity: not absurd
    assert res.to_dict()["model"] == "linear"


def test_directional_probabilities_insufficient_evidence():
    res = directional_probabilities([100, 101, 102, 103], horizon=1, min_obs=24)
    assert res.evidence == Evidence.INSUFFICIENT_EVIDENCE
    assert "p_up" not in res.to_dict()


def test_directional_probabilities_sufficient_and_normalized():
    series = [100 * (1.03 ** i) for i in range(120)]  # steadily rising
    res = directional_probabilities(series, horizon=12, flat_band=0.02, min_obs=24)
    assert res.evidence == Evidence.SUFFICIENT
    d = res.to_dict()
    assert abs(d["p_up"] + d["p_flat"] + d["p_down"] - 1.0) < 1e-6
    assert d["p_up"] > d["p_down"]


def test_scenario_rate_shock_lowers_prices_and_volumes():
    out = run_scenario(ScenarioInput(mortgage_rate_pp=1.0))
    assert out.price_impact_pct < 0
    assert out.transaction_impact_pct < out.price_impact_pct  # volumes fall more
    probs = out.regime_probabilities
    assert abs(sum(probs.values()) - 1.0) < 1e-6


def test_scenario_positive_demography_supports_market():
    out = run_scenario(ScenarioInput(household_growth_pct=2.0))
    assert out.price_impact_pct > 0


def test_mortgage_math():
    # Zero-rate degrades to straight division.
    assert monthly_payment(120000, 0.0, 10) == 1000
    mp = monthly_payment(200000, 0.035, 25)
    assert 900 < mp < 1200
    res = affordability(300000, 60000, 0.035, 25, monthly_net_income=3500)
    assert res.loan_amount == 240000
    assert res.to_dict()["monthly_payment"] > 0
