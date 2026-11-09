"""Study definitions read from the tree (``authoring`` has the helpers that write them).

``read_definition`` turns a ``study`` node into a frozen ``StudyDefinition``: its variables (each
with the quantity it varies resolved and its dimension checked), responses, objectives and
constraints, plus the project's JSON form, the base case every design point starts from
(ADR-0016 point 3). Evaluators work from that snapshot only, so the live project is never touched
by an evaluation and an update could run beside further edits.

A variable names its quantity the way expressions do (``U_in``, ``physics.parameters.U_in``,
``regions.fluid.inlet.velocity``) or by path (``/physics/parameters/U_in``); ``resolve_target``
accepts all of them, through public ``Project`` methods only.

Import rule: the standard library, ``stokes.model`` and ``stokes.units``; no numpy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from stokes.errors import UnknownNode, ValidationError
from stokes.explore.model import STUDIES_PATH
from stokes.model.ids import stable_id
from stokes.model.nodes import Node, QuantityValue
from stokes.model.project import PARAMETERS_PATH, Project
from stokes.model.schema import NAME_RE
from stokes.units.dimensions import DIMENSIONS, Dimension, dimension
from stokes.units.quantities import quantity
from stokes.units.registry import Quantity, ureg

_SEPARATORS = re.compile(r"[/.]")


class StudyDefinitionError(ValidationError):
    """A study cannot be generated or evaluated as defined (names, references, domains)."""

    code = "explore.study_definition"


# ---------------------------------------------------------------- the snapshot


@dataclass(frozen=True)
class VariableDef:
    """A variable as evaluators see it: SI numbers, the quantity it overrides resolved."""

    name: str
    node_id: str
    parameter: str
    target_id: str
    target_param: str
    dimension: str
    domain: str
    values: tuple[float, ...] = ()
    labels: tuple[str, ...] = ()
    low: float | None = None
    high: float | None = None
    count: int = 0
    step: float | None = None
    mean: float | None = None
    std: float | None = None

    def domain_dict(self) -> dict[str, Any]:  # Any: the JSON form of stokes.explore.domain
        """The JSON form ``stokes.explore.domain.domain_from_dict`` builds the domain from."""
        match self.domain:
            case "values":
                return {"kind": "values", "values": list(self.values)}
            case "categorical":
                labels = list(self.labels) or [format(v, ".12g") for v in self.values]
                return {"kind": "categorical", "categories": labels}
            case "range":
                return {
                    "kind": "range",
                    "low": self.low,
                    "high": self.high,
                    "count": self.count or None,
                    "step": self.step,
                }
            case "uniform":
                return {"kind": "uniform", "low": self.low, "high": self.high}
        return {
            "kind": "normal",
            "mean": self.mean,
            "std": self.std,
            "low": self.low,
            "high": self.high,
        }

    def value_at(self, level: float) -> float:
        """The SI value of a sampled level: a categorical level is an index into ``values``."""
        if self.domain == "categorical":
            return float(self.values[min(len(self.values) - 1, max(0, round(level)))])
        return float(level)


@dataclass(frozen=True)
class ResponseDef:
    name: str
    node_id: str
    expression: str
    dimension: str
    label: str = ""


@dataclass(frozen=True)
class ObjectiveDef:
    """``response`` is the response's name, or None when the reference no longer resolves."""

    name: str
    node_id: str
    response: str | None
    direction: str


@dataclass(frozen=True)
class ConstraintDef:
    name: str
    node_id: str
    expression: str
    dimension: str
    operator: str
    limit: float


@dataclass(frozen=True)
class StudyDefinition:
    """A study as of one command: settings, definitions and the base case (``project``, the
    project's JSON form, which evaluators rebuild a private ``Project`` from)."""

    study_id: str
    name: str
    path: str
    type: str
    sampler: str
    seed: int
    count: int
    variables: tuple[VariableDef, ...]
    responses: tuple[ResponseDef, ...]
    objectives: tuple[ObjectiveDef, ...] = ()
    constraints: tuple[ConstraintDef, ...] = ()
    # Any: the project's JSON form, as ``Project.to_json`` makes it
    project: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class Column:
    """A design-table column: a variable or a response, with its SI unit."""

    name: str
    dimension: str
    unit: str
    source: str
    label: str = ""

    def to_json(self, display_units: str | None = None) -> dict[str, Any]:  # Any: JSON
        out: dict[str, Any] = {
            "name": self.name,
            "dimension": self.dimension,
            "unit": self.unit,
            "source": self.source,
        }
        if self.label:
            out["label"] = self.label
        if display_units is not None:
            out["display_unit"] = dimension(self.dimension).unit_for(display_units)
        return out


