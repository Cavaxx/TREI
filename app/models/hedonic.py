"""Hedonic price model (interpretable baseline, section 21).

Implements a ridge-regularized linear regression on ``log(price)`` with
one-hot categorical features and standardized numeric features. Ridge (L2) is
used rather than plain OLS to keep the model stable when one-hot categories
are sparse; it is the interpretable end of the spectrum. Gradient boosting is
left behind the optional ``ml`` extra and is compared offline (section 21).

Everything is implemented with numpy so the model runs in the minimal
environment and is fully reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Feature schema for the MVP. kind is "numeric" or "categorical".
DEFAULT_FEATURES: list[tuple[str, str]] = [
    ("surface_m2", "numeric"),
    ("rooms", "numeric"),
    ("bathrooms", "numeric"),
    ("floor", "numeric"),
    ("has_elevator", "numeric"),
    ("has_garage", "numeric"),
    ("has_garden", "numeric"),
    ("has_terrace", "numeric"),
    ("comune", "categorical"),
    ("condition", "categorical"),
    ("energy_class", "categorical"),
]


@dataclass
class HedonicModel:
    features: list[tuple[str, str]] = field(default_factory=lambda: list(DEFAULT_FEATURES))
    alpha: float = 1.0
    coefficients: np.ndarray | None = None
    feature_names_: list[str] = field(default_factory=list)
    numeric_means_: dict[str, float] = field(default_factory=dict)
    numeric_stds_: dict[str, float] = field(default_factory=dict)
    categories_: dict[str, list] = field(default_factory=dict)
    resid_std_: float | None = None
    target_mean_: float | None = None
    n_train_: int = 0

    # --- design matrix ------------------------------------------------------
    def _fit_encoding(self, rows: list[dict]) -> None:
        self.numeric_means_.clear()
        self.numeric_stds_.clear()
        self.categories_.clear()
        for name, kind in self.features:
            if kind == "numeric":
                vals = [float(r[name]) for r in rows if r.get(name) is not None]
                if vals:
                    self.numeric_means_[name] = float(np.mean(vals))
                    std = float(np.std(vals))
                    self.numeric_stds_[name] = std if std > 0 else 1.0
                else:
                    self.numeric_means_[name] = 0.0
                    self.numeric_stds_[name] = 1.0
            else:
                cats = sorted({str(r.get(name)) for r in rows if r.get(name) is not None})
                self.categories_[name] = cats

    def _design(self, rows: list[dict], fit: bool) -> np.ndarray:
        cols: list[np.ndarray] = [np.ones(len(rows))]  # intercept
        names = ["intercept"]
        for name, kind in self.features:
            if kind == "numeric":
                mu = self.numeric_means_.get(name, 0.0)
                sd = self.numeric_stds_.get(name, 1.0)
                vec = np.array(
                    [
                        (float(r[name]) - mu) / sd if r.get(name) is not None else 0.0
                        for r in rows
                    ]
                )
                cols.append(vec)
                names.append(name)
            else:
                cats = self.categories_.get(name, [])
                for c in cats[:-1]:  # drop last category (reference level)
                    vec = np.array([1.0 if str(r.get(name)) == c else 0.0 for r in rows])
                    cols.append(vec)
                    names.append(f"{name}={c}")
        if fit:
            self.feature_names_ = names
        return np.column_stack(cols) if cols else np.empty((len(rows), 0))

    # --- fit / predict ------------------------------------------------------
    def fit(self, rows: list[dict], target: str = "price") -> HedonicModel:
        """Fit on ``log(target)``. Rows with a missing target are ignored."""
        data = [r for r in rows if r.get(target) not in (None, 0) and r.get(target) > 0]
        if not data:
            raise ValueError("no rows with a positive target to fit on")
        self._fit_encoding(data)
        x = self._design(data, fit=True)
        y = np.log(np.array([float(r[target]) for r in data]))
        self.target_mean_ = float(np.mean(y))
        self.n_train_ = len(data)

        # Ridge normal equations: (X'X + alpha*I) beta = X'y ; do not penalize
        # the intercept (first column).
        xtx = x.T @ x
        reg = self.alpha * np.eye(xtx.shape[0])
        reg[0, 0] = 0.0
        beta = np.linalg.solve(xtx + reg, x.T @ y)
        self.coefficients = beta

        resid = y - x @ beta
        # Degrees of freedom correction for the residual sigma.
        dof = max(len(data) - x.shape[1], 1)
        self.resid_std_ = float(np.sqrt(float(resid @ resid) / dof))
        return self

    def predict_log(self, rows: list[dict]) -> np.ndarray:
        if self.coefficients is None:
            raise RuntimeError("model is not fitted")
        x = self._design(rows, fit=False)
        return x @ self.coefficients

    def predict(self, rows: list[dict]) -> np.ndarray:
        """Predict price levels (exp of the log prediction)."""
        return np.exp(self.predict_log(rows))

    def prediction_interval(
        self, rows: list[dict], z: float = 1.96
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (low, high) price bounds using the residual sigma as a simple
        prediction interval (approximate; wide when data is thin)."""
        log_pred = self.predict_log(rows)
        sigma = self.resid_std_ or 0.0
        low = np.exp(log_pred - z * sigma)
        high = np.exp(log_pred + z * sigma)
        return low, high

    def coefficients_table(self) -> list[dict]:
        """Human-readable coefficients on the log scale (≈ relative effects)."""
        if self.coefficients is None:
            return []
        out = []
        for name, coef in zip(self.feature_names_, self.coefficients):
            out.append({"feature": name, "coef_log": float(coef)})
        return sorted(out, key=lambda d: abs(d["coef_log"]), reverse=True)

