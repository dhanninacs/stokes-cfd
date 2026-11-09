"""Single-objective drivers on scipy: differential evolution, Nelder-Mead and COBYLA.

All three search the unit box and evaluate through a ``Recorder``, so they share bounds,
admissible-value rounding, NaN handling for failed points, evaluation limits, generation
callbacks and the result type. Differential evolution evaluates a whole generation as one batch
and handles constraints by comparing violations (Lampinen's rule, inside scipy); Nelder-Mead
takes constraints as a penalty; COBYLA takes them natively. A failed point is the worst possible
value for the algorithm, and an infeasible one is never reported as the best point.

Import rule: numpy at module level; scipy lazily.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore._imports import scipy_module
from stokes.explore.arrays import Floats, as_matrix, as_vector
from stokes.explore.errors import Cancelled, InvalidInput
from stokes.explore.optimise.recorder import Recorder, StopRun
from stokes.explore.optimise.types import Evaluator, GenerationCallback, OptimiseResult
from stokes.explore.problem import Problem
from stokes.explore.sampling import unit_latin_hypercube

_FAILED = 1e30  # what a failed point costs: finite, so no solver chokes on it


def _single(problem: Problem, algorithm: str) -> None:
    if problem.n_objectives != 1:
        raise InvalidInput(
            f"{algorithm} minimises one objective, the problem has {problem.n_objectives}: "
            "use nsga2 or nsga3 for several"
        )


def _start(problem: Problem, x0: ArrayLike) -> Floats:
    x = as_vector(x0, name="x0", finite=True)
    if x.shape[0] != problem.n_var:
        raise InvalidInput(f"x0 has {x.shape[0]} entries for {problem.n_var} variables")
    return np.asarray(problem.normalise(x[None, :])[0], dtype=np.float64)


def _objective(problem: Problem, responses: Floats) -> float:
    value = float(problem.minimised(responses)[0, 0])
    return value if np.isfinite(value) else _FAILED


def _outcome(run: Callable[[], Any]) -> tuple[str, str, Any]:
    """Run the scipy call, turning the ways a run can be cut short into a status."""
    try:
        res = run()
    except StopRun as stop:
        return stop.status, stop.message, None
    except Cancelled as cancelled:
        return "cancelled", cancelled.message, None
    except ValueError as exc:  # scipy refusing its options: a strategy name, a population size
        raise InvalidInput(f"scipy rejected the settings of this run: {exc}") from exc
    return "", "", res


def differential_evolution(
    problem: Problem,
    evaluator: Evaluator,
    *,
    population: int | None = None,
    max_generations: int = 100,
    max_evaluations: int | None = None,
    seed: int = 0,
    callback: GenerationCallback | None = None,
    initial: ArrayLike | None = None,
    strategy: str = "best1bin",
    mutation: tuple[float, float] = (0.5, 1.0),
    recombination: float = 0.7,
    tol: float = 1e-6,
    atol: float = 1e-10,
) -> OptimiseResult:
    """Differential evolution for one objective, bounds and constraints.

    The initial population is a Latin hypercube (``population`` members, default ``10 d`` but at
    least 20) in which the rows of ``initial`` (variable values) replace the first members. Every
    generation is one evaluator batch and one callback call; generation 0 is the initial
    population. The run ends when the population has converged (relative spread of the objective
    below ``tol`` or absolute below ``atol``), after ``max_generations`` generations or when
    ``max_evaluations`` would be exceeded, whichever comes first. There is no final polishing
    step, which would cost many extra evaluations.
    """
    _single(problem, "differential evolution")
    d = problem.n_var
    seeds = (
        problem.normalise(as_matrix(initial, name="initial", columns=d, finite=True))
        if initial is not None
        else np.zeros((0, d))
    )
    members = max(int(population) if population else max(20, 10 * d), seeds.shape[0], 5)
    generations = int(max_generations)
    if max_evaluations is not None:
        if max_evaluations < members:
            raise InvalidInput(
                f"max_evaluations {max_evaluations} is below the population size {members}"
            )
        generations = min(generations, max_evaluations // members - 1)
    unit = unit_latin_hypercube(d, members, seed)
    unit[: seeds.shape[0]] = seeds
    rec = Recorder(
        problem,
        evaluator,
        algorithm="differential_evolution",
        seed=seed,
        callback=callback,
        auto_emit=True,
        limit=max_evaluations,
    )
    optimize = scipy_module("scipy.optimize")

    def objective(u: Floats) -> Floats:
        responses = rec.evaluate_unit(u.T)
        values = problem.minimised(responses)[:, 0]
        return np.where(np.isfinite(values), values, np.inf)

    constraints: list[Any] = []  # Any: scipy NonlinearConstraint
    if problem.constraints:

        def violation(u: Floats) -> Floats:
            # scipy probes with one vector (d,) and then calls with a whole batch (d, S)
            if u.ndim == 1:
                return problem.violation(rec.evaluate_unit(u[None, :]))
            return problem.violation(rec.evaluate_unit(u.T))[None, :]

        constraints.append(optimize.NonlinearConstraint(violation, -np.inf, 0.0))

    def run() -> Any:  # Any: scipy OptimizeResult
        rec.evaluate_unit(unit)  # generation 0 is the whole initial population, as one batch
        return optimize.differential_evolution(
            objective,
            [(0.0, 1.0)] * d,
            strategy=strategy,
            maxiter=generations,
            tol=tol,
            atol=atol,
            mutation=mutation,
            recombination=recombination,
            rng=seed,
            polish=False,
            init=unit,
            updating="deferred",
            vectorized=True,
            constraints=constraints,
        )

    status, message, res = _outcome(run)
    if res is None:
        return rec.finish(status, message)
    final = problem.denormalise(np.asarray(res.population))
    if res.success:
        status = "converged"
    else:
        status = "max_generations" if generations == max_generations else "max_evaluations"
    return rec.finish(
        status, str(res.message), population=final, population_responses=rec.peek(final)
    )


def _simplex(u0: Floats, step: float) -> Floats:
    """The initial Nelder-Mead simplex in the unit box: ``u0`` and a step along every axis,
    taken inward where the step would leave the box."""
    d = u0.shape[0]
    vertices = np.tile(u0, (d + 1, 1))
    for i in range(d):
        vertices[i + 1, i] += step if u0[i] + step <= 1.0 else -step
    return np.clip(vertices, 0.0, 1.0)


def nelder_mead(
    problem: Problem,
    evaluator: Evaluator,
    x0: ArrayLike,
    *,
    max_evaluations: int = 200,
    step: float = 0.1,
    xatol: float = 1e-6,
    fatol: float = 1e-9,
    penalty: float = 1e3,
    adaptive: bool | None = None,
    callback: GenerationCallback | None = None,
) -> OptimiseResult:
    """Nelder-Mead simplex search for one objective from ``x0`` (variable values).

    The initial simplex, ``step`` long in unit-box coordinates, is evaluated as one batch
    (generation 0); every later iteration is a generation. Bounds are the variable bounds.
    Constraints become a penalty ``penalty * (1 + |f|) * violation`` added to the objective;
    prefer COBYLA or differential evolution when constraints matter. ``adaptive`` selects the
    dimension-adaptive parameters (default for more than two variables).
    """
    _single(problem, "Nelder-Mead")
    if not 0.0 < step <= 1.0:
        raise InvalidInput(f"step must lie in (0, 1], got {step!r}")
    d = problem.n_var
    u0 = _start(problem, x0)
    rec = Recorder(
        problem,
        evaluator,
        algorithm="nelder_mead",
        seed=None,
        callback=callback,
        limit=max_evaluations,
    )
    optimize = scipy_module("scipy.optimize")
    simplex = _simplex(u0, step)

    def objective(u: Floats) -> float:
        responses = rec.evaluate_unit(u[None, :])
        value = _objective(problem, responses)
        if problem.constraints and value < _FAILED:
            excess = float(problem.violation(responses)[0])
            value = (
                value + penalty * (1.0 + abs(value)) * excess if np.isfinite(excess) else _FAILED
            )
        return value

    def run() -> Any:  # Any: scipy OptimizeResult
        rec.evaluate_unit(simplex)
        rec.emit()
        return optimize.minimize(
            objective,
            u0,
            method="Nelder-Mead",
            bounds=[(0.0, 1.0)] * d,
            callback=lambda _xk: rec.emit(),
            options={
                "maxfev": max_evaluations,
                "xatol": xatol,
                "fatol": fatol,
                "initial_simplex": simplex,
                "adaptive": d > 2 if adaptive is None else adaptive,
            },
        )

    status, message, res = _outcome(run)
    if res is None:
        return rec.finish(status, message)
    return rec.finish(*_scipy_status(res, rec, max_evaluations))


def _scipy_status(res: Any, rec: Recorder, limit: int) -> tuple[str, str]:  # Any: OptimizeResult
    if res.success:
        return "converged", str(res.message)
    if rec.n_evaluations >= limit or int(res.nfev) >= limit:
        return "max_evaluations", str(res.message)
    return "stalled", str(res.message)


def cobyla(
    problem: Problem,
    evaluator: Evaluator,
    x0: ArrayLike,
    *,
    max_evaluations: int = 200,
    rhobeg: float = 0.2,
    tol: float = 1e-6,
    callback: GenerationCallback | None = None,
) -> OptimiseResult:
    """COBYLA (derivative-free, linear approximations) for one objective, bounds and
    constraints, from ``x0`` (variable values).

    ``rhobeg`` is the initial trust-region radius in unit-box coordinates and ``tol`` the final
    one. Constraints enter through their signed margins, so equalities and bands are handled.
    Every iteration is a generation (the initial evaluations are part of the first).
    """
    _single(problem, "COBYLA")
    if not rhobeg > 0.0:
        raise InvalidInput(f"rhobeg must be positive, got {rhobeg!r}")
    d = problem.n_var
    u0 = _start(problem, x0)
    rec = Recorder(
        problem,
        evaluator,
        algorithm="cobyla",
        seed=None,
        callback=callback,
        limit=max_evaluations,
    )
    optimize = scipy_module("scipy.optimize")
    n_margins = problem.margins(np.zeros((1, len(problem.responses)))).shape[1]

    def objective(u: Floats) -> float:
        return _objective(problem, rec.evaluate_unit(u[None, :]))

    def margins(u: Floats) -> Floats:
        slack = problem.margins(rec.evaluate_unit(u[None, :]))[0]
        return np.where(np.isfinite(slack), slack, -_FAILED)

    constraints: Sequence[dict[str, Any]] = (
        [{"type": "ineq", "fun": margins}] if n_margins else []
    )  # Any: scipy constraint dictionary

    def run() -> Any:  # Any: scipy OptimizeResult
        return optimize.minimize(
            objective,
            u0,
            method="COBYLA",
            bounds=optimize.Bounds(np.zeros(d), np.ones(d)),
            constraints=constraints,
            tol=tol,
            callback=lambda _xk: rec.emit(),
            options={"rhobeg": rhobeg, "maxiter": max_evaluations},
        )

    status, message, res = _outcome(run)
    if res is None:
        return rec.finish(status, message)
    return rec.finish(*_scipy_status(res, rec, max_evaluations))
