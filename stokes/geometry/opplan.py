"""What an operation's run reads (ADR-0021 decisions 8.4 and 8.5): its plan.

:func:`build_plan` turns an operation node, as the tree holds it now, into the job a worker
runs: the SI parameters (quantities as SI floats, the members of the tags it references as
``<name>_members``, kept to the members on the parts it reads: see :func:`_params`), the parts
it reads (its inputs, then each part list parameter such as a
boolean's tools) with their versions and the specs the shape provider loads them by, and the
**seed**: the hash of all of it, from which the versions of the outputs derive
(:func:`stokes.geometry.operations.output_basis`) and against which staleness is judged. What
cannot run is refused here, before any worker starts: a part that is gone or not a part, a
mesh-only part (``geometry.mesh_only``), too few inputs, an operation that would read its own
output through the operations before it. An implicit operation (ADR-0035) reads implicit parts
only, and its plan carries each input's **recipe** (:func:`implicit_recipe`) instead of a B-rep to
load.

Import rule: the standard library, the model and the light geometry modules; the store is passed
in. No OCP, no numpy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.geometry.errors import MeshOnly, OperationInvalid
from stokes.geometry.model import (
    BREPLESS_FAMILIES,
    OPERATION_COMMON,
    OPERATIONS_PATH,
    TAG_TYPE,
    is_implicit_operation,
    operation_type,
)
from stokes.model.ids import is_id
from stokes.model.nodes import QuantityValue
from stokes.model.schema import VECTOR_AXES

if TYPE_CHECKING:
    from stokes.geometry.store import GeometryStore
    from stokes.model.nodes import Node
    from stokes.model.project import Project


@dataclass
class Plan:
    """One run's job, built where commands run from the tree as it is."""

    op: str
    type: str
    name: str
    params: dict[str, Any]  # Any: SI JSON values, with <tag>_members
    parts: list[str]
    bases: dict[str, str]
    seed: str
    specs: list[dict[str, Any]]  # Any: JSON shape specs
    folder: Path
    refs: list[str] = field(default_factory=list)
    """The tags it references."""
    rebuild: list[str] = field(default_factory=list)
    """Operations whose outputs this run reads and whose B-reps are gone: they run first."""


def operations_in(project: Project) -> list[Node]:
    """The operation nodes of a project (under ``/geometry/operations``, at any depth)."""
    group = project.find(OPERATIONS_PATH)
    if group is None:
        return []
    return [n for n in group.walk() if operation_type(n.type) is not None]


def consumed(node: Node) -> list[str]:
    """The part ids an operation node reads, as stored: its inputs, then each part list
    parameter (a boolean's tools), each id once."""
    kind = operation_type(node.type)
    ids: list[str] = [str(v) for v in node.params.get("inputs") or []]
    for name in kind.part_params if kind is not None else ():
        ids.extend(str(v) for v in node.params.get(name) or [])
    return list(dict.fromkeys(ids))


def referenced_tags(node: Node) -> list[str]:
    """The tag ids an operation node's tag references point at."""
    kind = operation_type(node.type)
    names = kind.tag_params if kind is not None else ()
    return [str(node.params[n]) for n in names if isinstance(node.params.get(n), str)]


def build_plan(
    project: Project, node: Node, geo: GeometryStore, *, check_cache: bool = False
) -> Plan:
    """The job a run of ``node`` would send now; :class:`OperationInvalid` (or
    :class:`MeshOnly`) when it cannot run. ``check_cache`` reads the sidecars of derived inputs
    to find B-reps that are gone (their operations run first)."""
    from stokes.geometry.operations import seed_of
    from stokes.geometry.shapes import brep_path, fresh, part_spec

    kind = operation_type(node.type)
    if kind is None:
        raise OperationInvalid(f"{node.path} is a {node.type}, not an operation")
    params, refs = _params(project, node)
    inputs = [str(v) for v in node.params.get("inputs") or []]
    if len(inputs) < kind.min_inputs:
        raise OperationInvalid(
            f"{node.path} needs at least {kind.min_inputs} input part(s); it has {len(inputs)}",
            details={"op": node.id},
        )
    nodes = [_input(project, node, part) for part in consumed(node)]
    _check_cycle(project, node, nodes)
    folder = geo.shape_dir()
    bases = {n.id: geo.basis(n) for n in nodes}
    rebuild: list[str] = []
    implicit = is_implicit_operation(node.type)
    if check_cache and not implicit:
        for n in nodes:
            upstream = str(n.params.get("derived") or "")
            if upstream and not fresh(brep_path(folder, n.id), bases[n.id]):
                rebuild.append(upstream)
    seed = seed_of(
        {"type": node.type, "params": params, "inputs": [[n.id, bases[n.id]] for n in nodes]}
    )
    return Plan(
        node.id,
        node.type,
        node.name,
        params,
        [n.id for n in nodes],
        bases,
        seed,
        [
            _implicit_spec(project, n) if implicit else part_spec(n, folder, bases[n.id])
            for n in nodes
        ],
        folder,
        refs,
        list(dict.fromkeys(rebuild)),
    )


