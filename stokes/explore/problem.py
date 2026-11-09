"""Objectives, constraints and the optimisation problem (ADR-0016: ``objectives`` and
``constraints`` nodes over named ``responses``).

A problem fixes the design space, the names of the response columns an evaluator returns, which
responses are minimised or maximised and which inequalities must hold. It turns a response matrix
into the quantities the algorithms need: objective values in minimisation form, the total
constraint violation and the feasibility flag. A row with a NaN in any response the problem uses
is a failed evaluation: it is never feasible and never better than a real result.

Import rule: numpy only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Bools, Floats, Ints, as_matrix
from stokes.explore.errors import InvalidInput
from stokes.explore.space import DesignSpace, Variable

Sense = Literal["min", "max"]
ConstraintKind = Literal["<=", ">=", "=="]


@dataclass(frozen=True)
class Objective:
    """Minimise or maximise one response."""

    response: str
    sense: Sense = "min"

    def __post_init__(self) -> None:
        if self.sense not in ("min", "max"):
            raise InvalidInput(f"objective sense must be 'min' or 'max', got {self.sense!r}")


@dataclass(frozen=True)
class Constraint:
    """An inequality (or a band, for ``"=="``) on one response.

    The violation is ``max(0, r - bound - tolerance)`` for ``"<="``, ``max(0, bound - r -
    tolerance)`` for ``">="`` and ``max(0, |r - bound| - tolerance)`` for ``"=="``, divided by
    ``scale`` so that constraints in different units can be added.
    """

    response: str
    kind: ConstraintKind
    bound: float
    tolerance: float = 0.0
    scale: float = 1.0

    def __post_init__(self) -> None:
        if self.kind not in ("<=", ">=", "=="):
            raise InvalidInput(f"constraint kind must be '<=', '>=' or '==', got {self.kind!r}")
        if not math.isfinite(self.bound):
            raise InvalidInput(f"constraint bound must be finite, got {self.bound!r}")
        if not (math.isfinite(self.tolerance) and self.tolerance >= 0.0):
            raise InvalidInput(f"constraint tolerance must be >= 0, got {self.tolerance!r}")
        if not (math.isfinite(self.scale) and self.scale > 0.0):
            raise InvalidInput(f"constraint scale must be > 0, got {self.scale!r}")

    def margins(self, values: ArrayLike) -> Floats:
        """Signed slack of this constraint, ``(n, k)`` with ``k`` 1 (inequality) or 2 (band):
        positive where it holds with room to spare, negative where it is violated. Smooth, so
        it suits solvers and surrogate models that the zero-inside ``violation`` would starve."""
        r = np.asarray(values, dtype=np.float64)
        if self.kind == "<=":
            slack = (self.bound + self.tolerance - r)[:, None]
        elif self.kind == ">=":
            slack = (r - self.bound + self.tolerance)[:, None]
        else:
            slack = np.column_stack(
                [r - self.bound + self.tolerance, self.bound + self.tolerance - r]
            )
        return np.asarray(slack / self.scale, dtype=np.float64)

    def violation(self, values: ArrayLike) -> Floats:
        """Violation of this constraint for response ``values`` (0 where it holds)."""
        r = np.asarray(values, dtype=np.float64)
        if self.kind == "<=":
            excess = r - self.bound
        elif self.kind == ">=":
            excess = self.bound - r
        else:
            excess = np.abs(r - self.bound)
        return np.asarray(np.maximum(0.0, excess - self.tolerance) / self.scale, dtype=np.float64)


@dataclass(frozen=True)
class Problem:
    """Design space, response names, objectives and constraints."""

    space: DesignSpace
    responses: tuple[str, ...]
    objectives: tuple[Objective, ...]
    constraints: tuple[Constraint, ...] = ()

    def __post_init__(self) -> None:
        responses = tuple(self.responses)
        if len(set(responses)) != len(responses):
            raise InvalidInput(f"response names must be unique: {responses}")
        if not self.objectives:
            raise InvalidInput("a problem needs at least one objective")
        used = [o.response for o in self.objectives] + [c.response for c in self.constraints]
        for name in used:
            if name not in responses:
                raise InvalidInput(f"unknown response {name!r}; known: {responses}")
        object.__setattr__(self, "responses", responses)
        object.__setattr__(self, "objectives", tuple(self.objectives))
        object.__setattr__(self, "constraints", tuple(self.constraints))

    @classmethod
    def of(
        cls,
        variables: Sequence[Variable] | DesignSpace,
        responses: Sequence[str],
        objectives: Sequence[Objective],
        constraints: Sequence[Constraint] = (),
    ) -> Problem:
        """Build a problem from variables, response names, objectives and constraints."""
        return cls(
            DesignSpace.of(variables), tuple(responses), tuple(objectives), tuple(constraints)
        )

    @property
    def n_var(self) -> int:
        return self.space.size

    @property
    def n_objectives(self) -> int:
        return len(self.objectives)

    @property
    def bounds(self) -> Floats:
        """``(d, 2)`` array of variable bounds."""
        return self.space.bounds

    @property
    def senses(self) -> tuple[Sense, ...]:
        return tuple(o.sense for o in self.objectives)

    @property
    def objective_columns(self) -> Ints:
        return np.array([self.responses.index(o.response) for o in self.objectives], dtype=np.int64)

    @property
    def constraint_columns(self) -> Ints:
        return np.array(
            [self.responses.index(c.response) for c in self.constraints], dtype=np.int64
        )

    def denormalise(self, unit: ArrayLike) -> Floats:
        """Linear map from the unit box ``[0, 1]^d`` to variable values, rounded to admissible
        values (discrete levels, grid). Optimisers search in the unit box so that variables of
        very different size are treated alike."""
        box = self.bounds
        u = np.clip(as_matrix(unit, name="unit", columns=self.n_var), 0.0, 1.0)
        return self.space.snap(box[:, 0] + u * (box[:, 1] - box[:, 0]))

    def normalise(self, values: ArrayLike) -> Floats:
        """Inverse of ``denormalise`` (clipped to the unit box); a zero-width variable maps to 0."""
        box = self.bounds
        span = np.where(box[:, 1] > box[:, 0], box[:, 1] - box[:, 0], 1.0)
        x = as_matrix(values, name="values", columns=self.n_var)
        return np.asarray(np.clip((x - box[:, 0]) / span, 0.0, 1.0), dtype=np.float64)

    def _responses(self, responses: ArrayLike) -> Floats:
        return as_matrix(responses, name="responses", columns=len(self.responses))

    def objective_values(self, responses: ArrayLike) -> Floats:
        """Objective values ``(n, k)`` in their own units (maximised ones not negated)."""
        return self._responses(responses)[:, self.objective_columns]

    def minimised(self, responses: ArrayLike) -> Floats:
        """Objective values in minimisation form: maximised objectives are negated."""
        values = self.objective_values(responses)
        flip = np.array([-1.0 if s == "max" else 1.0 for s in self.senses])
        return np.asarray(values * flip, dtype=np.float64)

    def failed(self, responses: ArrayLike) -> Bools:
        """Rows with a NaN in an objective or constraint response."""
        matrix = self._responses(responses)
        columns = np.concatenate([self.objective_columns, self.constraint_columns])
        return np.asarray(np.isnan(matrix[:, columns]).any(axis=1), dtype=np.bool_)

    def violation(self, responses: ArrayLike) -> Floats:
        """Total constraint violation per row; ``inf`` for a failed row, 0 for a feasible one."""
        matrix = self._responses(responses)
        total = np.zeros(matrix.shape[0])
        for constraint, column in zip(self.constraints, self.constraint_columns, strict=True):
            with np.errstate(invalid="ignore"):
                total += constraint.violation(matrix[:, column])
        total[self.failed(matrix)] = math.inf
        return total

    def margins(self, responses: ArrayLike) -> Floats:
        """Signed constraint slacks ``(n, K)`` (a band constraint contributes two columns);
        NaN for a failed row. Positive means satisfied."""
        matrix = self._responses(responses)
        blocks = [
            constraint.margins(matrix[:, column])
            for constraint, column in zip(self.constraints, self.constraint_columns, strict=True)
        ]
        return np.hstack(blocks) if blocks else np.zeros((matrix.shape[0], 0))

    def feasible(self, responses: ArrayLike) -> Bools:
        """Rows that evaluated and satisfy every constraint."""
        return np.asarray(self.violation(responses) <= 0.0, dtype=np.bool_)
