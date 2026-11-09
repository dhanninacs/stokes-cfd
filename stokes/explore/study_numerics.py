"""The study layer's use of the numerics (ADR-0016 points 2 and 6): design points generated from
a study definition, and the Pareto front of evaluated points.

A ``sweep`` is the full factorial of its variables' levels (value lists, categories, ranges with a
count or a step, distributions with a level count); a ``doe`` asks ``stokes.explore.sampling`` for
the study's sampler, with its ``count`` and ``seed`` where the sampler takes them. Inputs come back
in SI units keyed by variable name; a categorical level becomes the value it labels.

Import rule: numpy and the exploration numerics (scipy stays lazy inside them). Only the
``study.*`` handlers import this module, inside the handler.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

import numpy as np

from stokes.explore.arrays import Json
from stokes.explore.definition import StudyDefinition, StudyDefinitionError, VariableDef
from stokes.explore.domain import domain_from_dict
from stokes.explore.errors import DomainError
from stokes.explore.model import SIZED_SAMPLERS
from stokes.explore.pareto import pareto_front
from stokes.explore.problem import Constraint, ConstraintKind, Sense
from stokes.explore.sampling import full_factorial, generate
from stokes.explore.space import Variable
from stokes.explore.study_table import PointRow, json_safe
from stokes.explore.table import DesignTable

_RELATIVE_TOLERANCE = 1e-9
"""Constraint slack that absorbs round-off: a point exactly on its limit stays feasible."""


def _has_levels(variable: VariableDef) -> bool:
    match variable.domain:
        case "values" | "categorical":
            return True
        case "range":
            return variable.count >= 2 or variable.step is not None
    return variable.count > 0


def _variables(definition: StudyDefinition) -> tuple[list[Variable], dict[str, int]]:
    variables: list[Variable] = []
    levels: dict[str, int] = {}
    for variable in definition.variables:
        try:
            domain = domain_from_dict(variable.domain_dict())
        except DomainError as exc:
            raise StudyDefinitionError(f"variable {variable.name}: {exc.message}") from exc
        variables.append(Variable(variable.name, domain))
        if variable.domain in ("uniform", "normal") and variable.count > 0:
            levels[variable.name] = variable.count
    return variables, levels


def design_points(
    definition: StudyDefinition, *, max_points: int
) -> tuple[list[dict[str, float]], dict[str, Json]]:
    """The design's points (SI inputs by variable name) and the sampler's metadata."""
    if not definition.variables:
        raise StudyDefinitionError(
            f"study {definition.name} has no variables; add one with study.add_variable"
        )
    variables, levels = _variables(definition)
    table: DesignTable
    if definition.type == "sweep" or definition.sampler == "full_factorial":
        missing = [v.name for v in definition.variables if not _has_levels(v)]
        if missing:
            raise StudyDefinitionError(
                f"a full factorial needs levels for every variable; give {missing} values, a "
                "range with a count or a step, or a distribution with a count",
                details={"variables": missing},
            )
        table = full_factorial(variables, levels or None, max_points=max_points)
    elif definition.sampler in SIZED_SAMPLERS:
        if not 1 <= definition.count <= max_points:
            raise StudyDefinitionError(
                f"the {definition.sampler} sampler needs count, the number of points "
                f"(1 to {max_points}); the study has {definition.count}",
                details={"count": definition.count},
            )
        table = generate(definition.sampler, variables, n=definition.count, seed=definition.seed)
    else:
        table = generate(definition.sampler, variables)
    points = [
        {v.name: v.value_at(float(x)) for v, x in zip(definition.variables, row, strict=True)}
        for row in table.points
    ]
    return points, cast("dict[str, Json]", json_safe(dict(table.metadata)))


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _violation(
    definition: StudyDefinition, values: list[float] | str | None
) -> tuple[float, str | None]:
    """Total scaled violation of the constraints at one point, ``inf`` when they failed."""
    if values is None or isinstance(values, str):
        return math.inf, values
    total = 0.0
    for constraint, value in zip(definition.constraints, values, strict=True):
        scale = abs(constraint.limit) or 1.0
        check = Constraint(
            constraint.name,
            cast("ConstraintKind", constraint.operator),
            constraint.limit,
            tolerance=_RELATIVE_TOLERANCE * max(1.0, abs(constraint.limit)),
            scale=scale,
        )
        total += float(check.violation(np.array([value]))[0])
    return total, None


def pareto(
    definition: StudyDefinition,
    rows: Sequence[PointRow],
    constraint_values: Mapping[int, list[float] | str],
) -> dict[str, Any]:  # Any: JSON
    """The non-dominated feasible points of the evaluated rows, with every row's rank."""
    objectives = definition.objectives
    if not objectives:
        raise StudyDefinitionError(
            f"study {definition.name} has no objectives; add them with study.set_objective"
        )
    known = {r.name for r in definition.responses}
    broken = [o.name for o in objectives if o.response is None or o.response not in known]
    if broken:
        raise StudyDefinitionError(
            f"objectives {broken} refer to no response of study {definition.name}",
            details={"objectives": broken},
        )
    names = [cast("str", o.response) for o in objectives]
    evaluated = [r for r in rows if r.status == "done" and all(n in r.outputs for n in names)]
    senses: list[Sense] = ["max" if o.direction == "maximise" else "min" for o in objectives]
    values = np.array([[r.outputs[n] for n in names] for r in evaluated], dtype=np.float64)
    checks = [
        _violation(definition, constraint_values.get(r.index))
        if definition.constraints
        else (0.0, None)
        for r in evaluated
    ]
    points: list[dict[str, Any]] = []  # Any: JSON
    front: list[int] = []
    if evaluated:
        result = pareto_front(
            values.reshape(len(evaluated), len(names)),
            senses,
            violation=np.array([c[0] for c in checks], dtype=np.float64),
        )
        on_front = {int(i) for i in result.indices}
        front = [evaluated[int(i)].index for i in result.indices]
        for i, row in enumerate(evaluated):
            violation, error = checks[i]
            entry: dict[str, Any] = {  # Any: JSON
                "index": row.index,
                "objectives": {
                    o.name: row.outputs[n] for o, n in zip(objectives, names, strict=True)
                },
                "rank": int(result.ranks[i]),
                "crowding": _finite(float(result.crowding[i])),
                "feasible": bool(result.feasible[i]),
                "violation": _finite(violation),
                "on_front": i in on_front,
            }
            if error:
                entry["error"] = error
            points.append(entry)
    evaluated_ids = {r.index for r in evaluated}
    return {
        "objectives": [
            {"name": o.name, "response": o.response, "direction": o.direction} for o in objectives
        ],
        "constraints": [
            {
                "name": c.name,
                "expression": c.expression,
                "operator": c.operator,
                "limit": c.limit,
                "dimension": c.dimension,
            }
            for c in definition.constraints
        ],
        "front": front,
        "points": points,
        "excluded": [r.index for r in rows if r.index not in evaluated_ids],
    }
