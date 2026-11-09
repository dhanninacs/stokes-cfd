"""Analytic test problems with known answers, used by the tests and later by the ``function``
engine demos (ADR-0016, decision 8): Branin, Rosenbrock, ZDT1, ZDT2, DTLZ2, Schaffer N.1, a
constrained Rosenbrock, the constrained Binh-Korn problem and Ishigami.

Each factory returns an ``AnalyticFunction``. It is an ``Evaluator`` (``evaluate(points)`` maps
design points ``(n, d)`` to responses ``(n, m)``), it knows its variables, objectives and
constraints (``problem()``), and it carries what is known about the answer: the global minima, or
the analytic Pareto front and its hypervolume. Constraint responses are returned as columns next
to the objectives.

Import rule: numpy only.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Floats, as_matrix
from stokes.explore.domain import Range
from stokes.explore.errors import InvalidInput
from stokes.explore.problem import Constraint, Objective, Problem
from stokes.explore.space import Variable


@dataclass(frozen=True, eq=False)
class AnalyticFunction:
    """A named test problem. ``minimizers`` and ``minimum`` describe a single-objective optimum;
    ``front`` returns ``n`` objective vectors on the true Pareto front and ``true_hypervolume``
    the exact hypervolume of that front for a reference point, where they are known."""

    name: str
    variables: tuple[Variable, ...]
    response_names: tuple[str, ...]
    objectives: tuple[Objective, ...]
    kernel: Callable[[Floats], Floats]
    constraints: tuple[Constraint, ...] = ()
    minimizers: tuple[tuple[float, ...], ...] = ()
    minimum: float | None = None
    front: Callable[[int], Floats] | None = field(default=None, repr=False)
    true_hypervolume: Callable[[ArrayLike], float] | None = field(default=None, repr=False)

    @property
    def n_var(self) -> int:
        return len(self.variables)

    def evaluate(self, points: ArrayLike) -> Floats:
        """Responses ``(n, m)`` for design points ``(n, d)`` (the ``Evaluator`` protocol)."""
        x = as_matrix(points, name="points", columns=self.n_var)
        return np.asarray(self.kernel(x), dtype=np.float64)

    def problem(self) -> Problem:
        """The optimisation problem over this function's variables."""
        return Problem.of(self.variables, self.response_names, self.objectives, self.constraints)


def _box(count: int, low: float, high: float, prefix: str = "x") -> tuple[Variable, ...]:
    return tuple(Variable(f"{prefix}{i + 1}", Range(low, high)) for i in range(count))


def _responses(*columns: Floats) -> Floats:
    return np.column_stack(columns)


# ---------------------------------------------------------------- single objective


def branin() -> AnalyticFunction:
    """Branin-Hoo on [-5, 10] x [0, 15]: three global minima with value 0.397887."""
    a, b, c = 1.0, 5.1 / (4.0 * math.pi**2), 5.0 / math.pi
    r, s, t = 6.0, 10.0, 1.0 / (8.0 * math.pi)

    def kernel(x: Floats) -> Floats:
        x1, x2 = x[:, 0], x[:, 1]
        return _responses(a * (x2 - b * x1**2 + c * x1 - r) ** 2 + s * (1 - t) * np.cos(x1) + s)

    return AnalyticFunction(
        "branin",
        (Variable("x1", Range(-5.0, 10.0)), Variable("x2", Range(0.0, 15.0))),
        ("f",),
        (Objective("f"),),
        kernel,
        minimizers=((-math.pi, 12.275), (math.pi, 2.275), (9.42478, 2.475)),
        minimum=0.397887,
    )


def _rosenbrock_values(x: Floats) -> Floats:
    return np.asarray(
        (100.0 * (x[:, 1:] - x[:, :-1] ** 2) ** 2 + (1.0 - x[:, :-1]) ** 2).sum(axis=1),
        dtype=np.float64,
    )


def rosenbrock(n_var: int = 2) -> AnalyticFunction:
    """Rosenbrock's banana valley on [-2.048, 2.048]^n: minimum 0 at (1, ..., 1)."""
    if n_var < 2:
        raise InvalidInput("the Rosenbrock function needs at least two variables")
    return AnalyticFunction(
        "rosenbrock",
        _box(n_var, -2.048, 2.048),
        ("f",),
        (Objective("f"),),
        lambda x: _responses(_rosenbrock_values(x)),
        minimizers=(tuple([1.0] * n_var),),
        minimum=0.0,
    )


def rosenbrock_disk() -> AnalyticFunction:
    """Rosenbrock constrained to the unit disk: minimise f subject to x1^2 + x2^2 <= 1.

    The constrained minimum is 0.0456748 at (0.786415, 0.617698), on the circle."""
    return AnalyticFunction(
        "rosenbrock_disk",
        _box(2, -1.5, 1.5),
        ("f", "g"),
        (Objective("f"),),
        lambda x: _responses(_rosenbrock_values(x), x[:, 0] ** 2 + x[:, 1] ** 2),
        constraints=(Constraint("g", "<=", 1.0),),
        minimizers=((0.786415, 0.617698),),
        minimum=0.0456748,
    )