# ---------------------------------------------------------------- tree lookups


def find_study(project: Project, ref: str) -> Node:
    """A study by id, path or bare name (looked up under ``/studies`` first)."""
    node = project.find(f"{STUDIES_PATH}/{ref}") if NAME_RE.match(ref) else None
    if node is None:
        node = project.find(ref)
    if node is None:
        raise UnknownNode(f"no study {ref!r}", details={"ref": ref})
    if node.type != "study":
        raise ValidationError(
            f"{node.path} is a {node.type}, not a study", details={"node": node.path}
        )
    return node


def group_id(study_id: str, group: str) -> str:
    """The id of a study's group, derived from the study's so that a journal replays exactly."""
    return stable_id("g", f"{study_id}/{group}")


def members(study: Node, group: str, node_type: str) -> list[Node]:
    """The nodes of one type in one of the study's groups, in tree order."""
    holder = study.child(group)
    return [] if holder is None else [c for c in holder.children if c.type == node_type]


def resolve_target(project: Project, ref: str) -> tuple[Node, str]:
    """The node and quantity parameter a reference names, resolved like expression names.

    Accepted: ``U_in`` (a parameter of ``/physics/parameters``), a parameter's path in dots or
    slashes, and a node quantity with the parameter name last (``regions.fluid.inlet.velocity``
    or ``/regions/fluid/inlet.velocity``).
    """
    text = ref.strip()
    node = project.find(text) if text else None
    if node is not None and node.type == "parameter":
        return node, "value"
    segments = [s for s in _SEPARATORS.split(text) if s]
    if len(segments) == 1:
        group = project.find(PARAMETERS_PATH)
        candidate = group.child(segments[0]) if group is not None else None
        if candidate is not None and candidate.type == "parameter":
            return candidate, "value"
    if len(segments) >= 2:
        owner = project.find("/" + "/".join(segments[:-1]))
        if owner is not None:
            spec = project.spec_of(owner)
            if any(p.name == segments[-1] and p.kind == "quantity" for p in spec.params):
                return owner, segments[-1]
    raise StudyDefinitionError(
        f"{ref!r} names no parameter or node quantity", details={"parameter": ref}
    )


def quantity_dimension(project: Project, node: Node, param: str) -> Dimension:
    return project.param_dimension(node, project.spec_of(node).param(param))


# ---------------------------------------------------------------- reading a study


def _si(node: Node, name: str) -> float:
    value = node.params.get(name)
    return float(value.value) if isinstance(value, QuantityValue) else 0.0


def _healthy(node: Node, what: str) -> None:
    """A definition whose own expression failed holds a stale value: refuse to use it."""
    if node.status == "failed":
        raise StudyDefinitionError(
            f"{what} {node.name}: {node.meta.get('error', 'an expression failed')}",
            details={"node": node.path},
        )


def _variable(project: Project, node: Node) -> VariableDef:
    _healthy(node, "variable")
    text = str(node.params.get("parameter") or "").strip()
    if not text:
        raise StudyDefinitionError(f"variable {node.name} names no parameter")
    try:
        target, pname = resolve_target(project, text)
    except StudyDefinitionError as exc:
        raise StudyDefinitionError(f"variable {node.name}: {exc.message}") from exc
    actual = quantity_dimension(project, target, pname).name
    declared = str(node.params.get("dimension"))
    if declared != actual:
        raise StudyDefinitionError(
            f"variable {node.name} is a {declared} but {text} is a {actual}; set the variable's "
            "dimension (then its values) or add it again",
            details={"variable": node.path, "dimension": actual},
        )
    domain = str(node.params.get("domain") or "values")
    low, high = _si(node, "min"), _si(node, "max")
    bounded = domain in ("range", "uniform") or not (low == 0.0 and high == 0.0)
    step = _si(node, "step")
    return VariableDef(
        name=node.name,
        node_id=node.id,
        parameter=text,
        target_id=target.id,
        target_param=pname,
        dimension=actual,
        domain=domain,
        values=tuple(float(v) for v in node.params.get("values") or ()),
        labels=split_labels(str(node.params.get("labels") or "")),
        low=low if bounded else None,
        high=high if bounded else None,
        count=int(node.params.get("count") or 0),
        step=step if step > 0.0 else None,
        mean=_si(node, "mean"),
        std=_si(node, "std"),
    )


