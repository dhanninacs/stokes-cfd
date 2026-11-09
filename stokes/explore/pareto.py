"""Pareto utilities: non-dominated sorting, crowding distance, hypervolume and front extraction.

All low-level functions work on objective matrices ``(n, m)`` in *minimisation* form, one row per
point; ``senses`` arguments (``"min"`` or ``"max"`` per objective) convert from natural units.
Constraints follow Deb's rule: a feasible point dominates an infeasible one, and between two
infeasible points the smaller total violation dominates. Sorting is the fast non-dominated sort,
O(m n^2) time, done in blocks so that thousands of points need no n x n matrix in memory.

Import rule: numpy only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Bools, Floats, Ints, as_matrix, as_vector
from stokes.explore.errors import ParetoError
from stokes.explore.problem import Constraint, Objective, Problem, Sense
from stokes.explore.table import DesignTable

_BLOCK_CELLS = 8_000_000  # comparisons per block of the dominance matrix


def _signs(senses: Sequence[Sense] | None, m: int) -> Floats:
    if senses is None:
        return np.ones(m)
    if len(senses) != m:
        raise ParetoError(f"{len(senses)} senses for {m} objectives")
    bad = [s for s in senses if s not in ("min", "max")]
    if bad:
        raise ParetoError(f"senses must be 'min' or 'max', got {bad}")
    return np.array([-1.0 if s == "max" else 1.0 for s in senses])


def dominates(a: ArrayLike, b: ArrayLike) -> bool:
    """Whether ``a`` Pareto-dominates ``b`` (minimisation): no worse anywhere, better somewhere."""
    x = as_vector(a, name="a")
    y = as_vector(b, name="b")
    return bool(np.all(x <= y) and np.any(x < y))


def _dominance_block(f: Floats, violation: Floats | None, rows: Ints) -> Bools:
    """``out[i, j]``: does point ``rows[i]`` constrained-dominate point ``j``?"""
    fi = f[rows][:, None, :]
    pareto = (fi <= f[None, :, :]).all(axis=2) & (fi < f[None, :, :]).any(axis=2)
    if violation is None:
        return pareto
    cv_i = violation[rows][:, None]
    cv_j = violation[None, :]
    feasible_i, feasible_j = cv_i <= 0.0, cv_j <= 0.0
    return (
        (feasible_i & feasible_j & pareto)
        | (feasible_i & ~feasible_j)
        | (~feasible_i & ~feasible_j & (cv_i < cv_j))
    )


def _blocks(rows: Ints, n: int, m: int) -> list[Ints]:
    size = max(1, _BLOCK_CELLS // max(1, n * m))
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def non_dominated_sort(objectives: ArrayLike, violation: ArrayLike | None = None) -> Ints:
    """Non-domination rank of every point: 0 for the Pareto front, 1 for the front of what is
    left once rank 0 is removed, and so on. ``violation`` (>= 0, 0 means feasible) enables
    constrained domination. Objectives must not contain NaN."""
    f = as_matrix(objectives, name="objectives")
    n, m = f.shape
    if np.isnan(f).any():
        raise ParetoError("objectives contain NaN; drop failed points first")
    cv: Floats | None = None
    if violation is not None:
        cv = as_vector(violation, name="violation")
        if cv.shape[0] != n or np.isnan(cv).any():
            raise ParetoError("violation must have one non-NaN value per point")
    everything = np.arange(n, dtype=np.int64)
    dominated_by = np.zeros(n, dtype=np.int64)
    for block in _blocks(everything, n, m):
        dominated_by += _dominance_block(f, cv, block).sum(axis=0)
    ranks = np.full(n, -1, dtype=np.int64)
    current = np.flatnonzero(dominated_by == 0)
    rank = 0
    while current.size:
        ranks[current] = rank
        for block in _blocks(current, n, m):
            dominated_by -= _dominance_block(f, cv, block).sum(axis=0)
        dominated_by[current] = -1  # assigned
        current = np.flatnonzero(dominated_by == 0)
        rank += 1
    return ranks


def fronts(ranks: ArrayLike) -> list[Ints]:
    """Index arrays of each front, from rank 0 up, given the output of ``non_dominated_sort``."""
    r = np.asarray(ranks, dtype=np.int64)
    if r.size == 0:
        return []
    return [np.flatnonzero(r == k) for k in range(int(r.max()) + 1)]


def non_dominated_mask(objectives: ArrayLike, violation: ArrayLike | None = None) -> Bools:
    """Which points are on the Pareto front (rank 0)."""
    return np.asarray(non_dominated_sort(objectives, violation) == 0, dtype=np.bool_)


def crowding_distance(objectives: ArrayLike, ranks: ArrayLike | None = None) -> Floats:
    """NSGA-II crowding distance: for each objective, the normalised gap between the two
    neighbours of a point in the sorted order, summed; the extremes of a front get ``inf``.
    With ``ranks`` the distance is computed within each front, otherwise over all points."""
    f = as_matrix(objectives, name="objectives")
    n, m = f.shape
    groups = [np.arange(n)] if ranks is None else fronts(ranks)
    distance = np.zeros(n)
    for members in groups:
        if members.size <= 2:
            distance[members] = math.inf
            continue
        block = f[members]
        total = np.zeros(members.size)
        for j in range(m):
            order = np.argsort(block[:, j], kind="stable")
            column = block[order, j]
            span = column[-1] - column[0]
            total[order[0]] = total[order[-1]] = math.inf
            if span > 0.0 and math.isfinite(span):
                total[order[1:-1]] += (column[2:] - column[:-2]) / span
        distance[members] = total
    return distance


def _hv_2d(points: Floats, reference: Floats) -> float:
    order = np.argsort(points[:, 0], kind="stable")
    p = points[order]
    ceiling = np.minimum.accumulate(p[:, 1])
    widths = np.append(p[1:, 0], reference[0]) - p[:, 0]
    return float(np.sum(widths * (reference[1] - ceiling)))


def _hypervolume(points: Floats, reference: Floats) -> float:
    """Exact hypervolume by slicing along the last objective; ``points`` all dominate
    ``reference`` strictly. Dimension 1 and 2 are closed forms, 3 is O(n^2 log n)."""
    m = points.shape[1]
    if m == 1:
        return float(reference[0] - points.min())
    if m == 2:
        return _hv_2d(points, reference)
    order = np.argsort(points[:, -1], kind="stable")
    p = points[order]
    depth = p[:, -1]
    total = 0.0
    for i in range(len(p)):
        upper = depth[i + 1] if i + 1 < len(p) else reference[-1]
        if upper > depth[i]:
            slab = p[: i + 1, :-1]
            if m > 3:
                slab = slab[non_dominated_mask(slab)]
            total += float(upper - depth[i]) * _hypervolume(slab, reference[:-1])
    return total


def hypervolume(
    objectives: ArrayLike, reference: ArrayLike, senses: Sequence[Sense] | None = None
) -> float:
    """Hypervolume dominated by the points and bounded by ``reference`` (the worst corner).

    Exact for any number of objectives (closed form for two, slicing for more; the cost grows
    quickly beyond three, where pymoo's indicator is the better tool). Points that do not
    dominate the reference contribute nothing. With ``senses`` both arguments are in natural
    units and maximised objectives are negated internally.
    """
    f = as_matrix(objectives, name="objectives")
    ref = as_vector(reference, name="reference")
    if ref.shape[0] != f.shape[1]:
        raise ParetoError(f"reference has {ref.shape[0]} entries for {f.shape[1]} objectives")
    sign = _signs(senses, f.shape[1])
    f, ref = f * sign, ref * sign
    inside = f[(f < ref).all(axis=1)]
    if inside.shape[0] == 0:
        return 0.0
    if inside.shape[1] > 3:  # beyond three objectives, dropping dominated points pays for itself
        inside = inside[non_dominated_mask(inside)]
    return _hypervolume(inside, ref)


@dataclass(frozen=True, eq=False)
class ParetoFront:
    """Result of ``pareto_front``.

    ``indices`` are the rows of the feasible non-dominated points, ordered by the first
    objective; ``ranks`` is the (constrained) non-domination rank of every input row, ``-1`` for a
    failed row; ``crowding`` the crowding distance within each rank; ``feasible`` flags rows that
    evaluated and satisfy the constraints; ``objectives`` holds the front's objective values in
    natural units (not negated for maximised objectives).
    """

    indices: Ints
    ranks: Ints
    crowding: Floats
    feasible: Bools
    objectives: Floats


def pareto_front(
    objectives: ArrayLike,
    senses: Sequence[Sense] | None = None,
    *,
    violation: ArrayLike | None = None,
    feasible: ArrayLike | None = None,
) -> ParetoFront:
    """Extract the Pareto front of ``objectives`` (natural units, ``senses`` per column).

    Rows with a NaN objective, or an infinite or NaN violation, are failed points: ranked ``-1``
    and never on the front. Infeasible points (``violation > 0`` or ``feasible`` false) are ranked
    after every feasible one and are never on the front.
    """
    natural = as_matrix(objectives, name="objectives")
    n, m = natural.shape
    f = natural * _signs(senses, m)
    if violation is not None and feasible is not None:
        raise ParetoError("give violation or feasible, not both")
    cv = np.zeros(n)
    if violation is not None:
        cv = as_vector(violation, name="violation")
        if cv.shape[0] != n:
            raise ParetoError(f"violation has {cv.shape[0]} entries for {n} points")
    elif feasible is not None:
        flags = np.asarray(feasible, dtype=np.bool_)
        if flags.shape != (n,):
            raise ParetoError(f"feasible has shape {flags.shape} for {n} points")
        cv = np.where(flags, 0.0, 1.0)
    valid = ~np.isnan(f).any(axis=1) & np.isfinite(cv)
    rows = np.flatnonzero(valid)
    ranks = np.full(n, -1, dtype=np.int64)
    crowd = np.zeros(n)
    if rows.size:
        constrained = bool((cv[rows] > 0.0).any())
        ranks[rows] = non_dominated_sort(f[rows], cv[rows] if constrained else None)
        crowd[rows] = crowding_distance(f[rows], ranks[rows])
    ok = valid & (cv <= 0.0)
    on_front = np.flatnonzero((ranks == 0) & ok)
    on_front = on_front[np.argsort(f[on_front, 0], kind="stable")]
    return ParetoFront(on_front, ranks, crowd, ok, natural[on_front])


def pareto_front_of_table(
    table: DesignTable,
    objectives: Sequence[Objective],
    constraints: Sequence[Constraint] = (),
) -> ParetoFront:
    """The Pareto front of a design table's responses, with minimise/maximise flags from the
    ``objectives`` and feasibility from the ``constraints``."""
    if table.responses is None:
        raise ParetoError("the table has no responses")
    problem = Problem.of(table.variables, table.response_names, objectives, constraints)
    return pareto_front(
        problem.objective_values(table.responses),
        problem.senses,
        violation=problem.violation(table.responses),
    )
