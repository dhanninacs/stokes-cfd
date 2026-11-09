"""Surrogate models for the response-surface study type (ADR-0016): polynomial (least squares and
ridge), radial basis functions and Gaussian processes, with k-fold cross-validation and
predicted-versus-observed data.

A surrogate maps design points ``(n, d)`` to one response. ``fit(x, y)`` scales the inputs to the
unit cube first (from the variable bounds when they are given, else from the data), ``predict(x)``
returns a ``Prediction`` whose ``std`` is ``None`` for models without an uncertainty estimate
(radial basis functions). Several responses mean several surrogates, one per column; ``fit_table``
builds one from a ``DesignTable`` and drops the failed rows. Categorical variables enter as their
index, so a surrogate treats them as ordinal.

Import rule: numpy at module level; scipy lazily; scikit-learn lazily and only for the Gaussian
process, which raises ``MissingDependency`` when the ``explore`` extra is not installed.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from itertools import combinations_with_replacement
from typing import Any, ClassVar, Self

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore._imports import require, scipy_module
from stokes.explore.arrays import Floats, Ints, Json
from stokes.explore.errors import InvalidInput, SurrogateError
from stokes.explore.surrogate_base import (
    Prediction,
    ScaledSurrogate,
    Surrogate,
    merge_duplicates,
)
from stokes.explore.table import DesignTable
from stokes.explore.validation import (
    CrossValidation,
    PredictedObserved,
    cross_validate,
    predicted_vs_observed,
    r2_score,
    rmse,
)

__all__ = [
    "SURROGATE_KINDS",
    "CrossValidation",
    "GaussianProcessSurrogate",
    "PolynomialSurrogate",
    "PredictedObserved",
    "Prediction",
    "RBFSurrogate",
    "Surrogate",
    "cross_validate",
    "fit_surrogate",
    "fit_table",
    "make_surrogate",
    "predicted_vs_observed",
    "r2_score",
    "rmse",
]

_RBF_KERNELS = (
    "linear",
    "thin_plate_spline",
    "cubic",
    "quintic",
    "multiquadric",
    "inverse_multiquadric",
    "inverse_quadratic",
    "gaussian",
)
_SCALED_RBF_KERNELS = _RBF_KERNELS[4:]  # kernels with a shape parameter epsilon
_GP_KERNELS = ("matern52", "matern32", "rbf")


def _exponents(d: int, degree: int) -> Ints:
    """Exponent vectors of every monomial of total degree <= ``degree`` in ``d`` variables,
    ordered by degree (so each term's parent, one degree lower, comes first)."""
    rows = [np.zeros(d, dtype=np.int64)]
    for k in range(1, degree + 1):
        for combo in combinations_with_replacement(range(d), k):
            exponent = np.zeros(d, dtype=np.int64)
            for index in combo:
                exponent[index] += 1
            rows.append(exponent)
    return np.array(rows, dtype=np.int64)


def _parents(terms: Ints) -> tuple[Ints, Ints]:
    """For each term, the index of the term it extends and the variable it multiplies."""
    index = {tuple(int(v) for v in row): i for i, row in enumerate(terms)}
    parent = np.zeros(len(terms), dtype=np.int64)
    variable = np.zeros(len(terms), dtype=np.int64)
    for i, row in enumerate(terms):
        if i == 0:
            continue
        j = int(np.flatnonzero(row)[-1])
        smaller = row.copy()
        smaller[j] -= 1
        parent[i], variable[i] = index[tuple(int(v) for v in smaller)], j
    return parent, variable


def _features(coded: Floats, terms: Ints) -> Floats:
    parent, variable = _parents(terms)
    phi = np.empty((coded.shape[0], len(terms)))
    phi[:, 0] = 1.0
    for t in range(1, len(terms)):
        phi[:, t] = phi[:, parent[t]] * coded[:, variable[t]]
    return phi


def _pinv_symmetric(matrix: Floats) -> Floats:
    """Pseudo-inverse of a symmetric positive semi-definite matrix through its eigenvalues,
    several times cheaper than the SVD route and just as safe for a Gram matrix."""
    values, vectors = np.linalg.eigh(matrix)
    cutoff = max(float(values.max()), 0.0) * matrix.shape[0] * float(np.finfo(np.float64).eps)
    keep = values > cutoff
    inverse = np.zeros_like(values)
    inverse[keep] = 1.0 / values[keep]
    return np.asarray((vectors * inverse) @ vectors.T, dtype=np.float64)


class PolynomialSurrogate(ScaledSurrogate):
    """Polynomial response surface of total degree 1 to 3, all cross terms included.

    Fitted by least squares on inputs coded to [-1, 1]; ``ridge`` > 0 adds a Tikhonov penalty on
    every coefficient except the intercept and allows more terms than points. The standard
    deviation of a prediction is the standard error of the fitted mean (residual variance times
    the leverage of the point); it is ``None`` when the fit has no residual degrees of freedom,
    as in an exactly determined fit.
    """

    kind: ClassVar[str] = "polynomial"

    def __init__(self, degree: int = 2, ridge: float = 0.0) -> None:
        super().__init__()
        if degree not in (1, 2, 3):
            raise InvalidInput(f"polynomial degree must be 1, 2 or 3, got {degree!r}")
        if not (math.isfinite(ridge) and ridge >= 0.0):
            raise InvalidInput(f"ridge must be >= 0, got {ridge!r}")
        self.degree, self.ridge = int(degree), float(ridge)
        self.terms: Ints = np.zeros((0, 0), dtype=np.int64)
        self.coefficients: Floats = np.zeros(0)  # in coded coordinates, one per term
        self._cov: Floats = np.zeros((0, 0))
        self._sigma2 = math.nan

    def options(self) -> dict[str, Json]:
        return {"degree": self.degree, "ridge": self.ridge}

    def clone(self) -> Self:
        return type(self)(self.degree, self.ridge)

    def term_labels(self, names: Sequence[str] | None = None) -> list[str]:
        """Human-readable monomials such as ``1``, ``x1``, ``x1*x2``, ``x2^2``."""
        labels = names if names is not None else [f"x{i + 1}" for i in range(self._d)]
        out = []
        for row in self.terms:
            parts = [
                labels[j] if e == 1 else f"{labels[j]}^{e}" for j, e in enumerate(row) if e > 0
            ]
            out.append("*".join(parts) if parts else "1")
        return out

    def fit(self, x: ArrayLike, y: ArrayLike, *, bounds: ArrayLike | None = None) -> Self:
        unit, values = self._prepare(x, y, bounds)
        n, d = unit.shape
        terms = _exponents(d, self.degree)
        p = len(terms)
        if self.ridge == 0.0 and n < p:
            raise SurrogateError(
                f"a degree-{self.degree} polynomial in {d} variables has {p} terms but there are "
                f"only {n} points: lower the degree, add points or set ridge > 0",
                details={"terms": p, "points": n},
            )
        phi = _features(2.0 * unit - 1.0, terms)
        penalty = np.full(p, self.ridge)
        penalty[0] = 0.0
        gram = phi.T @ phi
        inverse = _pinv_symmetric(gram + np.diag(penalty))
        # normal equations through the eigen-decomposition (about five times cheaper than an SVD
        # least-squares at 1000 points and 286 terms), with one step of iterative refinement
        # that recovers the digits the squared condition number costs
        rhs = phi.T @ values
        beta = inverse @ rhs
        beta += inverse @ (rhs - gram @ beta - penalty * beta)
        residual = values - phi @ beta
        rss = float(residual @ residual)
        effective = float(np.sum((phi @ inverse) * phi))
        dof = n - effective
        self.terms, self.coefficients = terms, beta
        self._cov = inverse @ gram @ inverse
        self._sigma2 = rss / dof if dof > 1e-9 else math.nan
        total = float(np.sum((values - values.mean()) ** 2))
        self.diagnostics = {
            "n_train": n,
            "n_terms": p,
            "rmse_train": math.sqrt(rss / n),
            "r2_train": 1.0 - rss / total if total > 0.0 else math.nan,
            "residual_dof": dof,
        }
        return self

    def predict(self, x: ArrayLike) -> Prediction:
        phi = _features(2.0 * self._scale(x) - 1.0, self.terms)
        mean = phi @ self.coefficients
        if not math.isfinite(self._sigma2):
            return Prediction(mean, None)
        variance = self._sigma2 * np.sum((phi @ self._cov) * phi, axis=1)
        return Prediction(mean, np.sqrt(np.maximum(variance, 0.0)))


class RBFSurrogate(ScaledSurrogate):
    """Radial basis function interpolation (``scipy.interpolate.RBFInterpolator``).

    The default thin-plate spline with a linear polynomial tail interpolates the training points
    exactly; ``smoothing`` > 0 relaxes that for noisy data. Kernels with a shape parameter use
    ``epsilon`` (default ``max(1, n ** (1/d))``, the inverse of a typical point spacing in the
    unit cube). There is no uncertainty estimate. Identical points are merged by averaging.
    """

    kind: ClassVar[str] = "rbf"

    def __init__(
        self,
        kernel: str = "thin_plate_spline",
        smoothing: float = 0.0,
        degree: int | None = None,
        epsilon: float | None = None,
        neighbors: int | None = None,
    ) -> None:
        super().__init__()
        if kernel not in _RBF_KERNELS:
            raise InvalidInput(f"unknown RBF kernel {kernel!r}; known: {_RBF_KERNELS}")
        if not (math.isfinite(smoothing) and smoothing >= 0.0):
            raise InvalidInput(f"smoothing must be >= 0, got {smoothing!r}")
        self.kernel, self.smoothing, self.degree = kernel, float(smoothing), degree
        self.epsilon, self.neighbors = epsilon, neighbors
        self._model: Any = None  # Any: scipy RBFInterpolator

    def options(self) -> dict[str, Json]:
        return {
            "kernel": self.kernel,
            "smoothing": self.smoothing,
            "degree": self.degree,
            "epsilon": self.epsilon,
            "neighbors": self.neighbors,
        }

    def clone(self) -> Self:
        return type(self)(self.kernel, self.smoothing, self.degree, self.epsilon, self.neighbors)

    def fit(self, x: ArrayLike, y: ArrayLike, *, bounds: ArrayLike | None = None) -> Self:
        unit, values = self._prepare(x, y, bounds)
        unit, values, merged = merge_duplicates(unit, values)
        n, d = unit.shape
        epsilon = self.epsilon
        if epsilon is None and self.kernel in _SCALED_RBF_KERNELS:
            epsilon = max(1.0, n ** (1.0 / d))
        interpolate = scipy_module("scipy.interpolate")
        try:
            self._model = interpolate.RBFInterpolator(
                unit,
                values,
                neighbors=self.neighbors,
                smoothing=self.smoothing,
                kernel=self.kernel,
                epsilon=1.0 if epsilon is None else epsilon,
                degree=self.degree,
            )
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise SurrogateError(f"the RBF fit failed: {exc}") from exc
        self.diagnostics = {"n_train": n, "n_merged": merged, "kernel": self.kernel}
        return self

    def predict(self, x: ArrayLike) -> Prediction:
        unit = self._scale(x)
        return Prediction(np.asarray(self._model(unit), dtype=np.float64), None)


class GaussianProcessSurrogate(ScaledSurrogate):
    """Gaussian-process regression (scikit-learn): an amplitude times an anisotropic Matern or
    squared-exponential kernel on the unit cube, hyper-parameters by marginal likelihood with
    ``restarts`` random restarts seeded by ``seed``, outputs standardised. With ``warm_start`` a
    refit of the same object starts from the previous optimum and skips the restarts, which makes
    the repeated fits of a surrogate-assisted loop an order of magnitude cheaper.

    ``noise`` is the observation-noise variance relative to the response variance: ``0`` (the
    default) interpolates, apart from a tiny jitter that is raised automatically when the
    covariance is not positive definite (see ``diagnostics["nugget"]``); ``None`` fits a noise
    level. Predictions carry the posterior standard deviation.
    """

    kind: ClassVar[str] = "gp"

    def __init__(
        self,
        kernel: str = "matern52",
        noise: float | None = 0.0,
        restarts: int = 2,
        seed: int = 0,
        warm_start: bool = False,
    ) -> None:
        super().__init__()
        if kernel not in _GP_KERNELS:
            raise InvalidInput(f"unknown GP kernel {kernel!r}; known: {_GP_KERNELS}")
        if noise is not None and not (math.isfinite(noise) and noise >= 0.0):
            raise InvalidInput(f"noise must be >= 0 or None, got {noise!r}")
        if restarts < 0:
            raise InvalidInput(f"restarts must be >= 0, got {restarts!r}")
        self.kernel, self.noise, self.restarts, self.seed = kernel, noise, int(restarts), int(seed)
        self.warm_start = bool(warm_start)
        self._model: Any = None  # Any: sklearn GaussianProcessRegressor

    def options(self) -> dict[str, Json]:
        return {
            "kernel": self.kernel,
            "noise": self.noise,
            "restarts": self.restarts,
            "seed": self.seed,
            "warm_start": self.warm_start,
        }

    def clone(self) -> Self:
        return type(self)(self.kernel, self.noise, self.restarts, self.seed, self.warm_start)

    def _build_kernel(self, d: int) -> Any:  # Any: sklearn kernel
        kernels = require(
            "sklearn.gaussian_process.kernels", feature="the Gaussian-process surrogate"
        )
        scale = np.ones(d)
        if self.kernel == "rbf":
            shape = kernels.RBF(scale, length_scale_bounds=(1e-2, 1e2))
        else:
            nu = 2.5 if self.kernel == "matern52" else 1.5
            shape = kernels.Matern(scale, length_scale_bounds=(1e-2, 1e2), nu=nu)
        kernel = kernels.ConstantKernel(1.0, (1e-3, 1e5)) * shape
        if self.noise is None:
            kernel = kernel + kernels.WhiteKernel(1e-5, (1e-10, 1e-1))
        return kernel

    def fit(self, x: ArrayLike, y: ArrayLike, *, bounds: ArrayLike | None = None) -> Self:
        gp_module = require("sklearn.gaussian_process", feature="the Gaussian-process surrogate")
        unit, values = self._prepare(x, y, bounds)
        unit, values, merged = merge_duplicates(unit, values)
        if unit.shape[0] < 2:
            raise SurrogateError("a Gaussian process needs at least two distinct points")
        nugget = max(self.noise or 0.0, 1e-10)
        messages: list[str] = []
        warm = (
            self.warm_start
            and self._model is not None
            and self._model.X_train_.shape[1] == unit.shape[1]
        )
        for _ in range(7):
            model = gp_module.GaussianProcessRegressor(
                kernel=self._model.kernel_ if warm else self._build_kernel(unit.shape[1]),
                alpha=nugget,
                normalize_y=True,
                n_restarts_optimizer=0 if warm else self.restarts,
                random_state=self.seed,
            )
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    model.fit(unit, values)
            except np.linalg.LinAlgError:
                nugget *= 10.0
                continue
            messages = sorted({str(w.message)[:160] for w in caught})
            break
        else:
            raise SurrogateError(
                "the Gaussian-process covariance is singular; remove near-duplicate points"
            )
        self._model = model
        self.diagnostics = {
            "n_train": int(unit.shape[0]),
            "n_merged": merged,
            "kernel": str(model.kernel_),
            "log_marginal_likelihood": float(model.log_marginal_likelihood_value_),
            "nugget": nugget,
            "warnings": list(messages),
        }
        return self

    def predict(self, x: ArrayLike) -> Prediction:
        unit = self._scale(x)
        with warnings.catch_warnings():
            # sklearn clips tiny negative variances to zero and says so; at a training point of an
            # interpolating model that is the expected rounding, not a problem
            warnings.filterwarnings("ignore", message="Predicted variances smaller than 0")
            mean, std = self._model.predict(unit, return_std=True)
        return Prediction(np.asarray(mean, dtype=np.float64), np.asarray(std, dtype=np.float64))


# ---------------------------------------------------------------- factory and table helpers

_KIND_ALIASES = {
    "polynomial": "polynomial",
    "poly": "polynomial",
    "rbf": "rbf",
    "radial_basis": "rbf",
    "gp": "gp",
    "gaussian_process": "gp",
    "kriging": "gp",
}
SURROGATE_KINDS = ("polynomial", "rbf", "gp")


def make_surrogate(kind: str, **options: Any) -> Surrogate:  # Any: hyper-parameters of any kind
    """An unfitted surrogate by kind (``polynomial``, ``rbf`` or ``gp``) and hyper-parameters."""
    canonical = _KIND_ALIASES.get(kind.lower().replace("-", "_").replace(" ", "_"))
    try:
        if canonical == "polynomial":
            return PolynomialSurrogate(**options)
        if canonical == "rbf":
            return RBFSurrogate(**options)
        if canonical == "gp":
            return GaussianProcessSurrogate(**options)
    except TypeError as exc:
        raise InvalidInput(f"bad options for a {canonical} surrogate: {exc}") from exc
    raise InvalidInput(
        f"unknown surrogate kind {kind!r}; known: {SURROGATE_KINDS}",
        details={"known": list(SURROGATE_KINDS)},
    )


def fit_surrogate(
    kind: str,
    x: ArrayLike,
    y: ArrayLike,
    *,
    bounds: ArrayLike | None = None,
    **options: Any,  # Any: hyper-parameters of any kind
) -> Surrogate:
    """Build a surrogate by kind and fit it."""
    return make_surrogate(kind, **options).fit(x, y, bounds=bounds)


def fit_table(
    kind: str,
    table: DesignTable,
    response: str,
    **options: Any,  # Any: hyper-parameters of any kind
) -> Surrogate:
    """Fit one response of a design table, ignoring failed rows, scaled by the variable bounds."""
    if table.responses is None:
        raise SurrogateError("the table has no responses to fit")
    keep = table.valid_rows()
    y = table.column(response)
    return fit_surrogate(kind, table.points[keep], y[keep], bounds=table.space.bounds, **options)
