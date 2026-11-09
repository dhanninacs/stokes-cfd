"""Helpers the ``study.*`` commands author study definitions with (ADR-0016 point 1): the undo
steps that add a study and its members, names for new nodes, quantities given as numbers, text or
expressions, the domain checks that need no numerics, and dimension inference.

``definition`` reads definitions back; this module only prepares what the commands push onto the
undo stack, and never changes the tree itself except to give an old project its ``/studies``
group (``ensure_studies_group``).

Import rule: the standard library, ``stokes.model``, ``stokes.units`` and ``definition``; no
numpy.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

from stokes.explore.definition import StudyDefinitionError, group_id, members
from stokes.explore.model import DOMAINS, STUDIES_PATH, STUDY_GROUPS
from stokes.model.commands import AddNode, Command, Composite
from stokes.model.ids import new_id, stable_id
from stokes.model.nodes import Node
from stokes.model.project import Project
from stokes.units.dimensions import DIMENSIONS, Dimension, dimension
from stokes.units.expressions import Resolver, evaluate
from stokes.units.quantities import is_quantity_text, parse_quantity, to_si
from stokes.units.registry import Quantity

_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


# ---------------------------------------------------------------- undo steps


def ensure_studies_group(project: Project) -> Node | None:
    """Add ``/studies`` to a project saved before the group existed; the new group, or None.

    It is part of the skeleton, so it is added outside the undo stack (undo could not remove
    it), with the structural id a new project gives it.
    """
    if project.find(STUDIES_PATH) is not None:
        return None
    name = STUDIES_PATH.strip("/")
    return project.add("/", "group", name, node_id=stable_id("g", STUDIES_PATH))


def create_command(
    name: str,
    params: dict[str, Any],  # Any: JSON parameter values
    node_id: str | None,
) -> Composite:
    """One undo step: the study under ``/studies`` and its four groups."""
    study_id = node_id or new_id()
    commands: list[Command] = [AddNode(STUDIES_PATH, "study", name, params, node_id=study_id)]
    commands.extend(
        AddNode(study_id, "group", group, node_id=group_id(study_id, group))
        for group in STUDY_GROUPS
    )
    return Composite(f"create study {name}", commands)


def member_command(
    study: Node,
    group: str,
    node_type: str,
    name: str,
    params: dict[str, Any],  # Any: JSON parameter values
    node_id: str | None,
) -> AddNode | Composite:
    """Add a definition node to a study's group, creating the group in the same undo step when a
    study made with ``node.add`` lacks it."""
    if study.child(group) is not None:
        return AddNode(f"{study.path}/{group}", node_type, name, params, node_id=node_id)
    holder = group_id(study.id, group)
    return Composite(
        f"add {node_type} {name}",
        [
            AddNode(study.id, "group", group, node_id=holder),
            AddNode(holder, node_type, name, params, node_id=node_id),
        ],
    )


# ---------------------------------------------------------------- names and values


def unique_name(base: str, taken: set[str]) -> str:
    """``base``, or ``base_2``, ``base_3`` ... whichever is not taken."""
    if base not in taken:
        return base
    index = 2
    while f"{base}_{index}" in taken:
        index += 1
    return f"{base}_{index}"


def identifier(text: str, fallback: str) -> str:
    """``text`` made into a node name: letters, digits and underscores, no leading digit."""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip()).strip("_")
    if not name:
        return fallback
    return f"{fallback}_{name}" if name[0].isdigit() else name


def taken_names(study: Node) -> set[str]:
    """Names used by the study's variables and responses (they share the table's columns)."""
    return {n.name for group in ("variables", "responses") for n in members_any(study, group)}


def members_any(study: Node, group: str) -> list[Node]:
    holder = study.child(group)
    return [] if holder is None else list(holder.children)


def si_value(project: Project, raw: Any, dim: Dimension, what: str) -> float:  # Any: JSON value
    """A number (SI), quantity text (``2 [m/s]``) or an expression, as an SI float of ``dim``."""
    if isinstance(raw, bool) or raw is None:
        raise StudyDefinitionError(f"{what} expects a quantity, got {raw!r}")
    if isinstance(raw, int | float):
        number = float(raw)
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if is_quantity_text(text):
            number = to_si(parse_quantity(text, dim), dim)
        else:
            q = evaluate_text(text, project.resolve, dim)
            try:
                number = to_si(q, dim)
            except TypeError as exc:  # a complex result
                raise StudyDefinitionError(f"{what} = {text!r} has no real value") from exc
    else:
        raise StudyDefinitionError(f"{what} expects a number or quantity text, got {raw!r}")
    if not math.isfinite(number):
        raise StudyDefinitionError(f"{what} must be finite, got {number}")
    return number


def si_values(project: Project, raw: Sequence[Any], dim: Dimension) -> list[float]:  # Any: JSON
    return [si_value(project, item, dim, f"values[{i}]") for i, item in enumerate(raw)]


def check_domain(
    domain: str,
    *,
    values: Sequence[float],
    labels: Sequence[str],
    low: float,
    high: float,
    count: int,
    step: float,
    std: float,
) -> None:
    """The checks that need no numerics; generation applies the full ``stokes.explore.domain``
    rules. Raises ``StudyDefinitionError`` with what to change."""
    if domain not in DOMAINS:
        raise StudyDefinitionError(f"domain must be one of {', '.join(DOMAINS)}; got {domain!r}")
    if domain in ("values", "categorical"):
        if not values:
            raise StudyDefinitionError(f"a {domain} variable needs values")
        if len(set(values)) != len(values):
            raise StudyDefinitionError("a variable's values must not repeat")
        if labels and len(labels) != len(values):
            raise StudyDefinitionError(f"{len(labels)} labels for {len(values)} values")
        if len(set(labels)) != len(labels):
            raise StudyDefinitionError("category labels must not repeat")
    if domain in ("range", "uniform") and not low < high:
        raise StudyDefinitionError(f"a {domain} variable needs min < max, got {low} and {high}")
    if domain == "range":
        if count and step:
            raise StudyDefinitionError("give a range either a count or a step, not both")
        if count == 1 or count < 0:
            raise StudyDefinitionError(f"a range's count must be 0 or at least 2, got {count}")
        if step < 0.0 or step > high - low:
            raise StudyDefinitionError(f"a range's step must lie in (0, {high - low}]")
    if domain in ("uniform", "normal") and count < 0:
        raise StudyDefinitionError(f"a level count must be 0 or more, got {count}")
    if domain == "normal" and not std > 0.0:
        raise StudyDefinitionError(f"a normal variable needs std > 0, got {std}")


def dimension_of(q: Quantity, what: str) -> Dimension:
    """The named dimension of a quantity (the first match: temperature before its difference)."""
    for dim in DIMENSIONS.values():
        if q.dimensionality == dim.dimensionality:
            return dim
    raise StudyDefinitionError(
        f"{what} has unit {q.units}, which matches no known dimension; pass dimension=...",
        details={"unit": str(q.units)},
    )


def response_resolver(project: Project, study: Node) -> Resolver:
    """Names for a constraint written against the base case: the study's responses by name
    (their expressions evaluated now), then the project's parameters and node quantities."""
    responses = {n.name: n for n in members(study, "responses", "response")}

    def resolve(name: str) -> Quantity:
        node = responses.get(name)
        if node is None:
            return project.resolve(name)
        dim = dimension(str(node.params.get("dimension")))
        return project.evaluate(str(node.params.get("expression") or ""), dim)

    return resolve


def evaluate_text(text: str, resolver: Resolver, expect: Dimension | None = None) -> Quantity:
    """``stokes.units.evaluate`` with the expression's own errors kept typed."""
    try:
        return evaluate(text, resolver, expect=expect)
    except (ArithmeticError, TypeError, ValueError) as exc:  # raised by pint outside the grammar
        raise StudyDefinitionError(f"cannot evaluate {text!r}: {exc}") from exc


def is_reference(text: str) -> bool:
    """True for a bare dotted name such as ``dp_dyn`` or ``regions.fluid.inlet.velocity``."""
    return bool(_REFERENCE.match(text.strip()))
