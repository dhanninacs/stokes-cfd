"""The ``function`` engine (ADR-0016 point 8): design points evaluated from expressions alone.

A design point is the base case with the point's inputs applied as parameter overrides.
``PointResolver`` answers expression names against a private ``Project`` rebuilt from the
definition's snapshot: an overridden quantity takes the point's value, a quantity defined by an
expression is evaluated afresh through the resolver (so ``dp_dyn = 0.5 * rho * U_in**2`` follows
an overridden ``U_in``), and any other quantity keeps its stored value. Every evaluation is
unit-checked by ``stokes.units``; no project is ever written to, so the live one cannot change.
A point fails, with the reason in its result, when an input is missing, an override does not fit
or an expression cannot be evaluated; the other points carry on.

Import rule: the standard library, ``stokes.model``, ``stokes.units`` and the study layer; no
numpy.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stokes.errors import CycleError, ExpressionError, StokesError, ValidationError
from stokes.explore.definition import (
    ConstraintDef,
    ResponseDef,
    StudyDefinition,
    StudyDefinitionError,
    quantity_dimension,
    resolve_target,
    si_quantity,
)
from stokes.explore.evaluate import (
    CancelToken,
    PointRequest,
    PointResult,
    ResultCallback,
    StartCallback,
)
from stokes.log import fields, get_logger
from stokes.model.nodes import QuantityValue
from stokes.model.project import ParamKey, Project
from stokes.units.dimensions import Dimension, dimension
from stokes.units.expressions import evaluate
from stokes.units.quantities import check_dimension
from stokes.units.registry import Quantity

log = get_logger(__name__)

_NUMERIC_FAILURES = (ArithmeticError, TypeError, ValueError)
"""What pint and float conversion raise outside the grammar (a complex root, an overflow)."""


class PointFailed(StokesError):
    """Why one design point could not be evaluated; becomes the point's error text."""

    code = "explore.point_failed"


class PointResolver:
    """Expression names for one design point: ``outputs`` by name first (responses already
    known), then the project's quantities with ``overrides`` applied (keys are ``(node id,
    parameter)``) and derived quantities re-evaluated."""

    def __init__(
        self,
        project: Project,
        overrides: Mapping[ParamKey, Quantity],
        outputs: Mapping[str, Quantity] | None = None,
    ) -> None:
        self._project = project
        self._overrides = overrides
        self._outputs = outputs or {}
        self._memo: dict[ParamKey, Quantity] = {}
        self._active: set[ParamKey] = set()

    def __call__(self, ref: str) -> Quantity:
        if ref in self._outputs:
            return self._outputs[ref]
        try:
            node, pname = resolve_target(self._project, ref)
        except StudyDefinitionError as exc:
            raise ExpressionError(f"unknown name {ref!r}", details={"ref": ref}) from exc
        key = (node.id, pname)
        found = self._overrides.get(key)
        if found is None:
            found = self._memo.get(key)
        if found is not None:
            return found
        value = node.params.get(pname)
        if isinstance(value, QuantityValue) and value.expr:
            if key in self._active:
                raise CycleError(f"{ref} refers to itself", details={"ref": ref})
            self._active.add(key)
            try:
                found = evaluate(
                    value.expr, self, expect=quantity_dimension(self._project, node, pname)
                )
            except ValidationError as exc:
                raise type(exc)(f"{ref}: {exc.message}", details=exc.details) from exc
            finally:
                self._active.discard(key)
        else:
            found = self._project.value_of(node.id, pname)
        self._memo[key] = found
        return found


def finite_si(q: Quantity, dim: Dimension, what: str) -> float:
    """The SI magnitude of ``q``; ``PointFailed`` for a wrong dimension, a complex or a
    non-finite value."""
    check_dimension(q, dim)
    magnitude = q.to(dim.si_unit or "dimensionless").magnitude
    if isinstance(magnitude, complex):
        raise PointFailed(f"{what}: has no real value ({magnitude})")
    number = float(magnitude)
    if not math.isfinite(number):
        raise PointFailed(f"{what}: is not finite ({number})")
    return number