def compound_si(node: Node, name: str, kind: str) -> Any:  # Any: JSON
    """A vector's three components, or a map's entries by name, in SI (ADR-0037): the form the
    operation builders read (a vector as a list of three numbers)."""
    prefix = f"{name}."
    if kind == "vector":
        values = []
        for axis in VECTOR_AXES:
            stored = node.params.get(prefix + axis)
            values.append(stored.value if isinstance(stored, QuantityValue) else 0.0)
        return values
    return {
        key[len(prefix) :]: value.value
        for key, value in node.params.items()
        if key.startswith(prefix) and isinstance(value, QuantityValue)
    }


def _params(project: Project, node: Node) -> tuple[dict[str, Any], list[str]]:
    """The op's own parameters as SI JSON, and the tags it references, whose members it
    receives as ``<name>_members`` (the stored list, with bases), kept to the members the run
    can actually read.

    A referenced tag collects faces from every operation in the chain: the tag a ``cap`` fills
    and an ``extract_fluid`` then reads ends up holding the extraction's own output, and the
    ``orient`` after that adds another. Those members are on parts the run does not read, so
    they stay out of its plan: reading them back would make a run look like a change to the
    very parameters it started from (a permanently stale op), and the next run would refuse
    them as "not an input of this run".

    The members kept are exactly the ones on :func:`consumed` parts — the same set the worker
    accepts (:meth:`stokes.geometry.operations.base.OpContext.input`) — so a member on an
    unrelated part is ignored rather than failing the run. Every member on a part the run
    reads stays, the user's edits included.
    """
    kind = operation_type(node.type)
    assert kind is not None
    reads = set(consumed(node))
    params: dict[str, Any] = {}
    refs: list[str] = []
    for spec in project.spec_of(node).params:
        if spec.name in OPERATION_COMMON:
            continue
        if spec.kind in ("vector", "quantity_map"):
            params[spec.name] = compound_si(node, spec.name, spec.kind)
            continue
        value = node.params.get(spec.name)
        if isinstance(value, QuantityValue):
            value = value.value
        elif spec.kind == "json" and isinstance(value, str):
            value = _parse(value)
        elif isinstance(value, tuple):
            value = list(value)
        params[spec.name] = value
        if spec.kind == "ref" and spec.name in kind.tag_params:
            tag = project.find(value) if isinstance(value, str) else None
            members: Any = []  # Any: JSON
            if tag is not None and tag.type == TAG_TYPE:
                refs.append(tag.id)
                raw = tag.params.get("members")
                current: Any = _parse(raw) if isinstance(raw, str) else raw or []  # Any: JSON
                members = _on_reads(current, reads)
            params[f"{spec.name}_members"] = members
    if node.type == "implicit_sketch" and isinstance(params.get("dimensions"), dict):
        # A drawn sketch's coordinates may be expressions of its dimensions (ADR-0037): the run
        # receives them as numbers.
        from stokes.geometry.sketchexpr import resolve_entities

        dims = {
            name: project.value_of(node.id, f"dimensions.{name}") for name in params["dimensions"]
        }
        params["entities"] = resolve_entities(params.get("entities"), dims)
    return params, refs


def _on_reads(members: Any, reads: set[str]) -> Any:  # Any: JSON members in and out
    """A stored member list kept to the members whose part the run reads. Anything that is not
    a list of member objects is handed on unchanged, for the run to report."""
    if not isinstance(members, list):
        return members
    kept: list[Any] = []  # Any: JSON members
    for member in members:
        if isinstance(member, dict) and str(member.get("part", "")) not in reads:
            continue
        kept.append(member)
    return kept


