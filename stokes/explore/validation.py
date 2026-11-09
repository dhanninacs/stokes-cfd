"""Accuracy measures for surrogates: error metrics, predicted-versus-observed data and k-fold
cross-validation (ADR-0016, response-surface studies).

``surrogate`` re-exports everything here, so callers can import it from either place. This module
needs the ``Surrogate`` protocol for typing only, which keeps the dependency one-way.

Import rule: numpy only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Floats, Ints, as_matrix, as_vector
from stokes.explore.errors import InvalidInput

if TYPE_CHECKING:
    from stokes.explore.surrogate_base import Surrogate


def r2_score(observed: ArrayLike, predicted: ArrayLike) -> float:
    """Coefficient of determination ``1 - SSE / SST`` (NaN when the observations are constant)."""
    y, f = as_vector(observed, name="observed"), as_vector(predicted, name="predicted")
    total = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - float(np.sum((y - f) ** 2)) / total if total > 0.0 else math.nan


def rmse(observed: ArrayLike, predicted: ArrayLike) -> float:
    """Root-mean-square error."""
    y, f = as_vector(observed, name="observed"), as_vector(predicted, name="predicted")
    return float(np.sqrt(np.mean((y - f) ** 2)))


@dataclass(frozen=True, eq=False)
class PredictedObserved:
    """Observed responses against a model's predictions: the data of a predicted-versus-observed
    plot (points near the diagonal are good) and the usual error measures."""

    observed: Floats
    predicted: Floats
    std: Floats | None = None

    @property
    def r2(self) -> float:
        return r2_score(self.observed, self.predicted)

    @property
    def rmse(self) -> float:
        return rmse(self.observed, self.predicted)

    @property
    def mae(self) -> float:
        return float(np.mean(np.abs(self.observed - self.predicted)))

    @property
    def max_error(self) -> float:
        return float(np.max(np.abs(self.observed - self.predicted)))


def predicted_vs_observed(model: Surrogate, x: ArrayLike, y: ArrayLike) -> PredictedObserved:
    """Compare a fitted model with observations at ``x``: training points show the goodness of
    fit, held-out points the real accuracy."""
    prediction = model.predict(x)
    return PredictedObserved(as_vector(y, name="y"), prediction.mean, prediction.std)


@dataclass(frozen=True, eq=False)
class CrossValidation:
    """k-fold cross-validation: every point is predicted by a model fitted without its fold.

    ``result`` holds the out-of-fold predictions against the observations (so ``r2`` is the
    predictive R-squared, often called Q2); ``fold_of`` gives each point's fold.
    """

    folds: int
    fold_of: Ints
    result: PredictedObserved

    @property
    def r2(self) -> float:
        return self.result.r2

    @property
    def rmse(self) -> float:
        return self.result.rmse

    @property
    def mae(self) -> float:
        return self.result.mae


def cross_validate(
    model: Surrogate,
    x: ArrayLike,
    y: ArrayLike,
    *,
    folds: int = 5,
    seed: int = 0,
    bounds: ArrayLike | None = None,
) -> CrossValidation:
    """k-fold cross-validation of a surrogate, cloned (unfitted) for every fold. Folds are a
    seeded random partition; ``folds == n`` is leave-one-out. ``bounds`` default to the data
    range, shared by all folds."""
    points = as_matrix(x, name="x", finite=True)
    values = as_vector(y, name="y", finite=True)
    n = points.shape[0]
    if values.shape[0] != n:
        raise InvalidInput(f"{n} points but {values.shape[0]} responses")
    if not 2 <= folds <= n:
        raise InvalidInput(f"folds must be between 2 and the number of points ({n}), got {folds}")
    if bounds is None:  # one scaling for every fold, so the folds are comparable
        bounds = np.column_stack([points.min(axis=0), points.max(axis=0)])
    order = np.random.default_rng(seed).permutation(n)
    fold_of = np.empty(n, dtype=np.int64)
    predicted = np.empty(n)
    std = np.empty(n)
    has_std = True
    for k, held_out in enumerate(np.array_split(order, folds)):
        fold_of[held_out] = k
        train = np.setdiff1d(order, held_out, assume_unique=True)
        fitted = model.clone().fit(points[train], values[train], bounds=bounds)
        prediction = fitted.predict(points[held_out])
        predicted[held_out] = prediction.mean
        if prediction.std is None:
            has_std = False
        else:
            std[held_out] = prediction.std
    return CrossValidation(
        folds, fold_of, PredictedObserved(values, predicted, std if has_std else None)
    )