@dataclass(frozen=True)
class _Target:
    key: ParamKey
    dim: Dimension


def _targets(project: Project, study: StudyDefinition) -> dict[str, _Target]:
    """Each variable's override key in the private copy (ids survive the snapshot)."""
    targets: dict[str, _Target] = {}
    for variable in study.variables:
        if project.find(variable.target_id) is None:
            raise StudyDefinitionError(
                f"variable {variable.name}: {variable.parameter} is not in the base case"
            )
        targets[variable.name] = _Target(
            (variable.target_id, variable.target_param), dimension(variable.dimension)
        )
    return targets


def _overrides(
    targets: Mapping[str, _Target], inputs: Mapping[str, float]
) -> dict[ParamKey, Quantity]:
    overrides: dict[ParamKey, Quantity] = {}
    for name, target in targets.items():
        if name not in inputs:
            raise PointFailed(f"the point has no value for variable {name}")
        value = inputs[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise PointFailed(f"variable {name} has no finite value ({value!r})")
        overrides[target.key] = si_quantity(float(value), target.dim)
    return overrides


def _value(text: str, resolver: PointResolver, dim: Dimension, what: str) -> float:
    try:
        q = evaluate(text, resolver, expect=dim)
    except StokesError as exc:
        raise PointFailed(f"{what}: {exc.message}", details=exc.details) from exc
    return finite_si(q, dim, what)


class FunctionEngine:
    """The ``Evaluator`` that evaluates response expressions; it makes no runs."""

    @property
    def name(self) -> str:
        return "function"

    def evaluate(
        self,
        study: StudyDefinition,
        points: Sequence[PointRequest],
        *,
        cancel: CancelToken | None = None,
        on_start: StartCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> list[PointResult]:
        project = Project.from_json(dict(study.project))  # private copy of the base case
        targets = _targets(project, study)
        responses = [(r, dimension(r.dimension)) for r in study.responses]
        results: list[PointResult] = []
        for point in points:
            if cancel is not None and cancel.cancelled:
                break
            if on_start is not None:
                on_start(point.index)
            result = self._point(project, targets, responses, point)
            results.append(result)
            if on_result is not None:
                on_result(result)
        return results

    def _point(
        self,
        project: Project,
        targets: Mapping[str, _Target],
        responses: Sequence[tuple[ResponseDef, Dimension]],
        point: PointRequest,
    ) -> PointResult:
        try:
            resolver = PointResolver(project, _overrides(targets, point.inputs))
            outputs = {
                response.name: _value(
                    response.expression, resolver, dim, f"response {response.name}"
                )
                for response, dim in responses
            }
        except StokesError as exc:
            return PointResult(point.index, "failed", error=exc.message)
        except _NUMERIC_FAILURES as exc:
            log.debug("design point failed", exc_info=True, extra=fields(index=point.index))
            return PointResult(point.index, "failed", error=f"cannot evaluate the point: {exc}")
        return PointResult(point.index, "done", outputs)


def constraint_values(
    study: StudyDefinition,
    points: Sequence[tuple[PointRequest, Mapping[str, float]]],
) -> dict[int, list[float] | str]:
    """Each constraint's value (SI, in the study's order) for evaluated points, from the point's
    overrides and its outputs; an error text instead where one cannot be evaluated."""
    project = Project.from_json(dict(study.project))
    targets = _targets(project, study)
    known = {r.name: dimension(r.dimension) for r in study.responses}
    values: dict[int, list[float] | str] = {}
    for request, outputs in points:
        try:
            resolver = PointResolver(
                project,
                _overrides(targets, request.inputs),
                {n: si_quantity(v, known[n]) for n, v in outputs.items() if n in known},
            )
            values[request.index] = [_constraint(c, resolver) for c in study.constraints]
        except StokesError as exc:
            values[request.index] = exc.message
        except _NUMERIC_FAILURES as exc:
            values[request.index] = f"cannot evaluate the constraints: {exc}"
    return values


def _constraint(constraint: ConstraintDef, resolver: PointResolver) -> float:
    dim = dimension(constraint.dimension)
    return _value(constraint.expression, resolver, dim, f"constraint {constraint.name}")