def _input(project: Project, op: Node, part: str) -> Node:
    found = project.find(part) if is_id(part) else None
    if found is None:
        raise OperationInvalid(
            f"{op.path} reads part {part}, which is not in the project",
            details={"op": op.id, "part": part},
        )
    if found.type != "part":
        raise OperationInvalid(
            f"{op.path} reads {found.path}, a {found.type}, not a part",
            details={"op": op.id, "part": part},
        )
    family = found.params.get("family")
    if is_implicit_operation(op.type):
        if family != "implicit":
            raise OperationInvalid(
                f"{op.path} reads {found.path}, which is not an implicit part: implicit "
                "operations combine parts made with Create only",
                details={"op": op.id, "part": part},
            )
        return found
    if family == "implicit":
        raise MeshOnly(
            f"{found.path} is an implicit part (no B-rep): {op.type} needs a B-rep part; "
            "use the implicit operations on it",
            details={"op": op.id, "part": part},
        )
    if family in BREPLESS_FAMILIES:
        raise MeshOnly(
            f"{found.path} is a mesh-only part (no B-rep): {op.type} needs a B-rep part; "
            "import the CAD file (STEP, IGES or BREP) instead",
            details={"op": op.id, "part": part},
        )
    return found


MAX_RECIPE_DEPTH = 64
"""Chained implicit operations a recipe may hold (as :mod:`stokes.geometry.operations.implicit`)."""


def _implicit_spec(project: Project, part: Node) -> dict[str, Any]:  # Any: JSON
    """What an implicit operation's worker job needs of one input: its recipe."""
    return {
        "part": part.id,
        "name": part.name,
        "family": "implicit",
        "recipe": implicit_recipe(project, part),
    }


def implicit_recipe(project: Project, part: Node, depth: int = 0) -> dict[str, Any]:  # Any: JSON
    """The recipe of an implicit part: the operation that made it, its SI parameters and the
    recipes of the parts it reads, as :func:`stokes.geometry.operations.implicit.body_of` reads
    them. Built from the tree as it is, so the worker rebuilds the part's field without any file
    of it (ADR-0035 decision 4: graphs are rebuilt from the operation nodes)."""
    if depth > MAX_RECIPE_DEPTH:
        raise OperationInvalid(
            f"{part.path} is built from more than {MAX_RECIPE_DEPTH} chained operations",
            details={"part": part.id},
        )
    op = project.find(str(part.params.get("derived") or ""))
    if op is None or not is_implicit_operation(op.type):
        raise OperationInvalid(
            f"{part.path} has no implicit operation that makes it", details={"part": part.id}
        )
    params, _refs = _params(project, op)
    kind = operation_type(op.type)
    tool_ids: list[str] = []
    for name in kind.part_params if kind is not None else ():
        tool_ids.extend(str(v) for v in op.params.get(name) or [])
    input_ids = [str(v) for v in op.params.get("inputs") or []]

    def recipes(ids: list[str]) -> list[dict[str, Any]]:  # Any: JSON
        out = []
        for ident in ids:
            node = _input(project, op, ident)
            out.append(implicit_recipe(project, node, depth + 1))
        return out

    outputs = [str(v) for v in op.params.get("outputs") or []]
    return {
        "type": op.type,
        "params": params,
        "inputs": recipes(input_ids),
        "tools": recipes(tool_ids),
        # Which of the operation's outputs the part is (a split makes two, ADR-0036).
        "output": outputs.index(part.id) if part.id in outputs else 0,
    }


def _check_cycle(project: Project, op: Node, parts: list[Node]) -> None:
    """Refuse an operation that reads, through other operations, its own outputs."""
    seen: set[str] = set()
    frontier = [str(p.params.get("derived") or "") for p in parts]
    while frontier:
        upstream = frontier.pop()
        if not upstream or upstream in seen:
            continue
        if upstream == op.id:
            raise OperationInvalid(
                f"{op.path} reads its own output, through the operations before it",
                details={"op": op.id},
            )
        seen.add(upstream)
        node = project.find(upstream)
        if node is not None and operation_type(node.type) is not None:
            for part in consumed(node):
                found = project.find(part)
                if found is not None:
                    frontier.append(str(found.params.get("derived") or ""))


def _parse(text: str) -> Any:  # Any: JSON
    try:
        return json.loads(text)
    except ValueError:
        return text
