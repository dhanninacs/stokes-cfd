"""Study variables and the design space: a name plus a domain, and vectorised unit-cube scaling
over whole point arrays (``domain`` has the domains themselves).

Import rule: numpy only (scipy stays lazy inside the domains).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Bools, Floats, Json, as_matrix
from stokes.explore.domain import Domain, Range, domain_from_dict
from stokes.explore.errors import DomainError


@dataclass(frozen=True)
class Variable:
    """A named study variable."""

    name: str
    domain: Domain

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DomainError("a variable needs a non-empty name")

    def to_dict(self) -> dict[str, Json]:
        return {"name": self.name, "domain": self.domain.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Variable:  # Any: untrusted JSON
        try:
            return cls(str(data["name"]), domain_from_dict(data["domain"]))
        except KeyError as exc:
            raise DomainError(f"a variable needs {exc.args[0]!r}") from exc


@dataclass(frozen=True)
class DesignSpace:
    """An ordered set of variables with vectorised unit-cube scaling over whole point arrays."""

    variables: tuple[Variable, ...]

    def __post_init__(self) -> None:
        names = [v.name for v in self.variables]
        if not names:
            raise DomainError("a design space needs at least one variable")
        if len(set(names)) != len(names):
            duplicates = sorted({n for n in names if names.count(n) > 1})
            raise DomainError(f"variable names must be unique; repeated: {duplicates}")

    @classmethod
    def of(cls, variables: Sequence[Variable] | DesignSpace) -> DesignSpace:
        """Accept a sequence of variables or an existing space."""
        if isinstance(variables, DesignSpace):
            return variables
        return cls(tuple(variables))

    @classmethod
    def from_bounds(cls, bounds: ArrayLike, names: Sequence[str] | None = None) -> DesignSpace:
        """A box of continuous ``Range`` variables from a ``(d, 2)`` array of (low, high)."""
        box = as_matrix(bounds, name="bounds", columns=2, finite=True)
        labels = names if names is not None else [f"x{i + 1}" for i in range(box.shape[0])]
        if len(labels) != box.shape[0]:
            raise DomainError(f"{box.shape[0]} bounds but {len(labels)} names")
        return cls(
            tuple(Variable(str(n), Range(lo, hi)) for n, (lo, hi) in zip(labels, box, strict=True))
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.variables)

    @property
    def size(self) -> int:
        """Number of variables."""
        return len(self.variables)

    @property
    def bounds(self) -> Floats:
        """``(d, 2)`` array of the lower and upper bound of every variable."""
        return np.array([[v.domain.low, v.domain.high] for v in self.variables], dtype=np.float64)

    @property
    def discrete(self) -> Bools:
        """Which variables take only a finite set of values."""
        return np.array([v.domain.is_discrete for v in self.variables], dtype=np.bool_)

    def column(self, name: str) -> int:
        """Column index of a variable."""
        try:
            return self.names.index(name)
        except ValueError:
            raise DomainError(f"unknown variable {name!r}; known: {self.names}") from None

    def _apply(self, points: ArrayLike, method: str) -> Floats:
        matrix = as_matrix(points, name="points", columns=self.size)
        out = np.empty_like(matrix)
        for j, variable in enumerate(self.variables):
            out[:, j] = getattr(variable.domain, method)(matrix[:, j])
        return out

    def from_unit(self, unit: ArrayLike) -> Floats:
        """Map unit-cube points ``(n, d)`` to variable values."""
        return self._apply(unit, "from_unit")

    def to_unit(self, points: ArrayLike) -> Floats:
        """Map variable values ``(n, d)`` to the unit cube (inverse of ``from_unit``)."""
        return self._apply(points, "to_unit")

    def snap(self, points: ArrayLike) -> Floats:
        """Move every value to the nearest admissible one (clip, or round to a level)."""
        return self._apply(points, "snap")

    def contains(self, points: ArrayLike, *, tol: float = 1e-9) -> Bools:
        """Which rows lie inside the bounds of every variable."""
        matrix = as_matrix(points, name="points", columns=self.size)
        box = self.bounds
        slack = tol * np.maximum(1.0, np.abs(box).max(axis=1))
        inside = (matrix >= box[:, 0] - slack) & (matrix <= box[:, 1] + slack)
        return np.asarray(inside.all(axis=1), dtype=np.bool_)
