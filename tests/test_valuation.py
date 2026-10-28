from app.core.enums import Confidence, ValuationClass
from app.db.seed import generate_demo_listings
from app.models.comparables import find_comparables
from app.models.hedonic import HedonicModel
from app.models.valuation import value_property


def _pool():
    return [r for r in generate_demo_listings(n_per_comune=60)]


def test_hedonic_fit_predict_and_interval():
    rows = _pool()
    model = HedonicModel().fit(rows, target="price")
    assert model.coefficients is not None
    assert model.resid_std_ is not None and model.resid_std_ > 0
    preds = model.predict(rows[:5])
    assert (preds > 0).all()
    low, high = model.prediction_interval(rows[:5])
    assert (low < high).all()
    table = model.coefficients_table()
    assert table and "feature" in table[0]


def test_comparables_prefer_same_comune():
    pool = _pool()
    subject = next(r for r in pool if r["comune"] == "Trento")
    comps = find_comparables(subject, pool, top_n=10)
    assert comps
    assert comps[0].similarity >= comps[-1].similarity
    # top comparable should share the comune (same-address/geo weighting)
    assert comps[0].comune == "Trento"


def test_valuation_overpriced_listing_flags_over_market():
    pool = _pool()
    subject = dict(next(r for r in pool if r["comune"] == "Trento"))
    # artificially double the asking price -> should look OVER_MARKET
    subject["price"] = subject["price"] * 2
    val = value_property(subject, pool)
    assert val.classification == ValuationClass.OVER_MARKET
    assert val.deviation > 0
    assert val.prediction_interval_low < val.prediction_interval_high
    assert val.to_dict()["method"] in {"comparables_only", "hedonic_plus_comparables"}


def test_valuation_underpriced_listing_flags_under_market():
    pool = _pool()
    subject = dict(next(r for r in pool if r["comune"] == "Trento"))
    subject["price"] = subject["price"] * 0.5
    val = value_property(subject, pool)
    assert val.classification == ValuationClass.UNDER_MARKET


def test_valuation_without_comparables_is_honest():
    subject = {"comune": "Nowhere", "surface_m2": 100.0, "price": 100000.0, "lat": 0.0, "lon": 0.0}
    val = value_property(subject, [])
    assert val.comparables_count == 0
    assert val.method == "insufficient_comparables"
    assert val.confidence == Confidence.LOW
