"""The evaluation bookkeeping every driver shares.

``Recorder`` stands between a driver and the user's ``Evaluator``. It rounds points to admissible
values, remembers every evaluated point (so an algorithm that asks twice pays once), sends only
new points to the evaluator in batches, turns a raising evaluator into NaN rows after retrying
point by point, enforces an evaluation limit, tracks the best feasible point and the
non-dominated archive, and closes generations: it builds the ``Generation`` record, keeps the
convergence and hypervolume series and calls the user's callback, whose ``True`` becomes a
``StopRun``.

Import rule: numpy only.
"""

from __future__ import annotations

import contextlib
import math

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Floats, Ints, as_vector
from stokes.explore.errors import Cancelled, OptimiseError
from stokes.explore.optimise.types import (
    Evaluator,
    Generation,
    GenerationCallback,
    OptimiseResult,
)
from stokes.explore.pareto import hypervolume, non_dominated_mask
from stokes.explore.problem import Problem
from stokes.log import fields, get_logger

log = get_logger(__name__)

_HEADROOM = 0.1  # reference point margin beyond the worst first-generation objective


class StopRun(Exception):
    """Unwinds a driver (and the scipy or pymoo loop beneath it) when the run must end."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Recorder:
    """Evaluates, remembers and reports for one run (see the module docstring)."""

    def __init__(
        self,
        problem: Problem,
        evaluator: Evaluator,
        *,
        algorithm: str,
        seed: int | None,
        callback: GenerationCallback | None = None,
        auto_emit: bool = False,
        limit: int | None = None,
        reference: ArrayLike | None = None,
    ) -> None:
        self.problem = problem
        self.evaluator = evaluator
        self.algorithm = algorithm
        self.seed = seed
        self.callback = callback
        self.auto_emit = auto_emit
        self.limit = limit
        self._n_resp = len(problem.responses)
        self._rows_x: list[Floats] = []
        self._rows_r: list[Floats] = []
        self._rows_gen: list[int] = []
        self._index: dict[bytes, int] = {}
        self._generation = 0
        self._emitted = 0  # rows already reported
        self._failed = 0
        self._best: int | None = None
        self._best_value = math.inf
        self._front: Ints = np.zeros(0, dtype=np.int64)
        self._signs = np.array([-1.0 if s == "max" else 1.0 for s in problem.senses])
        self._ref_min: Floats | None = None
        if reference is not None:
            ref = as_vector(reference, name="reference", finite=True)
            if ref.shape[0] != problem.n_objectives:
                raise OptimiseError(
                    f"reference point has {ref.shape[0]} entries for "
                    f"{problem.n_objectives} objectives"
                )
            self._ref_min = ref * self._signs
        self._convergence: list[float] = []
        self._hypervolume: list[float] = []

    # ------------------------------------------------------------ evaluation

    @property
    def n_evaluations(self) -> int:
        return len(self._rows_x)

    @property
    def n_failed(self) -> int:
        return self._failed

    def _call(self, batch: Floats) -> Floats:
        try:
            raw = self.evaluator.evaluate(batch)
        except Cancelled:
            raise
        except Exception:  # an evaluator may fail for any reason; a failed point is data
            log.warning(
                "the evaluator raised", extra=fields(points=int(batch.shape[0])), exc_info=True
            )
            if batch.shape[0] == 1:
                return np.full((1, self._n_resp), math.nan)
            return np.vstack([self._call(batch[i : i + 1]) for i in range(batch.shape[0])])
        out = np.asarray(raw, dtype=np.float64)
        if out.ndim == 1 and self._n_resp == 1:
            out = out[:, None]
        if out.shape != (batch.shape[0], self._n_resp):
            raise OptimiseError(
                f"the evaluator returned shape {out.shape}, expected "
                f"({batch.shape[0]}, {self._n_resp}) for responses {self.problem.responses}"
            )
        return out

    def _keys(self, points: Floats) -> list[bytes]:
        """Identity of each point: its unit-box position on a grid of ``1e-9``, so that points a
        round trip through a solver's own scaling moved by an ulp still count as the same."""
        grid = np.rint(self.problem.normalise(points) * 1e9).astype(np.int64)
        return [row.tobytes() for row in grid]

    def evaluate(self, values: ArrayLike) -> Floats:
        """Responses ``(n, m)`` for points in variable values, evaluating only the new ones."""
        points = self.problem.space.snap(values)
        keys = self._keys(points)
        fresh: list[int] = []
        seen: set[bytes] = set()
        for i, key in enumerate(keys):
            if key not in self._index and key not in seen:
                seen.add(key)
                fresh.append(i)
        truncated = False
        if fresh and self.limit is not None:
            room = self.limit - self.n_evaluations
            if room <= 0:
                raise StopRun("max_evaluations", f"the budget of {self.limit} evaluations is spent")
            if len(fresh) > room:
                fresh, truncated = fresh[:room], True
        if fresh:
            batch = points[fresh]
            responses = self._call(batch)
            failed = self.problem.failed(responses)
            self._failed += int(failed.sum())
            for row, (x, r) in enumerate(zip(batch, responses, strict=True)):
                self._index[keys[fresh[row]]] = len(self._rows_x)
                self._rows_x.append(x)
                self._rows_r.append(r)
                self._rows_gen.append(self._generation)
            if self.auto_emit:
                self.emit()
        if truncated:
            raise StopRun("max_evaluations", f"the budget of {self.limit} evaluations is spent")
        if not keys:  # solvers sometimes ask about an empty batch (nothing was feasible)
            return np.zeros((0, self._n_resp))
        return np.vstack([self._rows_r[self._index[key]] for key in keys])

    def evaluate_unit(self, unit: ArrayLike) -> Floats:
        """Like ``evaluate`` for points of the unit box (see ``Problem.denormalise``)."""
        return self.evaluate(self.problem.denormalise(unit))

    def history(self) -> tuple[Floats, Floats]:
        """Every distinct evaluation so far: points ``(n, d)`` and responses ``(n, m)``."""
        d, m = self.problem.n_var, self._n_resp
        x = np.vstack(self._rows_x) if self._rows_x else np.zeros((0, d))
        r = np.vstack(self._rows_r) if self._rows_r else np.zeros((0, m))
        return x, r

    def peek(self, values: ArrayLike) -> Floats:
        """Responses of points that were already evaluated (cache lookups only)."""
        points = self.problem.space.snap(values)
        rows: list[Floats] = []
        for point, key in zip(points, self._keys(points), strict=True):
            index = self._index.get(key)
            if index is None:  # a grid cell boundary fell between two equal points: look nearby
                index = self._nearest_evaluated(point)
            rows.append(self._rows_r[index])
        return np.vstack(rows) if rows else np.zeros((0, self._n_resp))

    def _nearest_evaluated(self, point: Floats) -> int:
        evaluated = self.problem.normalise(np.vstack(self._rows_x)) if self._rows_x else None
        if evaluated is not None:
            distance = np.linalg.norm(evaluated - self.problem.normalise(point[None, :]), axis=1)
            nearest = int(np.argmin(distance))
            if distance[nearest] < 1e-6:
                return nearest
        raise OptimiseError("peek asked for a point that was never evaluated")

    # ------------------------------------------------------------ search state

    def _update_state(self, first: int) -> None:
        """Fold the rows from ``first`` on into the best point and the non-dominated archive."""
        problem = self.problem
        responses = np.vstack(self._rows_r[first:])
        minimised = problem.minimised(responses)
        feasible = np.flatnonzero(problem.feasible(responses))
        if feasible.size:
            best = feasible[np.argmin(minimised[feasible, 0])]
            if minimised[best, 0] < self._best_value:
                self._best_value = float(minimised[best, 0])
                self._best = first + int(best)
        if problem.n_objectives > 1 and feasible.size:
            candidates = np.concatenate([self._front, first + feasible])
            objectives = problem.minimised(np.vstack([self._rows_r[i] for i in candidates]))
            self._front = candidates[non_dominated_mask(objectives)]
            if self._ref_min is None:
                finite = minimised[np.isfinite(minimised).all(axis=1)]
                worst, best_seen = finite.max(axis=0), finite.min(axis=0)
                self._ref_min = worst + _HEADROOM * np.maximum(worst - best_seen, 1e-12)

    def _front_objectives(self) -> Floats:
        return self.problem.minimised(np.vstack([self._rows_r[i] for i in self._front]))

    def _natural(self, minimised: Floats) -> Floats:
        return np.asarray(minimised * self._signs, dtype=np.float64)

    # ------------------------------------------------------------ generations

    def emit(
        self, population: Floats | None = None, population_responses: Floats | None = None
    ) -> None:
        """Close the open generation: report the rows evaluated since the last one. Does nothing
        when there are none. Raises ``StopRun`` when the callback asks to stop."""
        first = self._emitted
        if first >= len(self._rows_x):
            return
        self._update_state(first)
        problem = self.problem
        best_objective: float | None = None
        best_point: Floats | None = None
        if self._best is not None:
            row = self._rows_r[self._best][None, :]
            best_objective = float(problem.objective_values(row)[0, 0])
            best_point = self._rows_x[self._best]
        multi = problem.n_objectives > 1
        pareto_points = pareto_objectives = None
        volume: float | None = None
        if multi and self._front.size:
            pareto_points = np.vstack([self._rows_x[i] for i in self._front])
            front_min = self._front_objectives()
            pareto_objectives = self._natural(front_min)
            if self._ref_min is not None:
                volume = hypervolume(front_min, self._ref_min)
        self._convergence.append(math.nan if best_objective is None else best_objective)
        if multi:
            self._hypervolume.append(0.0 if volume is None else volume)
        generation = Generation(
            index=self._generation,
            points=np.vstack(self._rows_x[first:]),
            responses=np.vstack(self._rows_r[first:]),
            n_evaluations=self.n_evaluations,
            n_failed=self._failed,
            best_point=best_point,
            best_objective=best_objective,
            pareto_points=pareto_points,
            pareto_objectives=pareto_objectives,
            hypervolume=volume,
            population=population,
            population_responses=population_responses,
        )
        self._emitted = len(self._rows_x)
        self._generation += 1
        if self.callback is not None and self.callback(generation):
            raise StopRun("cancelled", "stopped by the generation callback")

    # ------------------------------------------------------------ result

    def finish(
        self,
        status: str,
        message: str,
        *,
        population: Floats | None = None,
        population_responses: Floats | None = None,
    ) -> OptimiseResult:
        """Flush the open generation and assemble the ``OptimiseResult``."""
        with contextlib.suppress(StopRun):  # the run is over anyway; keep the driver's status
            self.emit(population, population_responses)
        problem = self.problem
        d, m = problem.n_var, len(problem.responses)
        points = np.vstack(self._rows_x) if self._rows_x else np.zeros((0, d))
        responses = np.vstack(self._rows_r) if self._rows_r else np.zeros((0, m))
        if problem.n_objectives == 1:
            pareto = (
                np.array([self._best], dtype=np.int64) if self._best is not None else self._front
            )
        else:
            pareto = self._front
            if pareto.size:
                pareto = pareto[np.argsort(self._front_objectives()[:, 0], kind="stable")]
        reference = None if self._ref_min is None else self._natural(self._ref_min)
        return OptimiseResult(
            algorithm=self.algorithm,
            status=status,
            message=message,
            problem=problem,
            points=points,
            responses=responses,
            generation_of=np.array(self._rows_gen, dtype=np.int64),
            best_index=self._best,
            pareto_indices=np.asarray(pareto, dtype=np.int64),
            convergence=np.array(self._convergence, dtype=np.float64),
            hypervolume=np.array(self._hypervolume, dtype=np.float64),
            reference_point=reference,
            population=population,
            population_responses=population_responses,
            seed=self.seed,
        )