def _response(node: Node) -> ResponseDef:
    expression = str(node.params.get("expression") or "").strip()
    if not expression:
        raise StudyDefinitionError(f"response {node.name} has no expression")
    return ResponseDef(
        node.name,
        node.id,
        expression,
        str(node.params.get("dimension")),
        str(node.params.get("label") or ""),
    )


def _objective(project: Project, study: Node, node: Node) -> ObjectiveDef:
    ref = node.params.get("response")
    target = project.find(str(ref)) if ref else None
    direction = str(node.params.get("direction"))
    if target is None or target.type != "response" or target.parent is not study.child("responses"):
        return ObjectiveDef(node.name, node.id, None, direction)
    return ObjectiveDef(node.name, node.id, target.name, direction)


def _constraint(node: Node) -> ConstraintDef:
    _healthy(node, "constraint")
    expression = str(node.params.get("expression") or "").strip()
    if not expression:
        raise StudyDefinitionError(f"constraint {node.name} has no expression")
    return ConstraintDef(
        node.name,
        node.id,
        expression,
        str(node.params.get("dimension")),
        str(node.params.get("operator")),
        _si(node, "limit"),
    )


def read_definition(project: Project, study: Node, *, base: bool = True) -> StudyDefinition:
    """Snapshot ``study``; raises ``StudyDefinitionError`` naming what to fix. ``base`` adds the
    project's JSON form, which evaluators need and generation does not."""
    variables = tuple(_variable(project, n) for n in members(study, "variables", "variable"))
    responses = tuple(_response(n) for n in members(study, "responses", "response"))
    names = [v.name for v in variables] + [r.name for r in responses]
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise StudyDefinitionError(
            f"study {study.name}: variables and responses need distinct names; rename {repeated}",
            details={"names": repeated},
        )
    targets = [(v.target_id, v.target_param) for v in variables]
    for variable in variables:
        if targets.count((variable.target_id, variable.target_param)) > 1:
            raise StudyDefinitionError(
                f"study {study.name}: two variables vary {variable.parameter}",
                details={"parameter": variable.parameter},
            )
    return StudyDefinition(
        study_id=study.id,
        name=study.name,
        path=study.path,
        type=str(study.params.get("type")),
        sampler=str(study.params.get("sampler")),
        seed=int(study.params.get("seed") or 0),
        count=int(study.params.get("count") or 0),
        variables=variables,
        responses=responses,
        objectives=tuple(
            _objective(project, study, n) for n in members(study, "objectives", "objective")
        ),
        constraints=tuple(_constraint(n) for n in members(study, "constraints", "constraint")),
        # A deep copy through JSON: the snapshot shares no list or dict with the live tree.
        project=json.loads(json.dumps(project.to_json())) if base else {},
    )


def columns_of(study: Node) -> tuple[list[Column], list[Column]]:
    """The design-table columns as the tree defines them now; never fails on a broken definition,
    so a table can always be listed, cleared and exported."""

    def column(node: Node, source_param: str) -> Column:
        name = str(node.params.get("dimension") or "dimensionless")
        dim = DIMENSIONS.get(name, DIMENSIONS["dimensionless"])
        return Column(
            node.name,
            dim.name,
            dim.si_unit,
            str(node.params.get(source_param) or ""),
            str(node.params.get("label") or ""),
        )

    return (
        [column(n, "parameter") for n in members(study, "variables", "variable")],
        [column(n, "expression") for n in members(study, "responses", "response")],
    )


def split_labels(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def si_quantity(value: float, dim: Dimension) -> Quantity:
    """``value`` (SI) as a quantity of ``dim``."""
    return ureg().Quantity(value) if dim.si_unit == "" else quantity(value, dim.si_unit)
