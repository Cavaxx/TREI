"""Forecasting with strict leakage prevention (sections 26-28).

Two responsibilities:
  1. Walk-forward backtesting harness + accuracy metrics (MAE/RMSE/MAPE/
     directional accuracy / Brier score).
  2. Distribution-free directional probabilities P(up/flat/down) for a horizon,
     derived from the *empirical* distribution of overlapping historical
     changes. When history is too thin we return INSUFFICIENT_EVIDENCE rather
     than inventing a number.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from app.core.enums import Evidence


@dataclass
class WalkForwardFold:
    train_index: list[int]
    test_index: int


def walk_forward_splits(
    n: int, min_train: int = 6, step: int = 1
) -> Iterator[WalkForwardFold]:
    """Yield expanding-window folds: train[0..t-1] -> test[t].

    This is the ONLY correct way to evaluate a time series here: we never train
    on data that is chronologically after the test point.
    """
    t = min_train
    while t < n:
        yield WalkForwardFold(train_index=list(range(0, t)), test_index=t)
        t += step


def mae(actual: list[float], predicted: list[float]) -> float:
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def rmse(actual: list[float], predicted: list[float]) -> float:
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))


def mape(actual: list[float], predicted: list[float]) -> float:
    pairs = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not pairs:
        return float("nan")
    return sum(abs((a - p) / a) for a, p in pairs) / len(pairs)


def directional_accuracy(actual: list[float], predicted: list[float]) -> float:
    """Agreement on the SIGN of changes. Needs at least two points."""
    if len(actual) < 2:
        return float("nan")
    correct = 0
    total = 0
    for i in range(1, len(actual)):
        a_dir = actual[i] - actual[i - 1]
        p_dir = predicted[i] - predicted[i - 1]
        if a_dir == 0 and p_dir == 0:
            continue
        total += 1
        if (a_dir >= 0) == (p_dir >= 0):
            correct += 1
    return correct / total if total else float("nan")


def brier_score(outcomes: list[int], probabilities: list[float]) -> float:
    """Mean squared error of probabilistic predictions for a binary event."""
    if not outcomes:
        return float("nan")
    return sum((p - o) ** 2 for o, p in zip(outcomes, probabilities)) / len(outcomes)


@dataclass
class BacktestResult:
    model: str
    n_folds: int
    mae: float
    rmse: float
    mape: float
    directional_accuracy: float
    naive_mae: float
    improves_on_naive: bool = False
    folds: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "n_folds": self.n_folds,
            "mae": round(self.mae, 4),
            "rmse": round(self.rmse, 4),
            "mape": round(self.mape, 4) if not math.isnan(self.mape) else None,
            "directional_accuracy": round(self.directional_accuracy, 4)
            if not math.isnan(self.directional_accuracy)
            else None,
            "naive_mae": round(self.naive_mae, 4),
            "improves_on_naive": self.improves_on_naive,
        }


def naive_forecast(history: list[float]) -> float:
    """Random-walk baseline: predict the last observed value."""
    return history[-1]


def linear_trend_forecast(history: list[float]) -> float:
    """OLS linear trend extrapolated one step ahead."""
    n = len(history)
    if n < 2:
        return history[-1]
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(history) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return history[-1]
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, history)) / denom
    intercept = my - slope * mx
    return intercept + slope * n


def run_backtest(
    series: list[float],
    predictor: Callable[[list[float]], float],
    model_name: str = "model",
    min_train: int = 6,
) -> BacktestResult:
    """Expanding-window walk-forward backtest of a one-step predictor."""
    actual: list[float] = []
    predicted: list[float] = []
    naive_pred: list[float] = []
    folds: list[dict] = []
    for fold in walk_forward_splits(len(series), min_train=min_train):
        hist = series[: fold.train_index[-1] + 1]
        y_true = series[fold.test_index]
        y_hat = predictor(hist)
        y_naive = naive_forecast(hist)
        actual.append(y_true)
        predicted.append(y_hat)
        naive_pred.append(y_naive)
        folds.append({"t": fold.test_index, "actual": y_true, "predicted": y_hat, "naive": y_naive})
    if not actual:
        return BacktestResult(model_name, 0, float("nan"), float("nan"), float("nan"), float("nan"), float("nan"))
    res = BacktestResult(
        model=model_name,
        n_folds=len(actual),
        mae=mae(actual, predicted),
        rmse=rmse(actual, predicted),
        mape=mape(actual, predicted),
        directional_accuracy=directional_accuracy(actual, predicted),
        naive_mae=mae(actual, naive_pred),
        folds=folds,
    )
    res.improves_on_naive = res.mae < res.naive_mae
    return res


@dataclass
class DirectionalProbabilities:
    horizon: int
    evidence: Evidence
    p_up: float | None = None
    p_flat: float | None = None
    p_down: float | None = None
    sample_size: int = 0
    flat_band: float | None = None
    note: str = ""

    def to_dict(self) -> dict:
        d = {
            "horizon": self.horizon,
            "evidence": self.evidence.value,
            "sample_size": self.sample_size,
        }
        if self.evidence is Evidence.SUFFICIENT:
            d.update(
                {
                    "p_up": round(self.p_up, 3),
                    "p_flat": round(self.p_flat, 3),
                    "p_down": round(self.p_down, 3),
                    "flat_band": self.flat_band,
                }
            )
        else:
            d["note"] = self.note or "insufficient history for a robust estimate"
        return d


def directional_probabilities(
    series: list[float],
    horizon: int,
    flat_band: float = 0.02,
    min_obs: int = 24,
) -> DirectionalProbabilities:
    """Empirical P(up/flat/down) over ``horizon`` steps.

    Distribution-free: we look at every overlapping ``horizon``-step relative
    change in history and count how often the outcome exceeded the flat band.
    Returns INSUFFICIENT_EVIDENCE if there are fewer than ``min_obs`` windows.

    These are conditional frequencies, NOT point forecasts (section 26).
    """
    n = len(series)
    changes: list[float] = []
    for i in range(0, n - horizon):
        base = series[i]
        fut = series[i + horizon]
        if base:
            changes.append(fut / base - 1.0)

    if len(changes) < min_obs:
        return DirectionalProbabilities(
            horizon=horizon,
            evidence=Evidence.INSUFFICIENT_EVIDENCE,
            sample_size=len(changes),
            flat_band=flat_band,
            note=f"only {len(changes)} overlapping windows (< {min_obs})",
        )

    up = sum(1 for c in changes if c > flat_band) / len(changes)
    down = sum(1 for c in changes if c < -flat_band) / len(changes)
    flat = 1.0 - up - down
    return DirectionalProbabilities(
        horizon=horizon,
        evidence=Evidence.SUFFICIENT,
        p_up=up,
        p_flat=flat,
        p_down=down,
        sample_size=len(changes),
        flat_band=flat_band,
    )

