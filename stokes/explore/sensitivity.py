"""Sensitivity analysis (ADR-0016): correlation matrices, Sobol indices and Morris screening, on a
table of results or on a surrogate.

* Spearman and Pearson correlations come straight from a table of points and responses.
* Sobol indices need a special design (Saltelli's A, B and AB_i matrices): ``sobol_design`` builds
  it, a solver or a function evaluates it, ``sobol_analyze`` turns the responses into first-order
  and total indices with bootstrap confidence intervals. ``sobol_indices`` does all three for a
  cheap function and ``sobol_from_surrogate`` for a fitted surrogate, which is how a handful of
  expensive runs yields indices. SALib is used when it is installed; without it a built-in
  implementation of the same estimators (Saltelli 2010 for first order, Jansen 1999 for total
  order) on the same sample layout takes over, so the surrogate route never needs the extra.
* Morris screening (elementary effects) needs SALib.

Everything works in probability space: a design lives in the unit cube and is mapped to values
through the variables' quantile functions, so indices describe the variance under each variable's
own distribution (uniform ranges, truncated normals, discrete levels alike).

Import rule: numpy at module level; scipy, SALib lazily.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any, Literal

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore._imports import require, scipy_module, try_require
from stokes.explore.arrays import Floats, Json, as_matrix, as_vector, check_names
from stokes.explore.errors import InvalidInput, SensitivityError
from stokes.explore.sampling import unit_sobol
from stokes.explore.space import DesignSpace, Variable
from stokes.explore.surrogate_base import Surrogate
from stokes.explore.table import DesignTable

CorrelationMethod = Literal["spearman", "pearson"]
Backend = Literal["auto", "salib", "builtin"]

_QUIET = ("Convergence properties of the Sobol", "The balance properties of Sobol")


# ---------------------------------------------------------------- correlation


@dataclass(frozen=True, eq=False)
class Correlation:
    """A correlation matrix with its row and column names; ``n_used`` rows entered it."""

    matrix: Floats
    method: str
    row_names: tuple[str, ...]
    column_names: tuple[str, ...]
    n_used: int


def _pearson(a: Floats, b: Floats) -> Floats:
    ac, bc = a - a.mean(axis=0), b - b.mean(axis=0)
    scale = np.sqrt((ac**2).sum(axis=0))[:, None] * np.sqrt((bc**2).sum(axis=0))[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (ac.T @ bc) / scale
    out[scale == 0.0] = math.nan
    return np.asarray(np.clip(out, -1.0, 1.0), dtype=np.float64)


def _correlate(a: Floats, b: Floats, method: str) -> tuple[Floats, int]:
    if method not in ("spearman", "pearson"):
        raise InvalidInput(f"correlation method must be 'spearman' or 'pearson', got {method!r}")
    keep = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    a, b = a[keep], b[keep]
    if a.shape[0] < 3:
        raise SensitivityError(f"a correlation needs at least 3 complete rows, got {a.shape[0]}")
    if method == "spearman":
        rank = scipy_module("scipy.stats").rankdata
        a, b = np.asarray(rank(a, axis=0)), np.asarray(rank(b, axis=0))
    return _pearson(a, b), int(a.shape[0])


def correlation_matrix(
    data: ArrayLike,
    names: Sequence[str] | None = None,
    *,
    method: CorrelationMethod = "spearman",
) -> Correlation:
    """Correlation of every column of ``data`` ``(n, c)`` with every other. Rows with a NaN are
    dropped. Spearman (rank) correlation sees any monotone relation; Pearson only linear ones."""
    matrix = as_matrix(data, name="data")
    labels = check_names(names, matrix.shape[1], prefix="c")
    result, used = _correlate(matrix, matrix, method)
    return Correlation(result, method, labels, labels, used)


def input_output_correlation(
    x: ArrayLike,
    y: ArrayLike,
    input_names: Sequence[str] | None = None,
    output_names: Sequence[str] | None = None,
    *,
    method: CorrelationMethod = "spearman",
) -> Correlation:
    """Correlation of every input column ``(n, d)`` with every output column ``(n, m)``, as a
    ``(d, m)`` matrix. Rows with a NaN in either are dropped."""
    inputs, outputs = as_matrix(x, name="x"), as_matrix(y, name="y")
    if inputs.shape[0] != outputs.shape[0]:
        raise InvalidInput(f"{inputs.shape[0]} input rows but {outputs.shape[0]} output rows")
    rows = check_names(input_names, inputs.shape[1], prefix="x")
    columns = check_names(output_names, outputs.shape[1], prefix="y")
    result, used = _correlate(inputs, outputs, method)
    return Correlation(result, method, rows, columns, used)


def table_correlation(table: DesignTable, *, method: CorrelationMethod = "spearman") -> Correlation:
    """Inputs against responses of a design table (failed rows dropped)."""
    if table.responses is None:
        raise SensitivityError("the table has no responses")
    return input_output_correlation(
        table.points, table.responses, table.names, table.response_names, method=method
    )


# ---------------------------------------------------------------- Sobol indices


@dataclass(frozen=True, eq=False)
class SobolDesign:
    """The points a Sobol analysis needs, ``base_samples * (d + 2)`` rows: for every base sample
    ``j`` in turn the rows A_j, AB_1_j, ..., AB_d_j, B_j, where AB_i_j is A_j with its i-th
    coordinate taken from B_j (the layout SALib uses without second-order indices)."""

    table: DesignTable
    base_samples: int
    backend: str

    @property
    def n_evaluations(self) -> int:
        return self.table.n_points


@dataclass(frozen=True, eq=False)
class SobolIndices:
    """First-order and total Sobol indices per variable, with the half-width of the bootstrap
    confidence interval at ``confidence``. Indices are NaN when the response is constant."""

    names: tuple[str, ...]
    first: Floats
    total: Floats
    first_conf: Floats
    total_conf: Floats
    variance: float
    base_samples: int
    n_evaluations: int
    confidence: float
    backend: str


def _salib_problem(names: Sequence[str]) -> dict[str, Any]:  # Any: SALib's problem dictionary
    return {"num_vars": len(names), "names": list(names), "bounds": [[0.0, 1.0]] * len(names)}


def _salib(path: str, backend: Backend, feature: str) -> Any | None:  # Any: SALib submodule
    """The SALib module at ``path``, or ``None`` when the built-in route should run: always for
    ``"builtin"``, only if SALib is missing for ``"auto"``, never for ``"salib"`` (which raises)."""
    if backend == "builtin":
        return None
    if backend == "salib":
        return require(path, feature=feature)
    return try_require(path, feature=feature)


def _builtin_sobol_unit(d: int, n: int, seed: int) -> Floats:
    raw = unit_sobol(2 * d, n, seed)
    a, b = raw[:, :d], raw[:, d:]
    layout = np.empty((n, d + 2, d))
    layout[:, 0, :] = a
    for i in range(d):
        ab = a.copy()
        ab[:, i] = b[:, i]
        layout[:, i + 1, :] = ab
    layout[:, d + 1, :] = b
    return layout.reshape(n * (d + 2), d)


def sobol_design(
    variables: Sequence[Variable] | DesignSpace,
    n: int = 1024,
    seed: int = 0,
    *,
    backend: Backend = "auto",
) -> SobolDesign:
    """Saltelli's sampling plan for ``n`` base samples (a power of two converges best), costing
    ``n * (d + 2)`` evaluations. ``backend`` is ``"salib"``, ``"builtin"`` or ``"auto"`` (SALib
    when installed)."""
    space = DesignSpace.of(variables)
    if int(n) != n or n < 2:
        raise InvalidInput(f"base samples n must be an integer of at least 2, got {n!r}")
    n = int(n)
    module = _salib("SALib.sample.sobol", backend, "Sobol indices through SALib")
    if module is not None:
        with warnings.catch_warnings():
            for text in _QUIET:
                warnings.filterwarnings("ignore", message=text)
            unit = np.asarray(
                module.sample(
                    _salib_problem(space.names),
                    n,
                    calc_second_order=False,
                    scramble=True,
                    seed=seed,
                ),
                dtype=np.float64,
            )
        used = "salib"
    else:
        unit = _builtin_sobol_unit(space.size, n, seed)
        used = "builtin"
    metadata: dict[str, Json] = {
        "sampler": "sobol_indices",
        "seed": seed,
        "n": int(unit.shape[0]),
        "base_samples": n,
        "backend": used,
    }
    table = DesignTable(space.variables, space.from_unit(unit), metadata)
    return SobolDesign(table, n, used)


def _sobol_estimators(y: Floats, d: int) -> tuple[Floats, Floats, float]:
    """First-order (Saltelli 2010), total (Jansen) indices and the output variance from
    responses laid out as ``(n, d + 2)`` = [A, AB_1 .. AB_d, B]."""
    f_a, f_ab, f_b = y[:, 0], y[:, 1 : d + 1], y[:, d + 1]
    variance = float(np.var(np.concatenate([f_a, f_b])))
    if variance <= 0.0:
        return np.full(d, math.nan), np.full(d, math.nan), variance
    first = np.mean(f_b[:, None] * (f_ab - f_a[:, None]), axis=0) / variance
    total = 0.5 * np.mean((f_a[:, None] - f_ab) ** 2, axis=0) / variance
    return first, total, variance


def _builtin_analysis(
    y: Floats, d: int, seed: int, resamples: int, confidence: float
) -> tuple[Floats, Floats, Floats, Floats, float]:
    layout = y.reshape(-1, d + 2)
    layout = layout - layout.mean()
    first, total, variance = _sobol_estimators(layout, d)
    rng = np.random.default_rng(seed)
    n = layout.shape[0]
    boot = [_sobol_estimators(layout[rng.integers(0, n, n)], d) for _ in range(resamples)]
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    first_conf = z * np.std([b[0] for b in boot], axis=0, ddof=1)
    total_conf = z * np.std([b[1] for b in boot], axis=0, ddof=1)
    return first, total, first_conf, total_conf, variance


def sobol_analyze(
    design: SobolDesign,
    y: ArrayLike,
    *,
    seed: int = 0,
    resamples: int = 100,
    confidence: float = 0.95,
    backend: Backend = "auto",
) -> SobolIndices:
    """Sobol indices of the responses ``y`` (one per design row, in design order).

    A design from either backend can be analysed by either: they share one layout. Every point
    must have evaluated; with failed points use a surrogate (``sobol_from_surrogate``).
    """
    values = as_vector(y, name="y")
    table = design.table
    if values.shape[0] != table.n_points:
        raise InvalidInput(f"{table.n_points} design rows but {values.shape[0]} responses")
    if not np.isfinite(values).all():
        failed = int((~np.isfinite(values)).sum())
        raise SensitivityError(
            f"{failed} of {values.shape[0]} responses are not finite; Sobol indices need every "
            "point of the design, use a surrogate instead",
            details={"failed": failed},
        )
    if not 0.0 < confidence < 1.0 or resamples < 2:
        raise InvalidInput("confidence must lie in (0, 1) and resamples must be at least 2")
    d = table.n_variables
    module = _salib("SALib.analyze.sobol", backend, "Sobol indices through SALib")
    if module is not None:
        result = module.analyze(
            _salib_problem(table.names),
            values,
            calc_second_order=False,
            num_resamples=resamples,
            conf_level=confidence,
            print_to_console=False,
            seed=seed,
        )
        first, total = np.asarray(result["S1"]), np.asarray(result["ST"])
        first_conf, total_conf = np.asarray(result["S1_conf"]), np.asarray(result["ST_conf"])
        variance = float(np.var(values))
        used = "salib"
    else:
        first, total, first_conf, total_conf, variance = _builtin_analysis(
            values, d, seed, resamples, confidence
        )
        used = "builtin"
    return SobolIndices(
        table.names,
        first,
        total,
        first_conf,
        total_conf,
        variance,
        design.base_samples,
        table.n_points,
        confidence,
        used,
    )


def sobol_indices(
    function: Callable[[Floats], ArrayLike],
    variables: Sequence[Variable] | DesignSpace,
    n: int = 1024,
    seed: int = 0,
    *,
    resamples: int = 100,
    confidence: float = 0.95,
    backend: Backend = "auto",
) -> SobolIndices:
    """Sobol indices of a cheap vectorised ``function``: points ``(m, d)`` to responses ``(m,)``."""
    design = sobol_design(variables, n, seed, backend=backend)
    responses = as_vector(function(design.table.points), name="function output")
    return sobol_analyze(
        design, responses, seed=seed, resamples=resamples, confidence=confidence, backend=backend
    )


def sobol_from_surrogate(
    surrogate: Surrogate,
    variables: Sequence[Variable] | DesignSpace,
    n: int = 4096,
    seed: int = 0,
    *,
    resamples: int = 100,
    confidence: float = 0.95,
    backend: Backend = "auto",
) -> SobolIndices:
    """Sobol indices of a fitted surrogate's mean prediction: the design is sampled from the
    surrogate instead of the solver, so it costs no runs. Works without SALib."""
    return sobol_indices(
        lambda points: surrogate.predict(points).mean,
        variables,
        n,
        seed,
        resamples=resamples,
        confidence=confidence,
        backend=backend,
    )


# ---------------------------------------------------------------- Morris screening


@dataclass(frozen=True, eq=False)
class MorrisDesign:
    """A Morris plan: ``trajectories`` one-at-a-time paths of ``d + 1`` points each, in the unit
    cube (``unit``, what the analysis reads) and in variable values (``table``)."""

    table: DesignTable
    unit: Floats
    trajectories: int
    levels: int


@dataclass(frozen=True, eq=False)
class MorrisResult:
    """Elementary-effect statistics per variable, in units of response change over the whole
    range (probability) of the variable: ``mu_star`` (mean absolute effect) ranks importance,
    ``sigma`` flags non-linearity or interaction, ``mu`` the signed mean effect."""

    names: tuple[str, ...]
    mu: Floats
    mu_star: Floats
    sigma: Floats
    mu_star_conf: Floats


def morris_design(
    variables: Sequence[Variable] | DesignSpace,
    trajectories: int = 10,
    seed: int = 0,
    *,
    levels: int = 4,
) -> MorrisDesign:
    """Morris trajectories through SALib (``trajectories * (d + 1)`` evaluations)."""
    space = DesignSpace.of(variables)
    if trajectories < 2 or levels < 2:
        raise InvalidInput("Morris screening needs at least 2 trajectories and 2 levels")
    module = require("SALib.sample.morris", feature="Morris screening")
    unit = np.asarray(
        module.sample(_salib_problem(space.names), trajectories, num_levels=levels, seed=seed),
        dtype=np.float64,
    )
    metadata: dict[str, Json] = {
        "sampler": "morris",
        "seed": seed,
        "n": int(unit.shape[0]),
        "trajectories": trajectories,
        "levels": levels,
    }
    return MorrisDesign(
        DesignTable(space.variables, space.from_unit(unit), metadata), unit, trajectories, levels
    )


def morris_analyze(
    design: MorrisDesign,
    y: ArrayLike,
    *,
    seed: int = 0,
    resamples: int = 100,
    confidence: float = 0.95,
) -> MorrisResult:
    """Elementary-effect statistics from the responses ``y`` of a Morris design."""
    values = as_vector(y, name="y", finite=True)
    if values.shape[0] != design.table.n_points:
        raise InvalidInput(f"{design.table.n_points} design rows but {values.shape[0]} responses")
    module = require("SALib.analyze.morris", feature="Morris screening")
    result = module.analyze(
        _salib_problem(design.table.names),
        design.unit,
        values,
        conf_level=confidence,
        print_to_console=False,
        num_levels=design.levels,
        num_resamples=resamples,
        seed=seed,
    )
    return MorrisResult(
        design.table.names,
        np.asarray(result["mu"], dtype=np.float64),
        np.asarray(result["mu_star"], dtype=np.float64),
        np.asarray(result["sigma"], dtype=np.float64),
        np.asarray(result["mu_star_conf"], dtype=np.float64),
    )


def morris_screening(
    function: Callable[[Floats], ArrayLike],
    variables: Sequence[Variable] | DesignSpace,
    trajectories: int = 10,
    seed: int = 0,
    *,
    levels: int = 4,
) -> MorrisResult:
    """Morris screening of a cheap vectorised function (or of a surrogate's mean)."""
    design = morris_design(variables, trajectories, seed, levels=levels)
    responses = as_vector(function(design.table.points), name="function output")
    return morris_analyze(design, responses, seed=seed)
