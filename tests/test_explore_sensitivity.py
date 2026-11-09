"""Correlations, Sobol indices and Morris screening (stokes.explore.sensitivity)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from stokes.explore.domain import Normal, Range
from stokes.explore.errors import InvalidInput, MissingDependency, SensitivityError
from stokes.explore.functions import ishigami, ishigami_indices
from stokes.explore.sampling import latin_hypercube
from stokes.explore.sensitivity import (
    correlation_matrix,
    input_output_correlation,
    morris_analyze,
    morris_design,
    morris_screening,
    sobol_analyze,
    sobol_design,
    sobol_from_surrogate,
    sobol_indices,
    table_correlation,
)
from stokes.explore.space import Variable
from stokes.explore.surrogate import fit_table
from tests.test_explore_support import block_packages

ISHIGAMI = ishigami()
FIRST, TOTAL = ishigami_indices()


def ishigami_values(points: np.ndarray) -> np.ndarray:
    return ISHIGAMI.evaluate(points)[:, 0]


# ---------------------------------------------------------------- correlation


def test_spearman_sees_any_monotone_relation_pearson_only_linear_ones() -> None:
    x = np.random.default_rng(0).uniform(0.0, 5.0, size=(300, 1))
    y = np.exp(2.0 * x)  # strongly non-linear but monotone
    spearman = input_output_correlation(x, y, ["x"], ["y"], method="spearman")
    pearson = input_output_correlation(x, y, ["x"], ["y"], method="pearson")
    assert spearman.matrix[0, 0] == pytest.approx(1.0)
    assert 0.5 < pearson.matrix[0, 0] < 0.9
    assert (
        spearman.row_names == ("x",) and spearman.column_names == ("y",) and spearman.n_used == 300
    )
    assert input_output_correlation(x, -y).matrix[0, 0] == pytest.approx(-1.0)


def test_correlation_of_independent_inputs_and_matrix_shape() -> None:
    rng = np.random.default_rng(1)
    data = rng.random((2000, 3))
    data = np.column_stack([data, data[:, 0] + 0.01 * rng.normal(size=2000)])
    result = correlation_matrix(data, ["a", "b", "c", "d"])
    matrix = result.matrix
    assert (
        matrix.shape == (4, 4)
        and np.allclose(np.diag(matrix), 1.0)
        and np.allclose(matrix, matrix.T)
    )
    assert abs(matrix[0, 1]) < 0.06 and matrix[0, 3] > 0.99
    assert result.row_names == result.column_names == ("a", "b", "c", "d")


def test_correlation_ignores_failed_rows_and_refuses_too_little_data() -> None:
    x = np.arange(10.0)[:, None]
    y = x.copy()
    y[3] = math.nan
    result = input_output_correlation(x, y)
    assert result.n_used == 9 and result.matrix[0, 0] == pytest.approx(1.0)
    with pytest.raises(SensitivityError, match="at least 3"):
        input_output_correlation(x[:2], y[:2])
    with pytest.raises(InvalidInput):
        input_output_correlation(x, y, method="kendall")  # type: ignore[arg-type]
    with pytest.raises(InvalidInput):
        input_output_correlation(x, y[:5])
    constant = input_output_correlation(x, np.ones((10, 1)))
    assert math.isnan(constant.matrix[0, 0])  # undefined, not an error


def test_table_correlation_uses_the_table_names() -> None:
    table = latin_hypercube(ISHIGAMI.variables, 400, seed=2)
    x = table.points
    responses = np.column_stack([np.exp(x[:, 0]) + 0.1 * x[:, 1], -(x[:, 2] ** 3)])
    result = table_correlation(table.with_responses(responses, ["growth", "decay"]))
    assert result.row_names == ("x1", "x2", "x3")
    assert result.column_names == ("growth", "decay")
    assert result.matrix[0, 0] > 0.9 and result.matrix[2, 1] == pytest.approx(-1.0)
    assert abs(result.matrix[2, 0]) < 0.15 and abs(result.matrix[0, 1]) < 0.15
    with pytest.raises(SensitivityError):
        table_correlation(latin_hypercube(ISHIGAMI.variables, 10))


# ---------------------------------------------------------------- Sobol indices


@pytest.mark.parametrize("backend", ["builtin", "salib"])
def test_sobol_indices_of_the_ishigami_function(backend: str) -> None:
    if backend == "salib":
        pytest.importorskip("SALib")
    result = sobol_indices(ishigami_values, ISHIGAMI.variables, n=2048, seed=3, backend=backend)  # type: ignore[arg-type]
    assert result.backend == backend and result.n_evaluations == 2048 * 5
    assert result.names == ("x1", "x2", "x3")
    assert np.allclose(result.first, FIRST, atol=0.06)  # known: 0.314, 0.442, 0
    assert np.allclose(result.total, TOTAL, atol=0.06)  # known: 0.558, 0.442, 0.244
    assert np.all(result.total >= result.first - 0.05)  # total includes the interactions
    assert np.all(result.first_conf > 0) and np.all(result.total_conf > 0)
    assert result.variance == pytest.approx(13.84, rel=0.1)  # a**2/8 + b*pi**4/5 + ... + 1/2


def test_sobol_design_layout() -> None:
    design = sobol_design(ISHIGAMI.variables, 64, seed=1, backend="builtin")
    rows = design.table.points.reshape(64, 5, 3)
    a, b = rows[:, 0], rows[:, 4]
    for i in range(3):  # AB_i is A with the i-th column taken from B
        expected = a.copy()
        expected[:, i] = b[:, i]
        assert np.allclose(rows[:, i + 1], expected)
    assert design.n_evaluations == 64 * 5 and design.table.metadata["backend"] == "builtin"
    assert sobol_analyze(design, ishigami_values(design.table.points)).n_evaluations == 64 * 5


def test_a_design_can_be_analysed_by_either_backend() -> None:
    pytest.importorskip("SALib")
    design = sobol_design(ISHIGAMI.variables, 256, seed=2, backend="salib")  # SALib's own layout
    values = ishigami_values(design.table.points)
    by_builtin = sobol_analyze(design, values, backend="builtin")
    by_salib = sobol_analyze(design, values, backend="salib")
    assert np.allclose(by_builtin.first, by_salib.first, atol=1e-9)
    assert np.allclose(by_builtin.total, by_salib.total, atol=1e-9)
    assert (by_builtin.backend, by_salib.backend) == ("builtin", "salib")


def test_sobol_is_deterministic_for_a_seed() -> None:
    one = sobol_indices(ishigami_values, ISHIGAMI.variables, 256, seed=7, backend="builtin")
    two = sobol_indices(ishigami_values, ISHIGAMI.variables, 256, seed=7, backend="builtin")
    assert np.array_equal(one.first, two.first) and np.array_equal(one.first_conf, two.first_conf)


def test_sobol_analysis_demands_a_complete_design() -> None:
    design = sobol_design(ISHIGAMI.variables, 32, backend="builtin")
    y = ishigami_values(design.table.points)
    y[5] = math.nan
    with pytest.raises(SensitivityError, match="surrogate"):
        sobol_analyze(design, y)
    with pytest.raises(InvalidInput, match="design rows"):
        sobol_analyze(design, y[:10])
    with pytest.raises(InvalidInput):
        sobol_design(ISHIGAMI.variables, 1)
    constant = sobol_analyze(design, np.ones(design.n_evaluations), backend="builtin")
    assert np.isnan(constant.first).all() and constant.variance == 0.0


def test_sobol_from_a_surrogate_needs_no_extra_package(monkeypatch: pytest.MonkeyPatch) -> None:
    table = latin_hypercube(ISHIGAMI.variables, 150, seed=5)
    table = table.with_responses(ishigami_values(table.points)[:, None], ["y"])
    model = fit_table("rbf", table, "y", kernel="cubic")
    block_packages(monkeypatch, "SALib", "sklearn")  # neither is installed, as far as we know
    result = sobol_from_surrogate(model, ISHIGAMI.variables, n=4096, seed=1)
    assert result.backend == "builtin"  # the fallback took over without a word
    assert np.allclose(result.first, FIRST, atol=0.12)
    assert np.allclose(result.total, TOTAL, atol=0.15)
    assert result.first.argmax() == 1 and result.total.argmin() == 2  # the right ranking
    with pytest.raises(MissingDependency, match="SALib"):
        sobol_design(ISHIGAMI.variables, 64, backend="salib")
    with pytest.raises(MissingDependency):
        morris_design(ISHIGAMI.variables, 5)


def test_sobol_indices_follow_each_variables_own_distribution() -> None:
    # y = x1 + x2 with x1 spread ten times wider than x2: x1 owns ~99 % of the variance
    variables = [Variable("x1", Normal(0.0, 10.0)), Variable("x2", Normal(0.0, 1.0))]
    result = sobol_indices(lambda p: p[:, 0] + p[:, 1], variables, 4096, seed=2, backend="builtin")
    assert result.first[0] == pytest.approx(100 / 101, abs=0.03)
    assert result.first[1] == pytest.approx(1 / 101, abs=0.03)


# ---------------------------------------------------------------- Morris


def test_morris_screening_ranks_a_linear_model_by_its_coefficients() -> None:
    pytest.importorskip("SALib")
    variables = [Variable(f"x{i}", Range(0.0, 2.0)) for i in range(1, 4)]
    coefficients = np.array([1.0, 5.0, 0.0])
    result = morris_screening(lambda x: x @ coefficients, variables, trajectories=20, seed=1)
    # an elementary effect is the change over the whole range, 2 * coefficient, with no spread
    assert np.allclose(result.mu_star, 2.0 * np.abs(coefficients))
    assert np.allclose(result.mu, 2.0 * coefficients) and np.allclose(result.sigma, 0.0, atol=1e-9)
    assert result.names == ("x1", "x2", "x3")
    assert list(np.argsort(-result.mu_star)) == [1, 0, 2]


def test_morris_flags_interaction_and_non_linearity() -> None:
    pytest.importorskip("SALib")
    result = morris_screening(ishigami_values, ISHIGAMI.variables, trajectories=40, seed=2)
    assert np.all(result.sigma > 1.0)  # every Ishigami variable acts non-linearly
    assert result.mu_star.shape == (3,) and np.all(result.mu_star_conf >= 0)
    design = morris_design(ISHIGAMI.variables, 12, seed=3, levels=4)
    assert design.table.n_points == 12 * 4 and design.unit.shape == (48, 3)
    assert design.table.space.contains(design.table.points).all()
    again = morris_analyze(design, ishigami_values(design.table.points), seed=3)
    assert again.mu_star.shape == (3,)
    with pytest.raises(InvalidInput):
        morris_design(ISHIGAMI.variables, 1)
    with pytest.raises(InvalidInput, match="design rows"):
        morris_analyze(design, np.zeros(5))
