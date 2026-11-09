"""Surrogates and their validation (stokes.explore.surrogate, validation)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from stokes.explore.errors import InvalidInput, MissingDependency, SurrogateError
from stokes.explore.functions import branin
from stokes.explore.sampling import latin_hypercube
from stokes.explore.surrogate import (
    SURROGATE_KINDS,
    GaussianProcessSurrogate,
    PolynomialSurrogate,
    RBFSurrogate,
    cross_validate,
    fit_surrogate,
    fit_table,
    make_surrogate,
    predicted_vs_observed,
    r2_score,
    rmse,
)
from tests.test_explore_support import block_packages


def quadratic(x: np.ndarray) -> np.ndarray:
    return (
        1.0
        + 2.0 * x[:, 0]
        - x[:, 1]
        + 0.5 * x[:, 2]
        + 0.3 * x[:, 0] ** 2
        - 0.7 * x[:, 1] * x[:, 2]
        + 0.2 * x[:, 2] ** 2
    )


@pytest.fixture(scope="module")
def branin_data() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """30 training points, 200 validation points and the bounds, all of Branin."""
    function = branin()
    train = latin_hypercube(function.variables, 30, seed=1).points
    test = latin_hypercube(function.variables, 200, seed=2).points
    bounds = function.problem().bounds
    return train, function.evaluate(train)[:, 0], test, function.evaluate(test)[:, 0], bounds


# ---------------------------------------------------------------- polynomial


def test_polynomial_recovers_a_quadratic_exactly() -> None:
    rng = np.random.default_rng(0)
    x = rng.uniform(-2.0, 2.0, size=(40, 3))
    model = PolynomialSurrogate(2).fit(x, quadratic(x))
    probe = rng.uniform(-2.0, 2.0, size=(25, 3))
    prediction = model.predict(probe)
    assert np.abs(prediction.mean - quadratic(probe)).max() < 1e-10
    assert (
        prediction.std is not None and prediction.std.max() < 1e-9
    )  # a perfect fit leaves no error
    assert model.diagnostics["r2_train"] == pytest.approx(1.0)
    assert model.diagnostics["n_terms"] == 10 and model.fitted


def test_polynomial_exactly_determined_fit_has_no_uncertainty_estimate() -> None:
    rng = np.random.default_rng(1)
    x = rng.random((6, 2))  # 6 points, 6 terms of a degree-2 polynomial in 2 variables
    y = 1.0 + x[:, 0] ** 2 + x[:, 0] * x[:, 1]
    prediction = PolynomialSurrogate(2).fit(x, y).predict(x)
    assert np.allclose(prediction.mean, y) and prediction.std is None


def test_polynomial_uncertainty_grows_away_from_the_data() -> None:
    rng = np.random.default_rng(2)
    x = rng.uniform(0.0, 1.0, size=(30, 1))
    y = 2.0 * x[:, 0] + rng.normal(0.0, 0.1, 30)
    model = PolynomialSurrogate(1).fit(x, y, bounds=[[-2.0, 3.0]])
    inside = model.predict([[0.5]]).std
    outside = model.predict([[3.0]]).std
    assert inside is not None and outside is not None and outside[0] > 3 * inside[0]


def test_polynomial_degrees_ridge_and_labels() -> None:
    rng = np.random.default_rng(3)
    x = rng.random((60, 2))
    y = 1.0 + x[:, 0] ** 3 - x[:, 0] * x[:, 1]
    errors = [np.abs(PolynomialSurrogate(d).fit(x, y).predict(x).mean - y).max() for d in (1, 2, 3)]
    assert errors[0] > errors[1] > errors[2] and errors[2] < 1e-9
    model = PolynomialSurrogate(2).fit(x, y)
    assert model.term_labels(["u", "v"]) == ["1", "u", "v", "u^2", "u*v", "v^2"]
    assert model.options() == {"degree": 2, "ridge": 0.0}
    # ridge shrinks the slope coefficients and allows more terms than points
    plain = PolynomialSurrogate(1).fit(x[:10], y[:10]).coefficients
    shrunk = PolynomialSurrogate(1, ridge=5.0).fit(x[:10], y[:10]).coefficients
    assert np.linalg.norm(shrunk[1:]) < np.linalg.norm(plain[1:])
    assert PolynomialSurrogate(3, ridge=1e-3).fit(x[:5], y[:5]).diagnostics["n_terms"] == 10


def test_polynomial_errors() -> None:
    x = np.random.default_rng(4).random((5, 3))
    with pytest.raises(SurrogateError, match="lower the degree"):
        PolynomialSurrogate(3).fit(x, x[:, 0])
    for bad in (0, 4):
        with pytest.raises(InvalidInput):
            PolynomialSurrogate(bad)
    with pytest.raises(InvalidInput):
        PolynomialSurrogate(2, ridge=-1.0)
    with pytest.raises(SurrogateError, match="not fitted"):
        PolynomialSurrogate().predict(x)
    with pytest.raises(InvalidInput, match="NaN"):
        PolynomialSurrogate(1).fit(np.array([[math.nan], [1.0], [2.0]]), [1.0, 2.0, 3.0])
    with pytest.raises(InvalidInput, match="responses"):
        PolynomialSurrogate(1).fit(x, x[:3, 0])


# ---------------------------------------------------------------- radial basis functions


@pytest.mark.parametrize("kernel", ["thin_plate_spline", "cubic", "linear", "gaussian"])
def test_rbf_interpolates_the_training_points(
    kernel: str, branin_data: tuple[np.ndarray, ...]
) -> None:
    train, y, _, _, bounds = branin_data
    model = RBFSurrogate(kernel).fit(train, y, bounds=bounds)
    prediction = model.predict(train)
    assert np.abs(prediction.mean - y).max() < 1e-6 * max(1.0, np.abs(y).max())
    assert prediction.std is None  # no uncertainty estimate


def test_rbf_smoothing_relaxes_interpolation_and_duplicates_are_merged() -> None:
    rng = np.random.default_rng(5)
    x = rng.random((25, 2))
    y = np.sin(3 * x[:, 0]) + rng.normal(0.0, 0.2, 25)
    exact = RBFSurrogate().fit(x, y).predict(x).mean
    smooth = RBFSurrogate(smoothing=0.01).fit(x, y).predict(x).mean
    assert np.abs(exact - y).max() < 1e-8 and np.abs(smooth - y).max() > 1e-3
    doubled = np.vstack([x, x[:3]])  # a replicated centre point, for instance
    model = RBFSurrogate().fit(doubled, np.concatenate([y, y[:3] + 0.2]))
    assert model.diagnostics["n_merged"] == 3
    assert np.allclose(model.predict(x[:3]).mean, y[:3] + 0.1)  # averaged, not an error


def test_rbf_errors() -> None:
    with pytest.raises(InvalidInput, match="kernel"):
        RBFSurrogate("wavelet")
    with pytest.raises(SurrogateError, match="RBF fit failed"):
        RBFSurrogate().fit(np.array([[0.0, 0.0], [1.0, 1.0]]), [1.0, 2.0])  # fewer than d + 1


# ---------------------------------------------------------------- Gaussian process


def test_gaussian_process_interpolates_and_knows_where_it_is_unsure(
    branin_data: tuple[np.ndarray, ...],
) -> None:
    pytest.importorskip("sklearn")
    train, y, test, y_test, bounds = branin_data
    model = GaussianProcessSurrogate().fit(train, y, bounds=bounds)
    at_train = model.predict(train)
    assert np.abs(at_train.mean - y).max() < 1e-3
    away = model.predict(test)
    assert at_train.std is not None and away.std is not None
    assert at_train.std.max() < 1e-2 < away.std.mean()
    assert r2_score(y_test, away.mean) > 0.99
    assert "kernel" in model.diagnostics and model.diagnostics["nugget"] >= 1e-10


def test_gaussian_process_options_noise_and_warm_start() -> None:
    pytest.importorskip("sklearn")
    rng = np.random.default_rng(6)
    x = rng.random((30, 2))
    y = np.sin(4 * x[:, 0]) + 0.1 * rng.normal(size=30)
    noisy = GaussianProcessSurrogate("matern32", noise=None, restarts=0).fit(x, y)
    interpolating = GaussianProcessSurrogate("rbf", noise=0.0, restarts=0).fit(x, y)
    assert np.abs(noisy.predict(x).mean - y).max() > np.abs(interpolating.predict(x).mean - y).max()
    warm = GaussianProcessSurrogate(warm_start=True, restarts=0)
    first = warm.fit(x, y).diagnostics["kernel"]
    second = warm.fit(x, y).diagnostics["kernel"]  # starts from the first optimum
    assert first == second
    assert warm.clone().options() == warm.options() and not warm.clone().fitted
    for kwargs in ({"kernel": "sinc"}, {"noise": -1.0}, {"restarts": -1}):
        with pytest.raises(InvalidInput):
            GaussianProcessSurrogate(**kwargs)  # type: ignore[arg-type]


def test_gaussian_process_survives_replicated_points() -> None:
    pytest.importorskip("sklearn")
    rng = np.random.default_rng(7)
    x = np.vstack([rng.random((12, 2)), np.full((3, 2), 0.5)])  # three copies of the centre
    y = np.concatenate([rng.random(12), [1.0, 1.1, 0.9]])
    model = GaussianProcessSurrogate(restarts=0).fit(x, y)
    assert model.diagnostics["n_merged"] == 2
    assert model.predict([[0.5, 0.5]]).mean[0] == pytest.approx(1.0, abs=0.05)


def test_gaussian_process_without_scikit_learn_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_packages(monkeypatch, "sklearn")
    with pytest.raises(MissingDependency) as info:
        GaussianProcessSurrogate().fit(np.random.default_rng(0).random((5, 2)), np.arange(5.0))
    error = info.value
    assert error.code == "explore.missing_dependency"
    assert error.details["package"] == "scikit-learn" and error.details["extra"] == "explore"
    assert "pip install" in str(error) and "[explore]" in str(error)
    # the other surrogates do not need the extra
    assert RBFSurrogate().fit(np.random.default_rng(0).random((5, 2)), np.arange(5.0)).fitted


# ---------------------------------------------------------------- factory and tables


def test_factory_kinds_aliases_and_errors() -> None:
    assert SURROGATE_KINDS == ("polynomial", "rbf", "gp")
    assert isinstance(make_surrogate("poly", degree=3), PolynomialSurrogate)
    assert isinstance(make_surrogate("radial-basis"), RBFSurrogate)
    assert isinstance(make_surrogate("kriging"), GaussianProcessSurrogate)
    assert make_surrogate("polynomial", degree=2, ridge=0.1).options() == {
        "degree": 2,
        "ridge": 0.1,
    }
    with pytest.raises(InvalidInput, match="unknown surrogate"):
        make_surrogate("neural")
    with pytest.raises(InvalidInput, match="bad options"):
        make_surrogate("rbf", degree_of_freedom=3)
    model = fit_surrogate(
        "polynomial", [[0.0], [1.0], [2.0], [3.0]], [1.0, 3.0, 5.0, 7.0], degree=1
    )
    assert model.predict([[4.0]]).mean[0] == pytest.approx(9.0)


def test_fit_table_drops_failed_rows_and_uses_the_variable_bounds() -> None:
    function = branin()
    table = latin_hypercube(function.variables, 20, seed=3)
    responses = function.evaluate(table.points)
    responses[[2, 5]] = math.nan  # two failed runs
    table = table.with_responses(responses, ["f"])
    model = fit_table("rbf", table, "f")
    assert model.diagnostics["n_train"] == 18
    assert np.allclose(model.predict(table.points[[0, 1]]).mean, responses[[0, 1], 0], atol=1e-6)
    with pytest.raises(SurrogateError, match="no responses"):
        fit_table("rbf", latin_hypercube(function.variables, 5), "f")


# ---------------------------------------------------------------- validation


def test_metrics() -> None:
    y = np.array([1.0, 2.0, 3.0, 4.0])
    assert r2_score(y, y) == 1.0 and rmse(y, y) == 0.0
    assert r2_score(y, np.full(4, y.mean())) == pytest.approx(0.0)
    assert rmse(y, y + 0.5) == pytest.approx(0.5)
    assert math.isnan(r2_score([1.0, 1.0], [1.0, 2.0]))  # constant observations


def test_cross_validation_on_branin(branin_data: tuple[np.ndarray, ...]) -> None:
    train, y, _, _, bounds = branin_data
    polynomial = cross_validate(PolynomialSurrogate(3), train, y, bounds=bounds)
    rbf = cross_validate(RBFSurrogate("cubic"), train, y, bounds=bounds)
    for name, cv in (("polynomial", polynomial), ("rbf", rbf)):
        assert 0.8 < cv.r2 <= 1.0, name  # sane: Branin is smooth and 30 points are plenty
        assert cv.rmse > 0.0 and cv.mae <= cv.rmse + 1e-12
        assert cv.result.observed.shape == cv.result.predicted.shape == (30,)
    assert rbf.result.std is None


def test_cross_validation_of_the_gaussian_process(branin_data: tuple[np.ndarray, ...]) -> None:
    pytest.importorskip("sklearn")
    train, y, _, _, bounds = branin_data
    gp = cross_validate(GaussianProcessSurrogate(restarts=0), train, y, bounds=bounds)
    rbf = cross_validate(RBFSurrogate("cubic"), train, y, bounds=bounds)
    assert gp.r2 > 0.98 and gp.result.std is not None
    assert gp.r2 > rbf.r2  # the right model for a smooth surface


def test_cross_validation_folds_are_a_seeded_partition(branin_data: tuple[np.ndarray, ...]) -> None:
    train, y, _, _, _ = branin_data
    cv = cross_validate(RBFSurrogate(), train, y, folds=5, seed=3)
    assert cv.folds == 5 and sorted(np.bincount(cv.fold_of)) == [6, 6, 6, 6, 6]
    again = cross_validate(RBFSurrogate(), train, y, folds=5, seed=3)
    other = cross_validate(RBFSurrogate(), train, y, folds=5, seed=4)
    assert np.array_equal(cv.fold_of, again.fold_of) and not np.array_equal(
        cv.fold_of, other.fold_of
    )
    # each point is predicted by a model that never saw it: not the training fit
    assert cv.rmse > 1e-3
    loo = cross_validate(PolynomialSurrogate(2), train, y, folds=len(y))
    assert loo.folds == 30 and np.array_equal(np.sort(loo.fold_of), np.arange(30))
    with pytest.raises(InvalidInput, match="folds"):
        cross_validate(RBFSurrogate(), train, y, folds=1)
    with pytest.raises(InvalidInput, match="folds"):
        cross_validate(RBFSurrogate(), train, y, folds=31)


def test_predicted_versus_observed_on_held_out_points(branin_data: tuple[np.ndarray, ...]) -> None:
    train, y, test, y_test, bounds = branin_data
    model = PolynomialSurrogate(3).fit(train, y, bounds=bounds)
    held_out = predicted_vs_observed(model, test, y_test)
    fitted = predicted_vs_observed(model, train, y)
    assert held_out.observed.shape == held_out.predicted.shape == (200,)
    assert held_out.r2 == pytest.approx(r2_score(y_test, model.predict(test).mean))
    assert held_out.rmse >= fitted.rmse * 0.5 and held_out.max_error >= held_out.mae
    assert held_out.std is not None  # the polynomial has an error estimate here