# ---------------------------------------------------------------- multi-objective


def _zdt_tail(x: Floats) -> Floats:
    return np.asarray(1.0 + 9.0 * x[:, 1:].mean(axis=1), dtype=np.float64)


def zdt1(n_var: int = 30) -> AnalyticFunction:
    """ZDT1: convex front f2 = 1 - sqrt(f1) on f1 in [0, 1] (all x2.. = 0)."""
    if n_var < 2:
        raise InvalidInput("ZDT1 needs at least two variables")

    def kernel(x: Floats) -> Floats:
        g = _zdt_tail(x)
        return _responses(x[:, 0], g * (1.0 - np.sqrt(x[:, 0] / g)))

    def front(n: int) -> Floats:
        f1 = np.linspace(0.0, 1.0, n)
        return _responses(f1, 1.0 - np.sqrt(f1))

    def true_hv(reference: ArrayLike) -> float:
        r = np.asarray(reference, dtype=np.float64)
        return float(r[0] * r[1] - 1.0 / 3.0)  # valid for reference >= (1, 1)

    return AnalyticFunction(
        "zdt1",
        _box(n_var, 0.0, 1.0),
        ("f1", "f2"),
        (Objective("f1"), Objective("f2")),
        kernel,
        front=front,
        true_hypervolume=true_hv,
    )


def zdt2(n_var: int = 30) -> AnalyticFunction:
    """ZDT2: concave front f2 = 1 - f1^2 on f1 in [0, 1]."""
    if n_var < 2:
        raise InvalidInput("ZDT2 needs at least two variables")

    def kernel(x: Floats) -> Floats:
        g = _zdt_tail(x)
        return _responses(x[:, 0], g * (1.0 - (x[:, 0] / g) ** 2))

    def front(n: int) -> Floats:
        f1 = np.linspace(0.0, 1.0, n)
        return _responses(f1, 1.0 - f1**2)

    def true_hv(reference: ArrayLike) -> float:
        r = np.asarray(reference, dtype=np.float64)
        return float(r[0] * r[1] - 2.0 / 3.0)  # valid for reference >= (1, 1)

    return AnalyticFunction(
        "zdt2",
        _box(n_var, 0.0, 1.0),
        ("f1", "f2"),
        (Objective("f1"), Objective("f2")),
        kernel,
        front=front,
        true_hypervolume=true_hv,
    )


def _simplex_lattice(parts: int, dimension: int) -> Floats:
    """All non-negative integer vectors of ``dimension`` entries that sum to ``parts``."""
    rows = []
    for bars in combinations(range(parts + dimension - 1), dimension - 1):
        edges = (-1, *bars, parts + dimension - 1)
        rows.append([edges[i + 1] - edges[i] - 1 for i in range(dimension)])
    return np.array(rows, dtype=np.float64)


def dtlz2(n_obj: int = 3, k: int = 10) -> AnalyticFunction:
    """DTLZ2 with ``n_obj`` objectives and ``n_obj + k - 1`` variables: the front is the part of
    the unit sphere with non-negative coordinates."""
    if n_obj < 2 or k < 1:
        raise InvalidInput("DTLZ2 needs at least two objectives and k >= 1")
    n_var = n_obj + k - 1

    def kernel(x: Floats) -> Floats:
        g = ((x[:, n_obj - 1 :] - 0.5) ** 2).sum(axis=1)
        angle = x[:, : n_obj - 1] * (math.pi / 2.0)
        out = np.empty((x.shape[0], n_obj))
        for i in range(n_obj):
            value = 1.0 + g
            for j in range(n_obj - 1 - i):
                value = value * np.cos(angle[:, j])
            if i > 0:
                value = value * np.sin(angle[:, n_obj - 1 - i])
            out[:, i] = value
        return out

    def front(n: int) -> Floats:
        parts = 1
        while math.comb(parts + n_obj - 1, n_obj - 1) < n:
            parts += 1
        lattice = _simplex_lattice(parts, n_obj)
        return np.asarray(lattice / np.linalg.norm(lattice, axis=1, keepdims=True))

    def true_hv(reference: ArrayLike) -> float:
        r = np.asarray(reference, dtype=np.float64)
        ball = math.pi ** (n_obj / 2.0) / math.gamma(n_obj / 2.0 + 1.0)
        return float(np.prod(r) - ball / 2.0**n_obj)  # valid for reference >= (1, ..., 1)

    return AnalyticFunction(
        "dtlz2",
        _box(n_var, 0.0, 1.0),
        tuple(f"f{i + 1}" for i in range(n_obj)),
        tuple(Objective(f"f{i + 1}") for i in range(n_obj)),
        kernel,
        front=front,
        true_hypervolume=true_hv,
    )


