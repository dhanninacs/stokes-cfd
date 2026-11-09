"""Pareto utilities: non-dominated sorting, crowding distance, hypervolume, front extraction."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stokes.explore.domain import Range
from stokes.explore.errors import ParetoError
from stokes.explore.pareto import (
    crowding_distance,
    dominates,
    fronts,
    hypervolume,
    non_dominated_mask,
    non_dominated_sort,
    pareto_front,
    pareto_front_of_table,
)
from stokes.explore.problem import Constraint, Objective
from stokes.explore.space import Variable
from stokes.explore.table import DesignTable

# a hand-made set (minimisation): two copies of (1, 5), a staircase and some dominated points
HAND = np.array(
    [[1, 5], [2, 3], [3, 4], [4, 1], [2, 2], [5, 5], [1, 5]],
    dtype=float,
)


def naive_ranks(f: np.ndarray, violation: np.ndarray) -> np.ndarray:
    """Textbook peeling with constrained domination, O(n^3): the reference for the fast sort."""

    def dom(i: int, j: int) -> bool:
        feasible_i, feasible_j = violation[i] <= 0, violation[j] <= 0
        if feasible_i and feasible_j:
            return bool(np.all(f[i] <= f[j]) and np.any(f[i] < f[j]))
        if feasible_i != feasible_j:
            return feasible_i
        return bool(violation[i] < violation[j])

    ranks = -np.ones(len(f), dtype=int)
    remaining = set(range(len(f)))
    rank = 0
    while remaining:
        front = [i for i in remaining if not any(dom(j, i) for j in remaining if j != i)]
        for i in front:
            ranks[i] = rank
        remaining -= set(front)
        rank += 1
    return ranks


# ---------------------------------------------------------------- sorting


def test_dominance() -> None:
    assert dominates([1, 2], [2, 3]) and dominates([1, 2], [1, 3])
    assert not dominates([1, 2], [1, 2])  # equal points do not dominate each other
    assert not dominates([1, 3], [2, 2])


def test_non_dominated_sort_of_a_hand_made_set() -> None:
    ranks = non_dominated_sort(HAND)
    # front 0: (1,5) twice, (2,2), (4,1); then (2,3); then (3,4); then (5,5)
    assert ranks.tolist() == [0, 1, 2, 0, 0, 3, 0]
    assert non_dominated_mask(HAND).tolist() == [True, False, False, True, True, False, True]
    assert [f.tolist() for f in fronts(ranks)] == [[0, 3, 4, 6], [1], [2], [5]]
    assert fronts([]) == []
    assert non_dominated_sort(np.empty((0, 2))).tolist() == []


def test_sort_matches_a_naive_implementation_with_ties_and_constraints() -> None:
    rng = np.random.default_rng(0)
    for objectives in (rng.random((70, 3)), rng.integers(0, 4, (80, 3)).astype(float)):
        n = len(objectives)
        assert np.array_equal(non_dominated_sort(objectives), naive_ranks(objectives, np.zeros(n)))
        violation = np.where(rng.random(n) < 0.4, rng.random(n), 0.0)
        assert np.array_equal(
            non_dominated_sort(objectives, violation), naive_ranks(objectives, violation)
        )


def test_feasible_beats_infeasible_and_smaller_violation_wins() -> None:
    f = np.array([[0.0, 0.0], [5.0, 5.0], [1.0, 1.0], [2.0, 2.0]])
    violation = np.array([3.0, 0.0, 1.0, 2.0])  # the best objectives are the most violated
    ranks = non_dominated_sort(f, violation)
    assert ranks.tolist() == [3, 0, 1, 2]


def test_sort_rejects_malformed_input() -> None:
    with pytest.raises(ParetoError, match="NaN"):
        non_dominated_sort([[1.0, math.nan]])
    with pytest.raises(ParetoError, match="violation"):
        non_dominated_sort(HAND, violation=[0.0, 1.0])


# ---------------------------------------------------------------- crowding


def test_crowding_distance() -> None:
    front = np.array([[0.0, 4.0], [1.0, 2.0], [2.0, 1.5], [4.0, 0.0]])
    distance = crowding_distance(front)
    assert distance[0] == distance[3] == math.inf  # the extremes are always kept
    # interior points: normalised gap between neighbours, summed over objectives
    assert distance[1] == pytest.approx((2 - 0) / 4 + (4 - 1.5) / 4)
    assert distance[2] == pytest.approx((4 - 1) / 4 + (2 - 0) / 4)
    assert np.all(np.isinf(crowding_distance(front[:2])))  # one or two points: all extremes


def test_crowding_distance_is_per_front() -> None:
    ranks = non_dominated_sort(HAND)
    distance = crowding_distance(HAND, ranks)
    assert math.isinf(distance[1]) and math.isinf(distance[2]) and math.isinf(distance[5])  # alone
    assert np.isfinite(distance[4]) and distance[4] > 0  # an interior member of front 0
    flat = np.array([[1.0, 1.0], [1.0, 2.0], [1.0, 3.0]])
    assert np.isfinite(crowding_distance(flat)[1])  # a constant objective adds nothing, no NaN


# ---------------------------------------------------------------- hypervolume


def test_hypervolume_of_known_fronts() -> None:
    assert hypervolume([[1, 3], [2, 2], [3, 1]], [4, 4]) == pytest.approx(6.0)
    assert hypervolume([[1, 1]], [3, 4]) == pytest.approx(2 * 3)
    staircase = [[0.0, 3.0], [1.0, 2.0], [2.0, 1.0], [3.0, 0.0]]
    assert hypervolume(staircase, [4.0, 4.0]) == pytest.approx(10.0)
    assert hypervolume([[5, 5]], [4, 4]) == 0.0  # does not dominate the reference
    assert hypervolume(np.empty((0, 2)), [1, 1]) == 0.0
    assert hypervolume([[1, 3], [2, 2], [3, 1], [3, 3]], [4, 4]) == pytest.approx(6.0)  # dominated
    assert hypervolume([[2.0], [3.0]], [5.0]) == pytest.approx(3.0)


def test_hypervolume_in_three_dimensions() -> None:
    assert hypervolume([[1, 1, 1]], [2, 2, 2]) == pytest.approx(1.0)
    # boxes of volumes 4 and 4 sharing a part of volume 2
    assert hypervolume([[1, 2, 1], [2, 1, 1]], [3, 3, 3]) == pytest.approx(6.0)
    assert hypervolume([[1, 2, 1], [2, 1, 1], [2, 2, 2]], [3, 3, 3]) == pytest.approx(6.0)
    # the octant of the unit sphere: 1 - pi/6 of the cube is dominated; a sample of 2500 points
    # gives the volume under their staircase, a little below that
    angles = np.linspace(0.0, math.pi / 2, 50)
    theta, phi = np.meshgrid(angles, angles)
    sphere = np.column_stack(
        [
            (np.sin(theta) * np.cos(phi)).ravel(),
            (np.sin(theta) * np.sin(phi)).ravel(),
            np.cos(theta).ravel(),
        ]
    )
    assert hypervolume(sphere, [1.0, 1.0, 1.0]) == pytest.approx(1 - math.pi / 6, abs=2e-2)


def inclusion_exclusion(f: np.ndarray, reference: np.ndarray) -> float:
    """Exact hypervolume of a few points: the union of their boxes by inclusion-exclusion."""
    total = 0.0
    for mask in range(1, 2 ** len(f)):
        members = f[[i for i in range(len(f)) if mask >> i & 1]]
        sign = 1.0 if members.shape[0] % 2 else -1.0
        total += sign * float(np.prod(reference - members.max(axis=0)))
    return total


@pytest.mark.parametrize("m", [3, 4, 5])
def test_hypervolume_matches_inclusion_exclusion(m: int) -> None:
    f = np.random.default_rng(m).random((9, m)) * 0.8
    reference = np.ones(m)
    assert hypervolume(f, reference) == pytest.approx(inclusion_exclusion(f, reference))
    assert hypervolume(f[::-1], reference) == pytest.approx(hypervolume(f, reference))


def test_hypervolume_with_maximised_objectives() -> None:
    # maximise both: the reference is the worst corner, bottom left
    assert hypervolume([[3, 3], [4, 1], [1, 4]], [0, 0], senses=["max", "max"]) == pytest.approx(
        11.0
    )
    mixed = hypervolume([[1.0, 4.0], [2.0, 6.0]], [5.0, 0.0], senses=["min", "max"])
    assert mixed == pytest.approx((5 - 2) * 6 + (2 - 1) * 4)
    with pytest.raises(ParetoError, match="reference"):
        hypervolume(HAND, [1, 2, 3])


# ---------------------------------------------------------------- front extraction


def test_pareto_front_with_minimise_and_maximise_flags() -> None:
    objectives = np.array(
        [[1.0, 10.0], [2.0, 8.0], [3.0, 9.0], [4.0, 4.0]]
    )  # minimise 1st, maximise 2nd
    front = pareto_front(objectives, ["min", "max"])
    assert front.indices.tolist() == [0]  # (1, 10) is better than everything on both counts
    assert front.ranks.tolist() == [0, 1, 1, 2]
    assert front.objectives.tolist() == [[1.0, 10.0]]  # natural units, not negated
    both_min = pareto_front(objectives)
    assert both_min.indices.tolist() == [0, 1, 3]  # ordered by the first objective
    assert both_min.crowding.shape == (4,)
    with pytest.raises(ParetoError, match="senses"):
        pareto_front(objectives, ["min"])
    with pytest.raises(ParetoError, match="senses"):
        pareto_front(objectives, ["min", "up"])  # type: ignore[list-item]


def test_pareto_front_respects_feasibility_and_failed_points() -> None:
    objectives = np.array([[0.0, 0.0], [1.0, 3.0], [2.0, 2.0], [3.0, 1.0], [math.nan, 1.0]])
    front = pareto_front(
        objectives, violation=[1.0, 0.0, 0.0, 0.0, 0.0]
    )  # the best point is infeasible
    assert front.indices.tolist() == [1, 2, 3]
    assert front.feasible.tolist() == [False, True, True, True, False]
    assert front.ranks.tolist() == [1, 0, 0, 0, -1]  # infeasible after feasible; failed is -1
    flagged = pareto_front(objectives, feasible=[False, True, True, True, True])
    assert flagged.indices.tolist() == [1, 2, 3]
    nothing = pareto_front(objectives[:3], violation=[1.0, 2.0, 3.0])
    assert nothing.indices.size == 0 and nothing.ranks.tolist() == [0, 1, 2]
    infinite = pareto_front(
        objectives[:3], violation=[0.0, math.inf, 0.0]
    )  # inf violation = failed
    assert infinite.ranks.tolist() == [0, -1, 1]
    with pytest.raises(ParetoError, match="not both"):
        pareto_front(objectives, violation=np.zeros(5), feasible=np.ones(5))


def test_pareto_front_of_a_design_table() -> None:
    variables = [Variable("x", Range(0, 1))]
    table = DesignTable(variables, np.linspace(0, 1, 5)[:, None]).with_responses(
        [[1.0, 2.0, 0.2], [2.0, 4.0, 0.9], [3.0, 3.0, 0.4], [4.0, math.nan, 0.1], [0.5, 1.0, 0.3]],
        ["cost", "gain", "stress"],
    )
    objectives = [Objective("cost", "min"), Objective("gain", "max")]
    front = pareto_front_of_table(table, objectives)
    assert front.indices.tolist() == [4, 0, 1]  # by cost; (3, 3) is beaten by (2, 4)
    assert front.ranks.tolist() == [0, 0, 1, -1, 0]  # the failed run is ranked -1
    limited = pareto_front_of_table(table, objectives, [Constraint("stress", "<=", 0.5)])
    assert limited.indices.tolist() == [4, 0, 2]  # the infeasible (2, 4) no longer shades (3, 3)
    assert limited.feasible.tolist() == [True, False, True, False, True]
    with pytest.raises(ParetoError, match="no responses"):
        pareto_front_of_table(DesignTable(variables, np.zeros((2, 1))), objectives)


# ---------------------------------------------------------------- invariants (hypothesis)


@settings(max_examples=60, deadline=None, derandomize=True)
@given(
    data=st.lists(
        st.tuples(st.integers(0, 5), st.integers(0, 5), st.integers(0, 5)),
        min_size=1,
        max_size=25,
    )
)
def test_ranks_satisfy_the_definition_of_a_non_dominated_sort(
    data: list[tuple[int, int, int]],
) -> None:
    f = np.array(data, dtype=float)
    ranks = non_dominated_sort(f)
    assert ranks.min() == 0 and sorted(set(ranks)) == list(range(ranks.max() + 1))
    for i in range(len(f)):
        dominators = [j for j in range(len(f)) if dominates(f[j], f[i])]
        assert all(ranks[j] < ranks[i] for j in dominators)  # nothing is dominated by a worse rank
        if ranks[i] == 0:
            assert not dominators
        else:  # every point of rank k > 0 is dominated by a point of rank k - 1
            assert any(ranks[j] == ranks[i] - 1 for j in dominators)
    # crowding distance is non-negative and the extremes of a front are infinite
    assert np.all(crowding_distance(f, ranks) >= 0.0)
