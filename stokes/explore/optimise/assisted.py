"""Surrogate-assisted optimisation: fit, optimise on the surrogate, propose, evaluate, refit.

Every iteration fits a surrogate to the objective and to each constraint margin of the points
evaluated so far, scores a pool of candidate points with an acquisition function, and sends the
best few to the real evaluator, until the evaluation budget is spent.

* With a Gaussian process the objective is scored by expected improvement over the best feasible
  value, multiplied by the probability that every constraint holds; before any feasible point is
  known the probability of feasibility alone is maximised (Efficient Global Optimisation).
* Radial basis and polynomial surrogates have no uncertainty, so the score mixes the predicted
  value with the distance to the points already evaluated, with a weight that cycles from
  exploitation to exploration (the scheme of stochastic RBF methods).
* The pool holds a scrambled Sobol set, Gaussian jitter around the incumbent and the optimum of
  the score found by differential evolution on the cheap surrogate; candidates keep a minimum
  distance from every evaluated point, and from each other inside a batch.
* Several objectives are reduced to one at each iteration by an augmented Chebyshev
  scalarisation with random weights (ParEGO), so the loop builds a Pareto front over time; the
  result carries the non-dominated set and the hypervolume history like NSGA-II does.

Failed points count against the budget, are excluded from the surrogates and are never proposed
again. Everything runs in the unit box and is seeded.

Import rule: numpy at module level; scipy lazily; scikit-learn only through the GP surrogate.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore._imports import scipy_module, try_require
from stokes.explore.arrays import Floats, as_matrix
from stokes.explore.errors import Cancelled, InvalidInput, SurrogateError
from stokes.explore.optimise.recorder import Recorder, StopRun
from stokes.explore.optimise.types import Evaluator, GenerationCallback, OptimiseResult
from stokes.explore.problem import Problem
from stokes.explore.sampling import unit_latin_hypercube, unit_sobol
from stokes.explore.surrogate import make_surrogate
from stokes.explore.surrogate_base import Surrogate
from stokes.log import fields, get_logger

log = get_logger(__name__)

_WEIGHT_CYCLE = (0.95, 0.75, 0.5, 0.25)  # exploitation weight of the distance-based score
# a candidate closer than this (unit box) to an evaluated point is a duplicate of it
_MIN_SEPARATION = 1e-3
_BATCH_SEPARATION = 0.05  # candidates of one batch keep this distance from each other
_CHEBYSHEV_RHO = 0.05  # weight of the sum term in the augmented Chebyshev scalarisation
_DEFAULTS: dict[str, dict[str, Any]] = {  # Any: hyper-parameters of any kind
    "gp": {"restarts": 1, "warm_start": True},
    "rbf": {},
    "polynomial": {"degree": 2, "ridge": 1e-6},
}


def _default_kind() -> str:
    found = try_require("sklearn.gaussian_process", feature="the Gaussian-process surrogate")
    return "gp" if found is not None else "rbf"


def _pdf(z: Floats) -> Floats:
    return np.asarray(np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi), dtype=np.float64)


def _nearest(pool: Floats, anchors: Floats) -> Floats:
    """Distance from every pool point to the closest anchor (``inf`` when there are none)."""
    if anchors.shape[0] == 0:
        return np.full(pool.shape[0], math.inf)
    sq = (pool**2).sum(axis=1)[:, None] + (anchors**2).sum(axis=1)[None, :] - 2.0 * pool @ anchors.T
    return np.sqrt(np.maximum(sq.min(axis=1), 0.0))


class _Proposer:
    """Fits the surrogates and proposes unit-box candidates; keeps the models between calls so a
    warm-started Gaussian process refits cheaply."""

    def __init__(
        self, problem: Problem, kind: str, options: Mapping[str, Any] | None, seed: int
    ) -> None:
        self.problem = problem
        self.kind = kind
        self.options = {**_DEFAULTS.get(kind, {}), **(options or {})}
        self.rng = np.random.default_rng(seed)
        self._models: dict[str, Surrogate] = {}
        self._unit_box = np.column_stack([np.zeros(problem.n_var), np.ones(problem.n_var)])
        self.iteration = 0

    def _fit(self, key: str, unit: Floats, values: Floats) -> Surrogate:
        model = self._models.get(key)
        if model is None:
            model = make_surrogate(self.kind, **self.options)
            self._models[key] = model
        return model.fit(unit, values, bounds=self._unit_box)

    def _target(self, minimised: Floats) -> Floats:
        """The scalar to minimise: the objective, or a random Chebyshev scalarisation."""
        if minimised.shape[1] == 1:
            return minimised[:, 0]
        low, high = minimised.min(axis=0), minimised.max(axis=0)
        normalised = (minimised - low) / np.where(high > low, high - low, 1.0)
        weighted = normalised * self.rng.dirichlet(np.ones(minimised.shape[1]))
        return np.asarray(weighted.max(axis=1) + _CHEBYSHEV_RHO * weighted.sum(axis=1))

    def _random(self, count: int) -> Floats:
        return unit_latin_hypercube(self.problem.n_var, count, int(self.rng.integers(2**31)))

    def propose(self, unit: Floats, responses: Floats, count: int) -> Floats:
        """``count`` new points of the unit box given everything evaluated so far."""
        problem = self.problem
        valid = ~problem.failed(responses)
        d = problem.n_var
        if int(valid.sum()) < max(3, d + 1):
            return self._random(count)  # too little data for a model: keep sampling
        feasible = problem.feasible(responses)[valid]
        target = self._target(problem.minimised(responses[valid]))
        margins = problem.margins(responses[valid])
        try:
            objective = self._fit("objective", unit[valid], target)
            constraints = [
                self._fit(f"margin{j}", unit[valid], margins[:, j]) for j in range(margins.shape[1])
            ]
        except SurrogateError:
            log.warning("surrogate fit failed; sampling instead", extra=fields(kind=self.kind))
            return self._random(count)
        best = float(target[feasible].min()) if feasible.any() else None
        incumbent = unit[valid][np.argmin(np.where(feasible, target, math.inf))]
        if not feasible.any() and margins.size:
            incumbent = unit[valid][np.argmax(margins.min(axis=1))]
        scale = float(np.ptp(target)) + 1.0

        def acquisition(points: Floats) -> Floats:
            return self._acquisition(points, objective, constraints, best, scale)

        pool = self._pool(incumbent, acquisition)
        values = acquisition(pool)
        if best is not None and objective.predict(pool[:1]).std is None:
            values = self._blend(values, pool, unit)
        chosen = self._select(pool, values, unit, count)
        self.iteration += 1
        return chosen

    @staticmethod
    def _acquisition(
        points: Floats,
        objective: Surrogate,
        constraints: list[Surrogate],
        best: float | None,
        scale: float,
    ) -> Floats:
        """How worthwhile each point is (higher is better); the same for a point whatever batch
        it is evaluated in, so that an optimiser can climb it."""
        ndtr = scipy_module("scipy.special").ndtr
        feasibility = np.ones(points.shape[0])
        for model in constraints:
            slack = model.predict(points)
            if slack.std is None:
                feasibility *= np.where(slack.mean >= 0.0, 1.0, 1e-3)
            else:
                feasibility *= ndtr(slack.mean / np.maximum(slack.std, 1e-12))
        if best is None:
            return np.asarray(feasibility)  # nothing feasible yet: look for feasibility
        prediction = objective.predict(points)
        if prediction.std is None:  # no uncertainty: the predicted value, penalised if infeasible
            return np.asarray(-prediction.mean - scale * (1.0 - feasibility))
        std = np.maximum(prediction.std, 1e-12)
        z = (best - prediction.mean) / std
        improvement = (best - prediction.mean) * ndtr(z) + std * _pdf(z)
        return np.asarray(improvement * feasibility)

    def _blend(self, values: Floats, pool: Floats, evaluated: Floats) -> Floats:
        """Weighted rank score of predicted value and distance to the evaluated points, with the
        weight cycling from exploitation to exploration (for models without uncertainty)."""

        def rank(x: Floats) -> Floats:  # 0 for the smallest, 1 for the largest
            return np.argsort(np.argsort(x, kind="stable"), kind="stable") / max(1, len(x) - 1)

        weight = _WEIGHT_CYCLE[self.iteration % len(_WEIGHT_CYCLE)]
        return np.asarray(weight * rank(values) + (1.0 - weight) * rank(_nearest(pool, evaluated)))

    def _pool(self, incumbent: Floats, merit: Callable[[Floats], Floats]) -> Floats:
        d = self.problem.n_var
        n_global = int(min(4096, max(512, 200 * d)))
        n_global = 1 << (n_global - 1).bit_length()
        seed = int(self.rng.integers(2**31))
        pieces = [unit_sobol(d, n_global, seed)]
        for sigma in (0.01, 0.03, 0.1):
            jitter = self.rng.normal(0.0, sigma, size=(40 * d, d))
            pieces.append(np.clip(incumbent + jitter, 0.0, 1.0))
        optimum = scipy_module("scipy.optimize").differential_evolution(
            lambda u: -merit(u.T),
            [(0.0, 1.0)] * d,
            popsize=10,
            maxiter=30,
            tol=1e-8,
            rng=seed,
            polish=False,
            updating="deferred",
            vectorized=True,
        )
        pieces.append(np.asarray(optimum.x)[None, :])
        return np.vstack(pieces)

    def _select(self, pool: Floats, merit: Floats, evaluated: Floats, count: int) -> Floats:
        taken = _nearest(pool, evaluated)
        near_chosen = np.full(pool.shape[0], math.inf)
        chosen: list[Floats] = []
        for _ in range(count):
            allowed = (taken >= _MIN_SEPARATION) & (near_chosen >= _BATCH_SEPARATION)
            if allowed.any():
                pick = int(np.argmax(np.where(allowed, merit, -math.inf)))
            else:  # nothing is far enough from everything: take the roomiest point
                pick = int(np.argmax(np.minimum(taken, near_chosen)))
            chosen.append(pool[pick])
            near_chosen = np.minimum(near_chosen, _nearest(pool, pool[pick][None, :]))
        return np.vstack(chosen)


def propose_candidates(
    problem: Problem,
    points: ArrayLike,
    responses: ArrayLike,
    *,
    count: int = 1,
    surrogate: str = "auto",
    surrogate_options: Mapping[str, Any] | None = None,
    seed: int = 0,
) -> Floats:
    """Candidate points worth running next, given evaluated ``points`` and their ``responses``.

    This is one step of the surrogate-assisted loop, for the response-surface study that wants
    candidates to verify by solving: it fits the surrogates, scores a pool of points and returns
    ``count`` variable-value rows. Failed rows (NaN) are ignored but never proposed again.
    """
    if count < 1:
        raise InvalidInput(f"count must be at least 1, got {count!r}")
    kind = _default_kind() if surrogate == "auto" else surrogate
    x = as_matrix(points, name="points", columns=problem.n_var, finite=True)
    r = as_matrix(responses, name="responses", columns=len(problem.responses))
    if x.shape[0] != r.shape[0]:
        raise InvalidInput(f"{x.shape[0]} points but {r.shape[0]} response rows")
    proposer = _Proposer(problem, kind, surrogate_options, seed)
    return problem.denormalise(proposer.propose(problem.normalise(x), r, count))


def surrogate_assisted(
    problem: Problem,
    evaluator: Evaluator,
    *,
    budget: int,
    initial: int | ArrayLike | None = None,
    surrogate: str = "auto",
    surrogate_options: Mapping[str, Any] | None = None,
    batch_size: int = 1,
    seed: int = 0,
    callback: GenerationCallback | None = None,
    reference_point: ArrayLike | None = None,
) -> OptimiseResult:
    """Optimise within a budget of ``budget`` real evaluations (the initial design included).

    ``initial`` is the number of Latin-hypercube points to start from (default five per variable,
    at most half the budget) or an array of variable values to start from; generation 0 is that
    design and every later generation is one batch of ``batch_size`` proposals. ``surrogate`` is
    ``"gp"``, ``"rbf"``, ``"polynomial"`` or ``"auto"`` (a Gaussian process when scikit-learn is
    installed, else radial basis functions); ``surrogate_options`` override its defaults.
    One objective is minimised; with several, ParEGO scalarisation builds a Pareto front.
    ``reference_point`` (natural units) fixes the hypervolume reference for several objectives.
    """
    d = problem.n_var
    if budget < 2:
        raise InvalidInput(f"the budget must allow at least 2 evaluations, got {budget!r}")
    if batch_size < 1:
        raise InvalidInput(f"batch_size must be at least 1, got {batch_size!r}")
    kind = _default_kind() if surrogate == "auto" else surrogate
    if initial is None or isinstance(initial, int):
        count = initial if initial is not None else min(max(5 * d, d + 2), max(2, budget // 2))
        start = problem.denormalise(unit_latin_hypercube(d, int(min(count, budget)), seed))
    else:
        start = as_matrix(initial, name="initial", columns=d, finite=True)[:budget]
    rec = Recorder(
        problem,
        evaluator,
        algorithm="surrogate_assisted",
        seed=seed,
        callback=callback,
        limit=budget,
        reference=reference_point,
    )
    proposer = _Proposer(problem, kind, surrogate_options, seed)
    status, message = "max_evaluations", f"the budget of {budget} evaluations is spent"
    try:
        rec.evaluate(start)
        rec.emit()
        while rec.n_evaluations < budget:
            before = rec.n_evaluations
            points, responses = rec.history()
            wanted = min(batch_size, budget - before)
            rec.evaluate_unit(proposer.propose(problem.normalise(points), responses, wanted))
            rec.emit()
            if rec.n_evaluations == before:
                status, message = "stalled", "the proposals duplicated points already evaluated"
                break
    except StopRun as stop:
        status, message = stop.status, stop.message
    except Cancelled as cancelled:
        status, message = "cancelled", cancelled.message
    return rec.finish(status, message)
