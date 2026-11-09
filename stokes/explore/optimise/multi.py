"""Multi-objective drivers: NSGA-II and NSGA-III through pymoo.

pymoo is optional (the ``explore`` extra); it is imported when a driver runs and a missing install
raises ``MissingDependency``. The drivers search the unit box, hand pymoo a problem whose
evaluation goes through the shared ``Recorder`` (one evaluator batch per generation, failed
points marked infeasible through an extra constraint column), and report every generation
with its population, the non-dominated set so far and the hypervolume history. The Pareto set in
the result is the feasible non-dominated set of *everything* evaluated, not only of the last
population.

Import rule: numpy at module level; scipy and pymoo lazily.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore._imports import require
from stokes.explore.arrays import Floats, as_matrix
from stokes.explore.errors import Cancelled, InvalidInput
from stokes.explore.optimise.recorder import Recorder, StopRun
from stokes.explore.optimise.types import Evaluator, GenerationCallback, OptimiseResult
from stokes.explore.problem import Problem
from stokes.explore.sampling import unit_latin_hypercube

_FEATURE = "NSGA-II and NSGA-III through pymoo"
_FILL = 1e6  # objective value given to a failed point when nothing finite has been seen


def _pymoo_problem(problem: Problem, rec: Recorder) -> Any:  # Any: pymoo Problem
    """A pymoo problem over the unit box that evaluates through the recorder."""
    base = require("pymoo.core.problem", feature=_FEATURE).Problem
    n_margins = problem.margins(np.zeros((1, len(problem.responses)))).shape[1]
    worst = np.full(problem.n_objectives, -math.inf)

    class Adapter(base):  # type: ignore[misc, valid-type]  # pymoo ships no stubs
        def __init__(self) -> None:
            super().__init__(
                n_var=problem.n_var,
                n_obj=problem.n_objectives,
                n_ieq_constr=n_margins + 1,
                xl=np.zeros(problem.n_var),
                xu=np.ones(problem.n_var),
            )

        def _evaluate(self, x: Floats, out: dict[str, Floats], *args: Any, **kwargs: Any) -> None:
            responses = rec.evaluate_unit(x)
            objectives = problem.minimised(responses)
            failed = problem.failed(responses)
            finite = np.where(failed[:, None], -math.inf, objectives)
            worst[:] = np.maximum(worst, finite.max(axis=0))
            fill = np.where(np.isfinite(worst), worst, _FILL)
            out["F"] = np.where(failed[:, None], fill, objectives)
            slack = problem.margins(responses)
            violated = np.where(np.isfinite(slack), -slack, 1.0)
            out["G"] = np.column_stack([violated, np.where(failed, 1.0, -1.0)])

    return Adapter()


def _initial_unit(problem: Problem, members: int, seed: int, initial: ArrayLike | None) -> Floats:
    unit = unit_latin_hypercube(problem.n_var, members, seed)
    if initial is not None:
        seeds = problem.normalise(
            as_matrix(initial, name="initial", columns=problem.n_var, finite=True)
        )
        unit[: min(members, seeds.shape[0])] = seeds[:members]
    return unit


def _generations(max_generations: int, max_evaluations: int | None, members: int) -> int:
    if max_evaluations is None:
        return max_generations
    if max_evaluations < members:
        raise InvalidInput(
            f"max_evaluations {max_evaluations} is below the population size {members}"
        )
    return min(max_generations, max_evaluations // members - 1)


def _evolve(
    problem: Problem,
    evaluator: Evaluator,
    *,
    name: str,
    build: Callable[[Floats], Any],
    members: int,
    max_generations: int,
    max_evaluations: int | None,
    seed: int,
    callback: GenerationCallback | None,
    initial: ArrayLike | None,
    reference_point: ArrayLike | None,
) -> OptimiseResult:
    if problem.n_objectives < 2:
        raise InvalidInput(f"{name} needs at least two objectives; use differential_evolution")
    generations = _generations(max_generations, max_evaluations, members)
    rec = Recorder(
        problem,
        evaluator,
        algorithm=name,
        seed=seed,
        callback=callback,
        limit=max_evaluations,
        reference=reference_point,
    )
    algorithm = build(_initial_unit(problem, members, seed, initial))
    algorithm.setup(
        _pymoo_problem(problem, rec),
        termination=("n_gen", generations + 1),
        seed=seed,
        verbose=False,
    )
    population: Floats | None = None
    status, message = "max_generations", f"{generations} generations after the initial population"
    try:
        while algorithm.has_next():
            algorithm.next()
            population = problem.denormalise(np.asarray(algorithm.pop.get("X")))
            rec.emit(population=population, population_responses=rec.peek(population))
    except StopRun as stop:
        status, message = stop.status, stop.message
    except Cancelled as cancelled:
        status, message = "cancelled", cancelled.message
    if status == "max_generations" and generations < max_generations:
        status = "max_evaluations"
    responses = None if population is None else rec.peek(population)
    return rec.finish(status, message, population=population, population_responses=responses)


def nsga2(
    problem: Problem,
    evaluator: Evaluator,
    *,
    population: int = 40,
    max_generations: int = 50,
    max_evaluations: int | None = None,
    seed: int = 0,
    callback: GenerationCallback | None = None,
    initial: ArrayLike | None = None,
    reference_point: ArrayLike | None = None,
) -> OptimiseResult:
    """NSGA-II for two or more objectives, with bounds and constraints.

    ``population`` members evolve for ``max_generations`` generations after the initial
    population (a Latin hypercube in which the rows of ``initial``, variable values, replace the
    first members), or until ``max_evaluations`` would be exceeded; there are
    ``population * (generations + 1)`` evaluations in all. ``reference_point`` (natural units) is
    the worst corner for the hypervolume history; the default is the worst first-generation
    objective plus a ten per cent margin. Seeded and deterministic.
    """
    nsga2_module = require("pymoo.algorithms.moo.nsga2", feature=_FEATURE)
    if population < 4:
        raise InvalidInput(f"NSGA-II needs a population of at least 4, got {population}")
    return _evolve(
        problem,
        evaluator,
        name="nsga2",
        build=lambda unit: nsga2_module.NSGA2(
            pop_size=population, sampling=unit, eliminate_duplicates=True
        ),
        members=population,
        max_generations=max_generations,
        max_evaluations=max_evaluations,
        seed=seed,
        callback=callback,
        initial=initial,
        reference_point=reference_point,
    )


def reference_directions(n_objectives: int, divisions: int) -> Floats:
    """The Das-Dennis reference directions NSGA-III spreads the front over: every way of
    splitting ``divisions`` parts between ``n_objectives`` objectives, ``C(divisions + m - 1,
    m - 1)`` directions."""
    module = require("pymoo.util.ref_dirs", feature=_FEATURE)
    return np.asarray(
        module.get_reference_directions("das-dennis", n_objectives, n_partitions=divisions)
    )


def nsga3(
    problem: Problem,
    evaluator: Evaluator,
    *,
    divisions: int | None = None,
    population: int | None = None,
    max_generations: int = 50,
    max_evaluations: int | None = None,
    seed: int = 0,
    callback: GenerationCallback | None = None,
    initial: ArrayLike | None = None,
    reference_point: ArrayLike | None = None,
) -> OptimiseResult:
    """NSGA-III for three or more objectives (two work too), with bounds and constraints.

    The population is spread over Das-Dennis reference directions: ``divisions`` per objective
    (default 12 for three objectives, 7 for four, 5 for five, 4 beyond; 99 for two) give
    ``C(divisions + m - 1, m - 1)`` directions, and the population defaults to that many members
    (never fewer). The rest is as for ``nsga2``.
    """
    nsga3_module = require("pymoo.algorithms.moo.nsga3", feature=_FEATURE)
    m = problem.n_objectives
    parts = divisions if divisions is not None else {2: 99, 3: 12, 4: 7, 5: 5}.get(m, 4)
    directions = reference_directions(m, parts)
    members = max(population or 0, directions.shape[0])
    return _evolve(
        problem,
        evaluator,
        name="nsga3",
        build=lambda unit: nsga3_module.NSGA3(
            pop_size=members, ref_dirs=directions, sampling=unit, eliminate_duplicates=True
        ),
        members=members,
        max_generations=max_generations,
        max_evaluations=max_evaluations,
        seed=seed,
        callback=callback,
        initial=initial,
        reference_point=reference_point,
    )
