"""The surrogate protocol and what every surrogate shares: the ``Prediction`` result, scaling of
the inputs to the unit cube, input checks and the merging of identical points.

Import rule: numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, Self

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Floats, Json, as_matrix, as_vector
from stokes.explore.errors import InvalidInput, SurrogateError


@dataclass(frozen=True, eq=False)
class Prediction:
    """Predicted mean ``(n,)`` and, where the model has one, its standard deviation ``(n,)``."""

    mean: Floats
    std: Floats | None = None


class Surrogate(Protocol):
    """What the rest of the numerics (cross-validation, sensitivity, the surrogate-assisted
    optimiser) need from a surrogate."""

    kind: ClassVar[str]
    diagnostics: dict[str, Json]

    @property
    def fitted(self) -> bool: ...

    def fit(self, x: ArrayLike, y: ArrayLike, *, bounds: ArrayLike | None = None) -> Self:
        """Fit to points ``x`` ``(n, d)`` and responses ``y`` ``(n,)``; returns the model."""
        ...

    def predict(self, x: ArrayLike) -> Prediction:
        """Predict at points ``x`` ``(m, d)``."""
        ...

    def clone(self) -> Self:
        """An unfitted copy with the same hyper-parameters."""
        ...

    def options(self) -> dict[str, Json]:
        """The hyper-parameters, as accepted by ``make_surrogate``."""
        ...


class ScaledSurrogate:
    """Input scaling to the unit cube and the checks every surrogate shares."""

    kind: ClassVar[str] = ""

    def __init__(self) -> None:
        self._lo: Floats | None = None
        self._span: Floats | None = None
        self._d = 0
        self.diagnostics: dict[str, Json] = {}

    @property
    def fitted(self) -> bool:
        return self._lo is not None

    def _prepare(
        self, x: ArrayLike, y: ArrayLike, bounds: ArrayLike | None
    ) -> tuple[Floats, Floats]:
        points = as_matrix(x, name="x", finite=True)
        values = as_vector(y, name="y", finite=True)
        n, d = points.shape
        if values.shape[0] != n:
            raise InvalidInput(f"{n} points but {values.shape[0]} responses")
        if n == 0:
            raise SurrogateError("cannot fit a surrogate to no points")
        if bounds is None:
            low, high = points.min(axis=0), points.max(axis=0)
        else:
            box = as_matrix(bounds, name="bounds", columns=2, finite=True)
            if box.shape[0] != d:
                raise InvalidInput(f"bounds has {box.shape[0]} rows for {d} variables")
            low, high = box[:, 0], box[:, 1]
        span = np.where(high > low, high - low, 1.0)
        self._lo, self._span, self._d = low, span, d
        return (points - low) / span, values

    def _scale(self, x: ArrayLike) -> Floats:
        if self._lo is None or self._span is None:
            raise SurrogateError("the surrogate is not fitted; call fit first")
        points = as_matrix(x, name="x", columns=self._d, finite=True)
        return np.asarray((points - self._lo) / self._span, dtype=np.float64)


def merge_duplicates(unit: Floats, values: Floats) -> tuple[Floats, Floats, int]:
    """Average the responses of identical points (replicated centre points, for example): an
    interpolating model cannot take two values at one place."""
    rounded = np.round(unit, 12)
    _, first, inverse = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
    inverse = np.asarray(inverse).ravel()
    if len(first) == unit.shape[0]:
        return unit, values, 0
    sums = np.bincount(inverse, weights=values)
    counts = np.bincount(inverse)
    return unit[first], sums / counts, unit.shape[0] - len(first)