def schaffer1() -> AnalyticFunction:
    """Schaffer N.1: minimise x^2 and (x - 2)^2 on [-5, 5]; the front is x in [0, 2]."""

    def kernel(x: Floats) -> Floats:
        return _responses(x[:, 0] ** 2, (x[:, 0] - 2.0) ** 2)

    def front(n: int) -> Floats:
        x = np.linspace(0.0, 2.0, n)
        return _responses(x**2, (x - 2.0) ** 2)

    return AnalyticFunction(
        "schaffer1",
        (Variable("x1", Range(-5.0, 5.0)),),
        ("f1", "f2"),
        (Objective("f1"), Objective("f2")),
        kernel,
        front=front,
    )


def binh_korn() -> AnalyticFunction:
    """Binh-Korn: two objectives, two constraints, on [0, 5] x [0, 3].

    Minimise f1 = 4 x1^2 + 4 x2^2 and f2 = (x1 - 5)^2 + (x2 - 5)^2 subject to
    (x1 - 5)^2 + x2^2 <= 25 and (x1 - 8)^2 + (x2 + 3)^2 >= 7.7. The Pareto set is the segment
    x1 = x2 for x1 in [0, 3] followed by x2 = 3 for x1 in [3, 5]."""

    def kernel(x: Floats) -> Floats:
        x1, x2 = x[:, 0], x[:, 1]
        return _responses(
            4.0 * x1**2 + 4.0 * x2**2,
            (x1 - 5.0) ** 2 + (x2 - 5.0) ** 2,
            (x1 - 5.0) ** 2 + x2**2,
            (x1 - 8.0) ** 2 + (x2 + 3.0) ** 2,
        )

    def front(n: int) -> Floats:
        first = max(2, round(n * 3.0 / 5.0))
        t = np.linspace(0.0, 3.0, first)
        s = np.linspace(3.0, 5.0, max(2, n - first))[1:]
        points = np.vstack([np.column_stack([t, t]), np.column_stack([s, np.full(s.shape, 3.0)])])
        return kernel(points)[:, :2]

    return AnalyticFunction(
        "binh_korn",
        (Variable("x1", Range(0.0, 5.0)), Variable("x2", Range(0.0, 3.0))),
        ("f1", "f2", "g1", "g2"),
        (Objective("f1"), Objective("f2")),
        kernel,
        constraints=(Constraint("g1", "<=", 25.0), Constraint("g2", ">=", 7.7)),
        front=front,
    )


# ---------------------------------------------------------------- sensitivity benchmark


def ishigami(a: float = 7.0, b: float = 0.1) -> AnalyticFunction:
    """Ishigami: ``sin x1 + a sin^2 x2 + b x3^4 sin x1`` on [-pi, pi]^3, the classical Sobol
    test; see ``ishigami_indices`` for the exact first-order and total indices."""

    def kernel(x: Floats) -> Floats:
        return _responses(
            np.sin(x[:, 0]) + a * np.sin(x[:, 1]) ** 2 + b * x[:, 2] ** 4 * np.sin(x[:, 0])
        )

    return AnalyticFunction(
        "ishigami",
        _box(3, -math.pi, math.pi),
        ("y",),
        (Objective("y"),),
        kernel,
    )


def ishigami_indices(a: float = 7.0, b: float = 0.1) -> tuple[Floats, Floats]:
    """Exact (first-order, total) Sobol indices of the Ishigami function."""
    pi4, pi8 = math.pi**4, math.pi**8
    variance = a**2 / 8.0 + b * pi4 / 5.0 + b**2 * pi8 / 18.0 + 0.5
    v1 = 0.5 * (1.0 + b * pi4 / 5.0) ** 2
    v2 = a**2 / 8.0
    v13 = b**2 * pi8 * (1.0 / 18.0 - 1.0 / 50.0)
    first = np.array([v1, v2, 0.0]) / variance
    total = np.array([v1 + v13, v2, v13]) / variance
    return first, total


_FACTORIES: dict[str, Callable[..., AnalyticFunction]] = {
    "branin": branin,
    "rosenbrock": rosenbrock,
    "rosenbrock_disk": rosenbrock_disk,
    "zdt1": zdt1,
    "zdt2": zdt2,
    "dtlz2": dtlz2,
    "schaffer1": schaffer1,
    "binh_korn": binh_korn,
    "ishigami": ishigami,
}


def function_names() -> tuple[str, ...]:
    """Names accepted by ``function``."""
    return tuple(_FACTORIES)


def function(name: str, **options: float) -> AnalyticFunction:
    """Build a test function by name, for example ``function("zdt1", n_var=10)``."""
    try:
        factory = _FACTORIES[name.lower()]
    except KeyError:
        raise InvalidInput(
            f"unknown test function {name!r}; known: {function_names()}",
            details={"known": list(function_names())},
        ) from None
    return factory(**options)
