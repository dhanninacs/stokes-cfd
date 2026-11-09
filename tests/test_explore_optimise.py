"""Optimisation drivers: scipy single-objective, pymoo multi-objective, surrogate-assisted."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from stokes.explore.domain import Categorical, Range, ValueList
from stokes.explore.errors import Cancelled, InvalidInput, MissingDependency, OptimiseError
from stokes.explore.functions import (
    AnalyticFunction,
    binh_korn,
    branin,
    dtlz2,
    rosenbrock,
    rosenbrock_disk,
    schaffer1,
    zdt1,
    zdt2,
)
from stokes.explore.optimise import (
    CallableEvaluator,
    Generation,
    cobyla,
    differential_evolution,
    nelder_mead,
    nsga2,
    nsga3,
    propose_candidates,
    reference_directions,
    surrogate_assisted,
)
from stokes.explore.optimise.recorder import Recorder, StopRun
from stokes.explore.pareto import hypervolume, non_dominated_mask
from stokes.explore.problem import Constraint, Objective, Problem
from stokes.explore.sampling import latin_hypercube
from stokes.explore.space import Variable
from stokes.explore.table import DesignTable
from tests.test_explore_support import block_packages


class Recording:
    """Wraps an evaluator and remembers the size of every call."""

    def __init__(self, inner: AnalyticFunction | CallableEvaluator) -> None:
        self.inner = inner
        self.batches: list[int] = []

    def evaluate(self, points: np.ndarray) -> np.ndarray:
        self.batches.append(len(points))
        return self.inner.evaluate(points)


def constrained_quadratic() -> tuple[Problem, CallableEvaluator]:
    """Minimise (x-2)^2 + (y-1)^2 subject to x + y <= 2: the optimum is (1.5, 0.5), f = 0.5."""
    variables = [Variable("x", Range(0.0, 3.0)), Variable("y", Range(0.0, 3.0))]
    problem = Problem.of(variables, ["f", "g"], [Objective("f")], [Constraint("g", "<=", 2.0)])

    def kernel(p: np.ndarray) -> np.ndarray:
        return np.column_stack([(p[:, 0] - 2.0) ** 2 + (p[:, 1] - 1.0) ** 2, p[:, 0] + p[:, 1]])

    return problem, CallableEvaluator(kernel)


# ---------------------------------------------------------------- differential evolution


def test_differential_evolution_finds_rosenbrocks_minimum() -> None:
    f = rosenbrock()
    result = differential_evolution(f.problem(), f, max_generations=200, seed=3)
    assert result.status == "converged"
    assert result.best_objective is not None and result.best_objective < 1e-6
    assert result.best_point is not None and np.allclose(result.best_point, [1.0, 1.0], atol=1e-3)
    assert result.population is not None and result.population.shape == (20, 2)
    assert result.population_responses is not None and result.population_responses.shape == (20, 1)
    assert result.n_evaluations == len(result.points) == len(result.responses)
    assert result.pareto_indices.tolist() == [result.best_index]


def test_differential_evolution_is_deterministic_and_streams_generations() -> None:
    f = rosenbrock()
    recorder = Recording(f)
    seen: list[Generation] = []
    result = differential_evolution(
        f.problem(), recorder, population=16, max_generations=12, seed=7, callback=seen.append
    )
    again = differential_evolution(f.problem(), f, population=16, max_generations=12, seed=7)
    other = differential_evolution(f.problem(), f, population=16, max_generations=12, seed=8)
    assert np.array_equal(result.points, again.points) and not np.array_equal(
        result.points, other.points
    )
    # generation 0 (the population) plus 12 generations, one evaluator batch each; a trial that
    # lands on an already evaluated point (clipped to a bound, say) is not evaluated twice
    assert len(recorder.batches) == 13 and recorder.batches[0] == 16 and max(recorder.batches) == 16
    assert sum(recorder.batches) == result.n_evaluations > 16 * 12
    assert [g.index for g in seen] == list(range(13))
    assert [len(g.points) for g in seen] == recorder.batches
    assert seen[-1].n_evaluations == result.n_evaluations
    best = [g.best_objective for g in seen]
    assert all(b is not None for b in best) and np.all(np.diff(best) <= 1e-15)  # never gets worse
    assert result.status == "max_generations" and result.n_generations == 13
    assert np.array_equal(result.convergence, best)
    assert result.generation_of.tolist() == sorted(result.generation_of.tolist())
    event = json.loads(json.dumps(seen[3].to_dict()))  # an event is plain JSON
    assert event["index"] == 3 and len(event["points"]) == 16 and event["pareto_points"] is None


def test_the_callback_can_stop_a_run() -> None:
    f = rosenbrock()
    result = differential_evolution(
        f.problem(), f, population=10, max_generations=50, seed=1, callback=lambda g: g.index >= 3
    )
    assert result.status == "cancelled" and result.n_generations == 4
    assert 36 <= result.n_evaluations <= 40 and result.best_point is not None  # the work is kept


def test_the_evaluator_can_cancel_with_an_exception() -> None:
    f = rosenbrock()

    class Stops:
        calls = 0

        def evaluate(self, points: np.ndarray) -> np.ndarray:
            self.calls += 1
            if self.calls == 4:
                raise Cancelled("the user pressed stop")
            return f.evaluate(points)

    result = nelder_mead(f.problem(), Stops(), [-1.2, 1.0], max_evaluations=100)
    assert result.status == "cancelled" and "stop" in result.message
    assert result.n_evaluations >= 3 and result.best_point is not None


def test_evaluation_budgets_are_hard_limits() -> None:
    f = rosenbrock(4)
    de = differential_evolution(
        f.problem(), f, population=20, max_generations=500, max_evaluations=110
    )
    assert de.status == "max_evaluations" and 90 < de.n_evaluations <= 100  # 5 generations of 20
    nm = nelder_mead(f.problem(), f, [-1.0] * 4, max_evaluations=37)
    assert nm.status == "max_evaluations" and nm.n_evaluations == 37
    co = cobyla(f.problem(), f, [-1.0] * 4, max_evaluations=25)
    assert co.status == "max_evaluations" and co.n_evaluations == 25
    with pytest.raises(InvalidInput, match="below the population"):
        differential_evolution(f.problem(), f, population=50, max_evaluations=40)


def test_failed_points_become_nan_rows_and_do_not_stop_the_search() -> None:
    f = rosenbrock()

    class Diverges:
        """Raises for any batch with a point above x2 = 1.5, as a solver that blows up would."""

        def __init__(self) -> None:
            self.batches: list[int] = []

        def evaluate(self, points: np.ndarray) -> np.ndarray:
            self.batches.append(len(points))
            if np.any(points[:, 1] > 1.5):
                raise RuntimeError("the solver diverged")
            return f.evaluate(points)

    evaluator = Diverges()
    result = differential_evolution(
        f.problem(), evaluator, population=24, max_generations=60, seed=2
    )
    failed = result.problem.failed(result.responses)
    assert failed.any() and result.n_failed == int(failed.sum())
    assert np.array_equal(failed, result.points[:, 1] > 1.5)  # exactly the points that raised
    assert evaluator.batches[0] == 24 and evaluator.batches[1] == 1  # the batch, then one by one
    assert result.best_point is not None and result.best_objective is not None
    assert result.best_objective < 1e-4 and result.points[result.best_index, 1] <= 1.5


def test_nan_rows_from_the_evaluator_count_as_failures_too() -> None:
    problem, evaluator = constrained_quadratic()

    def kernel(p: np.ndarray) -> np.ndarray:
        out = np.asarray(evaluator.evaluate(p)).copy()
        out[p[:, 0] > 2.2] = math.nan  # runs with a large x do not converge
        return out

    result = nelder_mead(problem, CallableEvaluator(kernel), [2.0, 0.2], max_evaluations=60)
    assert result.n_failed > 0 and result.best_index is not None
    assert result.points[result.best_index, 0] <= 2.2
    assert not result.problem.failed(result.responses[[result.best_index]]).any()
    assert result.best_objective is not None and result.best_objective < 1.0  # it moved on


def test_a_wrongly_shaped_response_is_an_error_not_a_failed_point() -> None:
    f = rosenbrock()
    bad = CallableEvaluator(lambda p: np.zeros((len(p), 3)))
    with pytest.raises(OptimiseError, match="shape"):
        differential_evolution(f.problem(), bad, population=8, max_generations=2)


def test_single_and_multi_objective_drivers_refuse_the_wrong_problem() -> None:
    zdt = zdt1(3)
    with pytest.raises(InvalidInput, match="nsga2"):
        differential_evolution(zdt.problem(), zdt)
    with pytest.raises(InvalidInput, match="nsga2"):
        nelder_mead(zdt.problem(), zdt, [0.5, 0.5, 0.5])
    rosen = rosenbrock()
    with pytest.raises(InvalidInput, match="two objectives"):
        nsga2(rosen.problem(), rosen)


def test_maximisation_and_natural_units() -> None:
    variables = [Variable("x", Range(-3.0, 3.0)), Variable("y", Range(-3.0, 3.0))]
    problem = Problem.of(variables, ["gain"], [Objective("gain", "max")])
    evaluator = CallableEvaluator(lambda p: -((p[:, 0] - 1.0) ** 2) - (p[:, 1] + 2.0) ** 2 + 5.0)
    result = differential_evolution(problem, evaluator, max_generations=100, seed=1)
    assert result.best_objective == pytest.approx(
        5.0, abs=1e-6
    )  # reported as the maximum, not negated
    assert np.allclose(result.best_point, [1.0, -2.0], atol=1e-3)
    assert result.convergence[-1] == pytest.approx(5.0, abs=1e-6)
    assert np.all(np.diff(result.convergence) >= -1e-12)  # rises towards the maximum


def test_discrete_variables_are_searched_through_their_admissible_values() -> None:
    variables = [
        Variable("size", ValueList([1.0, 2.0, 4.0, 8.0])),
        Variable("model", Categorical(["a", "b", "c"])),
        Variable("x", Range(0.0, 1.0, step=0.25, snap_to_grid=True)),
    ]
    problem = Problem.of(variables, ["f"], [Objective("f")])
    evaluator = CallableEvaluator(
        lambda p: (p[:, 0] - 4.0) ** 2 + (p[:, 1] - 2.0) ** 2 + (p[:, 2] - 0.5) ** 2
    )
    result = differential_evolution(problem, evaluator, population=12, max_generations=30, seed=4)
    assert set(result.points[:, 0]) <= {1.0, 2.0, 4.0, 8.0}
    assert set(result.points[:, 1]) <= {0.0, 1.0, 2.0}
    assert set(result.points[:, 2]) <= {0.0, 0.25, 0.5, 0.75, 1.0}
    assert result.best_point is not None and result.best_point.tolist() == [4.0, 2.0, 0.5]
    assert len({tuple(row) for row in result.points}) == result.n_evaluations  # each point once


def test_initial_points_seed_the_population() -> None:
    f = rosenbrock()
    seed = np.array([[1.0, 1.0], [0.5, 0.25]])
    result = differential_evolution(f.problem(), f, population=10, max_generations=1, initial=seed)
    assert np.allclose(result.points[:2], seed)
    assert result.best_objective == 0.0  # the known optimum was in the first generation


# ---------------------------------------------------------------- Nelder-Mead and COBYLA


def test_nelder_mead_from_the_classical_start() -> None:
    f = rosenbrock()
    seen: list[Generation] = []
    result = nelder_mead(f.problem(), f, [-1.2, 1.0], max_evaluations=400, callback=seen.append)
    assert result.status == "converged"
    assert result.best_objective is not None and result.best_objective < 1e-9
    assert np.allclose(result.best_point, [1.0, 1.0], atol=1e-4)
    assert len(seen[0].points) == 3 and seen[0].index == 0  # the initial simplex, one batch
    assert result.n_generations == len(seen) and result.n_evaluations < 250
    with pytest.raises(InvalidInput, match="x0"):
        nelder_mead(f.problem(), f, [1.0, 2.0, 3.0])
    with pytest.raises(InvalidInput, match="step"):
        nelder_mead(f.problem(), f, [0.0, 0.0], step=0.0)


@pytest.mark.parametrize("driver", ["cobyla", "nelder_mead", "differential_evolution"])
def test_constraints_are_honoured_by_every_single_objective_driver(driver: str) -> None:
    problem, evaluator = constrained_quadratic()
    if driver == "cobyla":
        result = cobyla(problem, evaluator, [0.5, 0.5], max_evaluations=200, rhobeg=0.2)
    elif driver == "nelder_mead":
        result = nelder_mead(problem, evaluator, [0.5, 0.5], max_evaluations=400)
    else:
        result = differential_evolution(problem, evaluator, max_generations=120, seed=2)
    assert result.best_point is not None and result.best_objective is not None
    assert result.best_objective == pytest.approx(0.5, abs=0.01)
    assert np.allclose(result.best_point, [1.5, 0.5], atol=0.06)
    assert result.best_point.sum() <= 2.0 + 1e-6  # the reported best is feasible


def test_differential_evolution_on_the_constrained_rosenbrock() -> None:
    f = rosenbrock_disk()
    result = differential_evolution(f.problem(), f, max_generations=300, seed=1)
    assert result.best_objective == pytest.approx(0.0456748, abs=1e-4)
    assert np.allclose(result.best_point, [0.786415, 0.617698], atol=2e-3)


def test_cobyla_takes_band_constraints() -> None:
    variables = [Variable("x", Range(0.0, 4.0)), Variable("y", Range(0.0, 4.0))]
    problem = Problem.of(
        variables,
        ["f", "g"],
        [Objective("f")],
        [Constraint("g", "==", 3.0, tolerance=0.01)],  # x + y must equal 3, give or take 0.01
    )
    evaluator = CallableEvaluator(
        lambda p: np.column_stack([p[:, 0] ** 2 + 2.0 * p[:, 1] ** 2, p[:, 0] + p[:, 1]])
    )
    result = cobyla(problem, evaluator, [1.0, 1.0], max_evaluations=200)
    assert result.best_point is not None
    assert result.best_point.sum() == pytest.approx(3.0, abs=0.02)
    assert np.allclose(
        result.best_point, [2.0, 1.0], atol=0.05
    )  # minimum of x^2 + 2y^2 on the line


def test_an_infeasible_run_reports_no_best_point() -> None:
    variables = [Variable("x", Range(0.0, 1.0))]
    problem = Problem.of(variables, ["f", "g"], [Objective("f")], [Constraint("g", ">=", 5.0)])
    evaluator = CallableEvaluator(lambda p: np.column_stack([p[:, 0], p[:, 0]]))  # g <= 1 always
    result = differential_evolution(problem, evaluator, population=8, max_generations=3)
    assert result.best_index is None and result.best_point is None and result.best_objective is None
    assert result.pareto_indices.size == 0 and np.isnan(result.convergence).all()


# ---------------------------------------------------------------- NSGA-II and NSGA-III


def test_nsga2_reaches_the_true_zdt1_front() -> None:
    pytest.importorskip("pymoo")
    f = zdt1(10)
    reference = [1.1, 1.1]
    result = nsga2(
        f.problem(), f, population=100, max_generations=80, seed=1, reference_point=reference
    )
    true = f.true_hypervolume(reference)
    assert result.hypervolume[-1] > 0.985 * true  # within 1.5 % of the exact front's hypervolume
    assert result.hypervolume[-1] == pytest.approx(
        hypervolume(result.pareto_objectives, reference), rel=1e-9
    )
    assert np.all(np.diff(result.hypervolume) >= -1e-12)  # the archive only improves
    assert 100 * 78 < result.n_evaluations <= 100 * 81 and result.status == "max_generations"
    front = result.pareto_objectives
    assert np.all(np.abs(front[:, 1] - (1.0 - np.sqrt(front[:, 0]))) < 0.05)  # on the curve
    assert front[:, 0].min() < 0.02 and front[:, 0].max() > 0.98  # and spread over all of it
    assert non_dominated_mask(front).all() and np.all(np.diff(front[:, 0]) >= 0)
    assert result.reference_point is not None and np.allclose(result.reference_point, reference)
    assert result.population is not None and result.population.shape == (100, 10)


def test_nsga2_streams_generations_with_population_and_front() -> None:
    pytest.importorskip("pymoo")
    f = zdt2(6)
    seen: list[Generation] = []
    result = nsga2(f.problem(), f, population=20, max_generations=8, seed=3, callback=seen.append)
    again = nsga2(f.problem(), f, population=20, max_generations=8, seed=3)
    assert np.array_equal(result.points, again.points)  # seeded and deterministic
    assert [g.index for g in seen] == list(range(9))
    assert all(g.population is not None and g.population.shape == (20, 6) for g in seen)
    assert all(g.population_responses is not None for g in seen)
    assert seen[-1].pareto_points is not None and seen[-1].pareto_objectives is not None
    assert seen[-1].hypervolume == pytest.approx(result.hypervolume[-1])
    assert result.reference_point is not None and np.all(result.reference_point > 1.0)  # default
    stopped = nsga2(
        f.problem(), f, population=20, max_generations=8, seed=3, callback=lambda g: g.index == 2
    )
    assert stopped.status == "cancelled" and stopped.n_generations == 3
    capped = nsga2(f.problem(), f, population=20, max_generations=50, max_evaluations=100, seed=3)
    assert capped.status == "max_evaluations" and capped.n_evaluations == 100


def test_nsga2_with_constraints_on_binh_korn() -> None:
    pytest.importorskip("pymoo")
    f = binh_korn()
    result = nsga2(f.problem(), f, population=60, max_generations=60, seed=2)
    assert result.problem.feasible(result.responses[result.pareto_indices]).all()
    true = f.front(400)
    distance = [np.min(np.linalg.norm(true - p, axis=1)) for p in result.pareto_objectives]
    assert max(distance) < 1.0  # every point of the set lies on the true front
    assert result.pareto_objectives[:, 0].max() > 130.0  # both extremes of the front are reached
    assert result.pareto_objectives[:, 1].max() > 45.0


def test_nsga2_survives_failed_points() -> None:
    pytest.importorskip("pymoo")
    f = schaffer1()

    class Fails:
        def evaluate(self, points: np.ndarray) -> np.ndarray:
            out = f.evaluate(points)
            out[points[:, 0] > 3.0] = math.nan  # a quarter of the range does not run
            return out

    result = nsga2(f.problem(), Fails(), population=24, max_generations=12, seed=5)
    assert result.n_failed > 0 and result.pareto_indices.size > 0
    assert not result.problem.failed(result.responses[result.pareto_indices]).any()
    assert np.all((result.pareto_points[:, 0] >= -0.05) & (result.pareto_points[:, 0] <= 2.05))


def test_nsga3_spreads_a_three_objective_front() -> None:
    pytest.importorskip("pymoo")
    f = dtlz2()
    reference = [1.1, 1.1, 1.1]
    result = nsga3(f.problem(), f, max_generations=40, seed=1, reference_point=reference)
    assert result.algorithm == "nsga3" and result.population is not None
    assert result.population.shape[0] == 91  # C(12 + 2, 2) reference directions
    assert result.hypervolume[-1] > 0.88 * f.true_hypervolume(reference)
    norms = np.linalg.norm(result.pareto_objectives, axis=1)
    assert np.median(norms) < 1.05 and norms.min() > 0.999  # near the unit sphere
    assert np.all(np.diff(result.hypervolume) >= -1e-12)


def test_reference_directions_count_the_compositions() -> None:
    pytest.importorskip("pymoo")
    directions = reference_directions(3, 12)
    assert directions.shape == (91, 3) and np.allclose(directions.sum(axis=1), 1.0)
    assert reference_directions(2, 9).shape == (10, 2)


def test_multi_objective_drivers_say_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    f = zdt1(3)
    block_packages(monkeypatch, "pymoo")
    for driver in (nsga2, nsga3):
        with pytest.raises(MissingDependency) as info:
            driver(f.problem(), f)
        assert info.value.code == "explore.missing_dependency"
        assert info.value.details["package"] == "pymoo" and "[explore]" in str(info.value)
    with pytest.raises(MissingDependency):
        reference_directions(3, 4)
    g = rosenbrock()  # nothing else needs pymoo
    assert (
        differential_evolution(g.problem(), g, population=8, max_generations=2).n_evaluations >= 20
    )


# ---------------------------------------------------------------- surrogate-assisted loop


def test_surrogate_assisted_loop_improves_branin_within_forty_evaluations() -> None:
    pytest.importorskip("sklearn")
    f = branin()
    seen: list[Generation] = []
    result = surrogate_assisted(
        f.problem(), f, budget=40, initial=8, surrogate="gp", seed=7, callback=seen.append
    )
    assert result.n_evaluations == 40 and result.status == "max_evaluations"
    start = result.convergence[0]
    assert start > 5.0  # a poor initial design: its best of eight points is far from 0.398
    assert result.best_objective is not None and result.best_objective < 0.5  # then found
    assert result.best_objective < start / 10
    assert [len(g.points) for g in seen] == [8] + [1] * 32  # the design, then one point per round
    assert np.all(np.diff(result.convergence) <= 1e-12)
    assert len({tuple(p) for p in result.points}) == 40  # nothing evaluated twice


def test_surrogate_assisted_loop_with_radial_basis_functions() -> None:
    f = branin()
    result = surrogate_assisted(f.problem(), f, budget=40, initial=8, surrogate="rbf", seed=7)
    assert result.best_objective is not None and result.best_objective < 1.0
    assert result.best_objective < result.convergence[0] / 10
    again = surrogate_assisted(f.problem(), f, budget=40, initial=8, surrogate="rbf", seed=7)
    assert np.array_equal(result.points, again.points)  # seeded and deterministic


def test_surrogate_assisted_loop_handles_constraints_failures_and_batches() -> None:
    problem, evaluator = constrained_quadratic()

    class Fails:
        def evaluate(self, points: np.ndarray) -> np.ndarray:
            out = np.asarray(evaluator.evaluate(points)).copy()
            out[points[:, 1] > 2.5] = math.nan
            return out

    recorder = Recording(CallableEvaluator(lambda p: Fails().evaluate(p)))
    result = surrogate_assisted(
        problem, recorder, budget=30, initial=8, surrogate="rbf", batch_size=3, seed=2
    )
    assert result.n_evaluations == 30 and recorder.batches[0] == 8
    assert all(size <= 3 for size in recorder.batches[1:]) and sum(recorder.batches) == 30
    assert result.best_point is not None and result.best_point.sum() <= 2.0 + 1e-9  # feasible
    assert result.best_objective is not None and result.best_objective < 0.8  # near 0.5


def test_surrogate_assisted_loop_traces_a_pareto_front() -> None:
    f = schaffer1()
    reference = [5.0, 5.0]
    result = surrogate_assisted(
        f.problem(), f, budget=30, initial=8, surrogate="rbf", seed=3, reference_point=reference
    )
    assert result.pareto_indices.size >= 5
    assert result.hypervolume[-1] > 0.9 * hypervolume(f.front(2000), reference)
    assert np.all(np.diff(result.hypervolume) >= -1e-12)
    assert result.problem.feasible(result.responses[result.pareto_indices]).all()


def test_surrogate_assisted_loop_starts_from_given_points_and_validates() -> None:
    f = branin()
    start = np.array([[0.0, 5.0], [5.0, 5.0], [2.0, 12.0], [8.0, 1.0], [-3.0, 9.0]])
    result = surrogate_assisted(f.problem(), f, budget=9, initial=start, surrogate="rbf", seed=1)
    assert np.array_equal(result.points[:5], start) and result.n_evaluations == 9
    for kwargs in ({"budget": 1}, {"budget": 10, "batch_size": 0}):
        with pytest.raises(InvalidInput):
            surrogate_assisted(f.problem(), f, **kwargs)  # type: ignore[arg-type]


def test_propose_candidates_returns_new_points_inside_the_bounds() -> None:
    f = branin()
    table = latin_hypercube(f.variables, 12, seed=4)
    responses = f.evaluate(table.points)
    responses[3] = math.nan  # one failed run is ignored
    candidates = propose_candidates(
        f.problem(), table.points, responses, count=4, surrogate="rbf", seed=1
    )
    assert candidates.shape == (4, 2) and f.problem().space.contains(candidates).all()
    unit = f.problem().normalise(candidates)
    seen = f.problem().normalise(table.points)
    assert min(np.linalg.norm(u - s) for u in unit for s in seen) > 1e-3  # all new
    again = propose_candidates(
        f.problem(), table.points, responses, count=4, surrogate="rbf", seed=1
    )
    assert np.array_equal(candidates, again)
    with pytest.raises(InvalidInput, match="count"):
        propose_candidates(f.problem(), table.points, responses, count=0)
    with pytest.raises(InvalidInput, match="response rows"):
        propose_candidates(f.problem(), table.points, responses[:5])


def test_the_loop_falls_back_to_radial_basis_functions_without_scikit_learn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_packages(monkeypatch, "sklearn")
    f = branin()
    result = surrogate_assisted(f.problem(), f, budget=14, initial=8, seed=1)  # "auto"
    assert result.n_evaluations == 14 and result.best_objective is not None
    with pytest.raises(MissingDependency):
        surrogate_assisted(f.problem(), f, budget=14, initial=8, surrogate="gp", seed=1)


# ---------------------------------------------------------------- results and adapters


def test_a_result_rebuilds_the_design_table() -> None:
    f = rosenbrock()
    result = differential_evolution(f.problem(), f, population=10, max_generations=3, seed=1)
    table = result.table()
    assert isinstance(table, DesignTable) and table.n_points == result.n_evaluations
    assert table.response_names == ("f",) and table.metadata["sampler"] == "differential_evolution"
    assert np.array_equal(table.points, result.points) and table.responses is not None
    assert np.array_equal(table.responses, result.responses)
    assert result.best_objective == pytest.approx(float(table.responses[result.best_index, 0]))


def test_callable_evaluator_adapts_vectorised_and_per_point_functions() -> None:
    points = np.array([[1.0, 2.0], [3.0, 4.0]])
    vectorised = CallableEvaluator(lambda p: p.sum(axis=1))
    per_point = CallableEvaluator(lambda p: np.array([p[0], p[0] * p[1]]), per_point=True)
    assert vectorised.evaluate(points).tolist() == [[3.0], [7.0]]
    assert per_point.evaluate(points).tolist() == [[1.0, 2.0], [3.0, 12.0]]


# ---------------------------------------------------------------- the evaluation recorder


def test_the_recorder_does_not_evaluate_the_same_point_twice() -> None:
    f = rosenbrock()
    problem = f.problem()
    calls = Recording(f)
    recorder = Recorder(problem, calls, algorithm="test", seed=None)
    x = np.array([[0.3, 0.7], [1.1, -0.4]])
    first = recorder.evaluate(x)
    again = recorder.evaluate(x * (1.0 + 4e-16))  # an ulp or two of scaling noise
    assert recorder.n_evaluations == 2 and calls.batches == [2]
    assert np.array_equal(first, again)
    mixed = recorder.evaluate(np.vstack([x[1], [0.0, 0.0], [0.0, 0.0]]))  # one old, one new twice
    assert recorder.n_evaluations == 3 and calls.batches == [2, 1] and mixed.shape == (3, 1)
    assert recorder.evaluate(np.zeros((0, 2))).shape == (0, 1)  # an empty batch is a no-op


def test_peek_finds_a_point_even_across_a_rounding_boundary() -> None:
    f = rosenbrock()
    recorder = Recorder(f.problem(), f, algorithm="test", seed=None)
    low, high = -2.048, 2.048
    # a point whose unit-box position sits on the edge between two cells of the identity grid
    boundary = np.array([[low + (high - low) * 1.5e-9, 0.0]])
    value = recorder.evaluate(boundary)
    nudged = boundary + np.array([[1e-15, 0.0]])
    assert np.array_equal(recorder.peek(nudged), value)
    with pytest.raises(OptimiseError, match="never evaluated"):
        recorder.peek([[1.0, 1.0]])


def test_the_recorder_enforces_its_limit_inside_a_batch() -> None:
    f = rosenbrock()
    recorder = Recorder(f.problem(), f, algorithm="test", seed=None, limit=5)
    recorder.evaluate(np.random.default_rng(0).random((3, 2)))
    with pytest.raises(StopRun) as info:
        recorder.evaluate(np.random.default_rng(1).random((4, 2)))  # two of the four fit
    assert info.value.status == "max_evaluations" and recorder.n_evaluations == 5
    with pytest.raises(StopRun):
        recorder.evaluate([[0.123, 0.456]])  # nothing fits any more
    result = recorder.finish("max_evaluations", "spent")
    assert result.n_evaluations == 5 and result.points.shape == (5, 2)
