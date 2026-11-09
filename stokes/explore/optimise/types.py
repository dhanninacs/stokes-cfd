"""The optimisation protocols and records: ``Evaluator``, ``GenerationCallback``, ``Generation``
and ``OptimiseResult``.

An ``Evaluator`` turns design points into responses and is the only thing a driver knows about the
world outside: the study layer wraps the run queue (or the ``function`` engine) in one. A driver
reports every generation, a batch of evaluated points with the state of the search, to a
``GenerationCallback``; that is what the API streams as ``study.generation`` events, and
returning ``True`` from it asks the driver to stop.

Import rule: numpy only.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Floats, Ints, Json, as_matrix
from stokes.explore.problem import Problem
from stokes.explore.table import DesignTable


class Evaluator(Protocol):
    """Maps design points to responses.

    ``evaluate(points)`` takes ``(n, d)`` points and returns ``(n, m)`` responses, columns in the
    order of ``Problem.responses``; it is called with whole batches (a generation) so that a run
    queue can work on them in parallel. A failed point is a row of NaN. If the call raises, the
    driver retries the points one by one and records the ones that raise again as NaN rows;
    raising ``stokes.explore.errors.Cancelled`` stops the run cleanly instead.
    """

    def evaluate(self, points: Floats) -> Floats: ...


class GenerationCallback(Protocol):
    """Called after every generation; return ``True`` to stop the run."""

    def __call__(self, generation: Generation) -> bool | None: ...


@dataclass(frozen=True)
class CallableEvaluator:
    """Adapts a plain function to the ``Evaluator`` protocol.

    ``function`` maps a points array ``(n, d)`` to responses ``(n, m)`` (or ``(n,)``); with
    ``per_point`` it maps one point ``(d,)`` to ``(m,)`` and is looped over the batch.
    """

    function: Callable[[Floats], ArrayLike]
    per_point: bool = False

    def evaluate(self, points: Floats) -> Floats:
        if self.per_point:
            rows = [np.atleast_1d(np.asarray(self.function(p), dtype=np.float64)) for p in points]
            return np.vstack(rows)
        return as_matrix(self.function(points), name="responses")


def _json_array(values: Floats | None) -> Json:
    if values is None:
        return None
    if values.ndim == 1:
        return [None if math.isnan(v) else float(v) for v in values]
    return [[None if math.isnan(v) else float(v) for v in row] for row in values]


@dataclass(frozen=True, eq=False)
class Generation:
    """One generation of a run: a batch of newly evaluated points and the state after it.

    ``index`` 0 is the initial population or design. ``points`` and ``responses`` are the points
    evaluated in this generation (as evaluated, so rounded to admissible values), ``n_evaluations``
    and ``n_failed`` are cumulative. ``best_point`` and ``best_objective`` (first objective, natural
    units) describe the best feasible point so far, ``pareto_*`` and ``hypervolume`` the
    non-dominated feasible set so far (several objectives), ``population`` the current population
    of an evolutionary algorithm with its responses.
    """

    index: int
    points: Floats
    responses: Floats
    n_evaluations: int
    n_failed: int
    best_point: Floats | None = None
    best_objective: float | None = None
    pareto_points: Floats | None = None
    pareto_objectives: Floats | None = None
    hypervolume: float | None = None
    population: Floats | None = None
    population_responses: Floats | None = None

    def to_dict(self) -> dict[str, Json]:
        """JSON-safe form for events (NaN becomes null)."""
        return {
            "index": self.index,
            "n_evaluations": self.n_evaluations,
            "n_failed": self.n_failed,
            "points": _json_array(self.points),
            "responses": _json_array(self.responses),
            "best_point": _json_array(self.best_point),
            "best_objective": self.best_objective,
            "pareto_points": _json_array(self.pareto_points),
            "pareto_objectives": _json_array(self.pareto_objectives),
            "hypervolume": self.hypervolume,
            "population": _json_array(self.population),
            "population_responses": _json_array(self.population_responses),
        }


@dataclass(frozen=True, eq=False)
class OptimiseResult:
    """The outcome of a run.

    ``points`` and ``responses`` hold every distinct evaluation in order (failed ones as NaN
    rows) and ``generation_of`` the generation each belongs to, which is all that is needed to
    rebuild a design table (``table()``). ``best_index`` (single objective) and ``pareto_indices``
    (the feasible non-dominated set of everything evaluated, ordered by the first objective)
    index into them; ``None`` or empty when no feasible point was found. ``convergence`` is the
    best first-objective value (natural units) after each generation, ``hypervolume`` the
    hypervolume of the non-dominated set after each generation against ``reference_point``
    (several objectives only). ``population`` is the final population of an evolutionary run.
    ``status`` is ``converged``, ``max_generations``, ``max_evaluations`` or ``cancelled``.
    """

    algorithm: str
    status: str
    message: str
    problem: Problem
    points: Floats
    responses: Floats
    generation_of: Ints
    best_index: int | None
    pareto_indices: Ints
    convergence: Floats
    hypervolume: Floats
    reference_point: Floats | None
    population: Floats | None
    population_responses: Floats | None
    seed: int | None

    @property
    def n_evaluations(self) -> int:
        return int(self.points.shape[0])

    @property
    def n_failed(self) -> int:
        return int(self.problem.failed(self.responses).sum()) if self.n_evaluations else 0

    @property
    def n_generations(self) -> int:
        return int(self.convergence.shape[0])

    @property
    def best_point(self) -> Floats | None:
        return None if self.best_index is None else self.points[self.best_index]

    @property
    def best_objective(self) -> float | None:
        if self.best_index is None:
            return None
        return float(
            self.problem.objective_values(self.responses[self.best_index : self.best_index + 1])[
                0, 0
            ]
        )

    @property
    def pareto_points(self) -> Floats:
        return self.points[self.pareto_indices]

    @property
    def pareto_objectives(self) -> Floats:
        """Objective values of the non-dominated set in natural units, ``(k, n_objectives)``."""
        return self.problem.objective_values(self.responses[self.pareto_indices])

    def table(self) -> DesignTable:
        """Every evaluated point with its responses, ready for the study's design table."""
        return DesignTable(
            self.problem.space.variables,
            self.points,
            {"sampler": self.algorithm, "seed": self.seed, "n": self.n_evaluations},
            responses=self.responses,
            response_names=self.problem.responses,
        )
