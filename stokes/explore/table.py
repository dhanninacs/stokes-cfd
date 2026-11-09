"""The design table: variables, design points, optional responses, and how the design was made.

This is the numerics-side counterpart of the study's design table (ADR-0016): one row per design
point with its inputs and, once evaluated, its outputs. Run status, run references and the retain
flag belong to the study node model, not here; a failed point is a row of NaN responses.

Import rule: numpy only (domains are imported for scaling; scipy stays lazy there).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from stokes.explore.arrays import Bools, Floats, Json, as_matrix
from stokes.explore.domain import Categorical
from stokes.explore.errors import InvalidInput
from stokes.explore.space import DesignSpace, Variable


def _frozen_copy(values: ArrayLike, *, name: str, columns: int | None = None) -> Floats:
    array = as_matrix(values, name=name, columns=columns)
    array = np.array(array, dtype=np.float64, copy=True)
    array.setflags(write=False)
    return array


def _json_number(value: float) -> float | None:
    return None if math.isnan(value) else float(value)


@dataclass(frozen=True, eq=False, repr=False)
class DesignTable:
    """Design points ``(n, d)`` over ``variables``, with optional responses ``(n, m)``.

    Arrays are copied and made read-only. ``metadata`` records how the design was produced
    (``sampler``, ``seed``, sampler options) so that a table can be regenerated and audited.
    """

    variables: tuple[Variable, ...]
    points: Floats
    metadata: dict[str, Json] = field(default_factory=dict)
    responses: Floats | None = None
    response_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        space = DesignSpace.of(self.variables)  # validates names
        points = _frozen_copy(self.points, name="points", columns=space.size)
        if not np.isfinite(points).all():
            raise InvalidInput("design points must be finite")
        object.__setattr__(self, "variables", space.variables)
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "metadata", dict(self.metadata))
        names = tuple(self.response_names)
        if self.responses is None:
            if names:
                raise InvalidInput("response names were given without responses")
            return
        responses = _frozen_copy(self.responses, name="responses")
        if responses.shape[0] != points.shape[0]:
            raise InvalidInput(
                f"{responses.shape[0]} response rows for {points.shape[0]} design points"
            )
        if len(names) != responses.shape[1]:
            raise InvalidInput(f"{responses.shape[1]} response columns need as many names")
        if len(set(names)) != len(names) or set(names) & set(space.names):
            raise InvalidInput("response names must be unique and differ from variable names")
        object.__setattr__(self, "responses", responses)
        object.__setattr__(self, "response_names", names)

    def __repr__(self) -> str:
        responses = f", responses={list(self.response_names)}" if self.response_names else ""
        return (
            f"DesignTable({self.n_points} points x {self.n_variables} variables "
            f"{list(self.names)}{responses}, sampler={self.metadata.get('sampler')!r})"
        )

    @property
    def space(self) -> DesignSpace:
        return DesignSpace(self.variables)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.variables)

    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])

    @property
    def n_variables(self) -> int:
        return len(self.variables)

    def unit_points(self) -> Floats:
        """The design in the unit cube (the inverse of the sampler's mapping)."""
        return self.space.to_unit(self.points)

    def column(self, name: str) -> Floats:
        """One variable or response as a 1-D array."""
        if name in self.names:
            return np.asarray(self.points[:, self.names.index(name)])
        if self.responses is not None and name in self.response_names:
            return np.asarray(self.responses[:, self.response_names.index(name)])
        raise InvalidInput(f"unknown column {name!r}; known: {self.names + self.response_names}")

    def with_responses(self, responses: ArrayLike, names: Sequence[str]) -> DesignTable:
        """A copy of this table carrying ``responses`` (NaN rows mark failed points)."""
        return DesignTable(
            self.variables,
            self.points,
            self.metadata,
            responses=as_matrix(responses, name="responses"),
            response_names=tuple(names),
        )

    def select(self, rows: Bools | Sequence[int] | slice) -> DesignTable:
        """A table restricted to ``rows`` (boolean mask, indices or slice)."""
        index: Any = rows  # Any: numpy accepts masks, index lists and slices alike
        return DesignTable(
            self.variables,
            self.points[index],
            self.metadata,
            responses=None if self.responses is None else self.responses[index],
            response_names=self.response_names,
        )

    def valid_rows(self) -> Bools:
        """Rows whose responses are all finite (every row when there are no responses)."""
        if self.responses is None:
            return np.ones(self.n_points, dtype=np.bool_)
        return np.asarray(np.isfinite(self.responses).all(axis=1), dtype=np.bool_)

    def to_records(self) -> list[dict[str, Json]]:
        """Rows as dictionaries, with categorical indices replaced by their labels."""
        records: list[dict[str, Json]] = []
        for i in range(self.n_points):
            row: dict[str, Json] = {}
            for j, variable in enumerate(self.variables):
                value = float(self.points[i, j])
                domain = variable.domain
                row[variable.name] = (
                    domain.label(value) if isinstance(domain, Categorical) else value
                )
            if self.responses is not None:
                for j, name in enumerate(self.response_names):
                    row[name] = _json_number(float(self.responses[i, j]))
            records.append(row)
        return records

    def to_dict(self) -> dict[str, Json]:
        """JSON-safe form (NaN responses become null), the inverse of ``from_dict``."""
        out: dict[str, Json] = {
            "variables": [v.to_dict() for v in self.variables],
            "points": [[float(x) for x in row] for row in self.points],
            "metadata": dict(self.metadata),
        }
        if self.responses is not None:
            out["response_names"] = list(self.response_names)
            out["responses"] = [[_json_number(float(x)) for x in row] for row in self.responses]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DesignTable:  # Any: untrusted JSON
        try:
            variables = tuple(Variable.from_dict(v) for v in data["variables"])
            points = np.asarray(data["points"], dtype=np.float64).reshape(-1, len(variables))
            raw = data.get("responses")
            responses = (
                None
                if raw is None
                else np.array(
                    [[math.nan if x is None else x for x in row] for row in raw], dtype=np.float64
                )
            )
            return cls(
                variables,
                points,
                dict(data.get("metadata") or {}),
                responses=responses,
                response_names=tuple(data.get("response_names") or ()),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInput(f"malformed design table: {exc!r}") from exc
