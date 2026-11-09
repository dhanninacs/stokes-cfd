"""Variable domains: what values a study variable may take, and how a point of the unit interval
maps to them and back (``space`` has ``Variable`` and ``DesignSpace`` on top of these).

A study variable (ADR-0016) is a name plus a domain: an explicit value list, a range with a count
or a step, a uniform or a truncated normal distribution, or a categorical set whose members are
stored as index levels. Every domain implements the same small protocol, so samplers, surrogates
and optimisers never branch on the kind.

``from_unit`` maps a point of the unit interval to a value through the domain's quantile function,
so a stratified or low-discrepancy design in the unit cube becomes a stratified design in
probability space whatever the distribution; ``to_unit`` is its inverse. Models that want linear
coordinates use ``DesignSpace.bounds`` instead.

Import rule: numpy at module level; scipy only inside ``Normal`` (lazily).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import numpy as np
from numpy.typing import ArrayLike, NDArray

from stokes.explore._imports import scipy_module
from stokes.explore.arrays import Floats, Json
from stokes.explore.errors import DomainError

_SIGMAS = 6.0  # default truncation of a Normal domain, in standard deviations


class Domain(Protocol):
    """The operations the numerics need from a variable domain."""

    kind: ClassVar[str]

    @property
    def low(self) -> float:
        """Smallest admissible value (the lower truncation bound for a distribution)."""
        ...

    @property
    def high(self) -> float:
        """Largest admissible value."""
        ...

    @property
    def is_discrete(self) -> bool:
        """True when only a finite set of values is admissible (value lists, categories)."""
        ...

    def levels(self, count: int | None = None) -> Floats:
        """The levels of a full factorial: intrinsic for discrete domains, ``count`` otherwise."""
        ...

    def from_unit(self, unit: ArrayLike) -> Floats:
        """Values for unit-interval points (the quantile function); input is clipped to [0, 1]."""
        ...

    def to_unit(self, values: ArrayLike) -> Floats:
        """Inverse of ``from_unit``: unit-interval position of each value."""
        ...

    def snap(self, values: ArrayLike) -> Floats:
        """The nearest admissible value: clipped to the bounds, or moved to the nearest level."""
        ...

    def to_dict(self) -> dict[str, Json]:
        """JSON-safe form, the inverse of ``domain_from_dict``."""
        ...


def _finite(value: float, what: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise DomainError(f"{what} must be a finite number, got {value!r}")
    return number


def _unit(values: ArrayLike) -> Floats:
    return np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)


def _levels_of_count(low: float, high: float, count: int) -> Floats:
    if count < 1:
        raise DomainError(f"a level count must be at least 1, got {count}")
    if count == 1:
        return np.array([(low + high) / 2.0])
    return np.linspace(low, high, count)


@dataclass(frozen=True)
class ValueList:
    """Explicit numeric values (levels). Stored sorted ascending; duplicates are an error."""

    values: Sequence[float]
    kind: ClassVar[str] = "values"

    def __post_init__(self) -> None:
        values = tuple(sorted(_finite(v, "a value") for v in self.values))
        if not values:
            raise DomainError("a value list needs at least one value")
        if len(set(values)) != len(values):
            raise DomainError(f"a value list must not repeat values: {values}")
        object.__setattr__(self, "values", values)

    @property
    def low(self) -> float:
        return float(self.values[0])

    @property
    def high(self) -> float:
        return float(self.values[-1])

    @property
    def is_discrete(self) -> bool:
        return True

    def _array(self) -> Floats:
        return np.asarray(self.values, dtype=np.float64)

    def _nearest(self, values: ArrayLike) -> NDArray[np.intp]:
        levels = self._array()
        midpoints = (levels[1:] + levels[:-1]) / 2.0
        return np.searchsorted(midpoints, np.asarray(values, dtype=np.float64))

    def levels(self, count: int | None = None) -> Floats:
        if count is not None and count != len(self.values):
            raise DomainError(f"a value list has {len(self.values)} levels, not {count}")
        return self._array()

    def from_unit(self, unit: ArrayLike) -> Floats:
        n = len(self.values)
        index = np.minimum((_unit(unit) * n).astype(np.int64), n - 1)
        return self._array()[index]

    def to_unit(self, values: ArrayLike) -> Floats:
        return (self._nearest(values) + 0.5) / len(self.values)

    def snap(self, values: ArrayLike) -> Floats:
        return self._array()[self._nearest(values)]

    def to_dict(self) -> dict[str, Json]:
        return {"kind": self.kind, "values": [float(v) for v in self.values]}


@dataclass(frozen=True)
class Range:
    """The interval [low, high], optionally with a grid given by ``count`` levels or a ``step``.

    The grid is what a full factorial uses. Continuous samplers treat the range as the whole
    interval unless ``snap_to_grid`` is set, which rounds every sampled value to the nearest
    grid level (a manufacturing resolution, for example).
    """

    low: float
    high: float
    count: int | None = None
    step: float | None = None
    snap_to_grid: bool = False
    kind: ClassVar[str] = "range"

    def __post_init__(self) -> None:
        low = _finite(self.low, "low")
        high = _finite(self.high, "high")
        if not low < high:
            raise DomainError(f"a range needs low < high, got [{low}, {high}]")
        if self.count is not None and self.step is not None:
            raise DomainError("give a range either a count or a step, not both")
        if self.count is not None and (int(self.count) != self.count or self.count < 2):
            raise DomainError(f"a range count must be an integer of at least 2, got {self.count}")
        if self.step is not None and not (0.0 < _finite(self.step, "step") <= high - low):
            raise DomainError(f"a range step must lie in (0, {high - low}], got {self.step}")
        if self.snap_to_grid and self.count is None and self.step is None:
            raise DomainError("snap_to_grid needs a count or a step")
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)

    @property
    def is_discrete(self) -> bool:
        return False

    def grid(self) -> Floats | None:
        """Grid levels from the count or the step; ``None`` for a plain interval."""
        if self.count is not None:
            return np.linspace(self.low, self.high, int(self.count))
        if self.step is not None:
            n = math.floor((self.high - self.low) / self.step + 1e-9) + 1
            return self.low + self.step * np.arange(n, dtype=np.float64)
        return None

    def levels(self, count: int | None = None) -> Floats:
        if count is not None:
            return _levels_of_count(self.low, self.high, count)
        grid = self.grid()
        if grid is None:
            raise DomainError("a range without a count or a step needs a level count")
        return grid

    def from_unit(self, unit: ArrayLike) -> Floats:
        values = self.low + _unit(unit) * (self.high - self.low)
        return self.snap(values) if self.snap_to_grid else values

    def to_unit(self, values: ArrayLike) -> Floats:
        return (np.asarray(values, dtype=np.float64) - self.low) / (self.high - self.low)

    def snap(self, values: ArrayLike) -> Floats:
        clipped = np.clip(np.asarray(values, dtype=np.float64), self.low, self.high)
        grid = self.grid()
        if not self.snap_to_grid or grid is None:
            return clipped
        spacing = float(grid[1] - grid[0])
        index = np.clip(np.rint((clipped - self.low) / spacing), 0, len(grid) - 1)
        return grid[index.astype(np.int64)]

    def to_dict(self) -> dict[str, Json]:
        out: dict[str, Json] = {"kind": self.kind, "low": self.low, "high": self.high}
        if self.count is not None:
            out["count"] = int(self.count)
        if self.step is not None:
            out["step"] = float(self.step)
        if self.snap_to_grid:
            out["snap_to_grid"] = True
        return out


@dataclass(frozen=True)
class Uniform:
    """A uniform distribution on [low, high]."""

    low: float
    high: float
    kind: ClassVar[str] = "uniform"

    def __post_init__(self) -> None:
        low = _finite(self.low, "low")
        high = _finite(self.high, "high")
        if not low < high:
            raise DomainError(f"a uniform distribution needs low < high, got [{low}, {high}]")
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)

    @property
    def is_discrete(self) -> bool:
        return False

    def levels(self, count: int | None = None) -> Floats:
        if count is None:
            raise DomainError("a uniform distribution needs a level count")
        return _levels_of_count(self.low, self.high, count)

    def from_unit(self, unit: ArrayLike) -> Floats:
        return self.low + _unit(unit) * (self.high - self.low)

    def to_unit(self, values: ArrayLike) -> Floats:
        return (np.asarray(values, dtype=np.float64) - self.low) / (self.high - self.low)

    def snap(self, values: ArrayLike) -> Floats:
        return np.clip(np.asarray(values, dtype=np.float64), self.low, self.high)

    def to_dict(self) -> dict[str, Json]:
        return {"kind": self.kind, "low": self.low, "high": self.high}


@dataclass(frozen=True)
class Normal:
    """A normal distribution truncated to [low, high].

    The bounds default to ``mean -/+ 6 std`` (NaN means "use the default"); they are always
    stored resolved, so two equal distributions compare equal.
    """

    mean: float
    std: float
    low: float = math.nan
    high: float = math.nan
    kind: ClassVar[str] = "normal"

    def __post_init__(self) -> None:
        mean = _finite(self.mean, "mean")
        std = _finite(self.std, "std")
        if std <= 0.0:
            raise DomainError(f"a normal distribution needs std > 0, got {std}")
        low = mean - _SIGMAS * std if math.isnan(self.low) else _finite(self.low, "low")
        high = mean + _SIGMAS * std if math.isnan(self.high) else _finite(self.high, "high")
        if not low < high:
            raise DomainError(f"a truncated normal needs low < high, got [{low}, {high}]")
        object.__setattr__(self, "mean", mean)
        object.__setattr__(self, "std", std)
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)

    @property
    def is_discrete(self) -> bool:
        return False

    def _dist(self) -> Any:  # Any: scipy.stats frozen distribution
        a = (self.low - self.mean) / self.std
        b = (self.high - self.mean) / self.std
        return scipy_module("scipy.stats").truncnorm(a, b, loc=self.mean, scale=self.std)

    def levels(self, count: int | None = None) -> Floats:
        if count is None:
            raise DomainError("a normal distribution needs a level count")
        if count < 1:
            raise DomainError(f"a level count must be at least 1, got {count}")
        return self.from_unit((np.arange(count) + 0.5) / count)

    def from_unit(self, unit: ArrayLike) -> Floats:
        values = np.asarray(self._dist().ppf(_unit(unit)), dtype=np.float64)
        return np.clip(values, self.low, self.high)

    def to_unit(self, values: ArrayLike) -> Floats:
        clipped = np.clip(np.asarray(values, dtype=np.float64), self.low, self.high)
        return np.asarray(self._dist().cdf(clipped), dtype=np.float64)

    def snap(self, values: ArrayLike) -> Floats:
        return np.clip(np.asarray(values, dtype=np.float64), self.low, self.high)

    def to_dict(self) -> dict[str, Json]:
        return {
            "kind": self.kind,
            "mean": self.mean,
            "std": self.std,
            "low": self.low,
            "high": self.high,
        }


@dataclass(frozen=True)
class Categorical:
    """Named categories stored as index levels 0 .. n-1 (the labels live here, the table holds
    the indices). Surrogates and correlations see the index, so they treat it as ordinal."""

    categories: Sequence[str]
    kind: ClassVar[str] = "categorical"

    def __post_init__(self) -> None:
        categories = tuple(str(c) for c in self.categories)
        if not categories:
            raise DomainError("a categorical domain needs at least one category")
        if any(not c.strip() for c in categories):
            raise DomainError("category labels must not be empty")
        if len(set(categories)) != len(categories):
            raise DomainError(f"categories must be unique: {categories}")
        object.__setattr__(self, "categories", categories)

    @property
    def low(self) -> float:
        return 0.0

    @property
    def high(self) -> float:
        return float(len(self.categories) - 1)

    @property
    def is_discrete(self) -> bool:
        return True

    def index(self, label: str) -> int:
        """Index level of a category label."""
        try:
            return tuple(self.categories).index(label)
        except ValueError:
            raise DomainError(
                f"unknown category {label!r}; known: {tuple(self.categories)}"
            ) from None

    def label(self, index: float) -> str:
        """Label of an index level (rounded to the nearest integer)."""
        position = round(float(index))
        if not 0 <= position < len(self.categories):
            raise DomainError(f"category index {index} is outside 0..{len(self.categories) - 1}")
        return str(self.categories[position])

    def levels(self, count: int | None = None) -> Floats:
        if count is not None and count != len(self.categories):
            raise DomainError(
                f"a categorical domain has {len(self.categories)} levels, not {count}"
            )
        return np.arange(len(self.categories), dtype=np.float64)

    def from_unit(self, unit: ArrayLike) -> Floats:
        n = len(self.categories)
        return np.minimum((_unit(unit) * n).astype(np.int64), n - 1).astype(np.float64)

    def to_unit(self, values: ArrayLike) -> Floats:
        return (self.snap(values) + 0.5) / len(self.categories)

    def snap(self, values: ArrayLike) -> Floats:
        index = np.rint(np.asarray(values, dtype=np.float64))
        return np.clip(index, 0.0, float(len(self.categories) - 1))

    def to_dict(self) -> dict[str, Json]:
        return {"kind": self.kind, "categories": [str(c) for c in self.categories]}


def domain_from_dict(data: Mapping[str, Any]) -> Domain:  # Any: untrusted JSON
    """Build a domain from its JSON form (``to_dict``); raises ``DomainError`` on bad input."""
    kind = data.get("kind")
    try:
        if kind == "values":
            return ValueList(data["values"])
        if kind == "range":
            return Range(
                data["low"],
                data["high"],
                count=data.get("count"),
                step=data.get("step"),
                snap_to_grid=bool(data.get("snap_to_grid", False)),
            )
        if kind == "uniform":
            return Uniform(data["low"], data["high"])
        if kind == "normal":
            low, high = data.get("low"), data.get("high")
            return Normal(
                data["mean"],
                data["std"],
                low=math.nan if low is None else low,
                high=math.nan if high is None else high,
            )
        if kind == "categorical":
            return Categorical(data["categories"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError(f"malformed {kind!r} domain: {exc!r}") from exc
    raise DomainError(f"unknown domain kind {kind!r}")
