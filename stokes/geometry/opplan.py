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
output through the operations before it.

Import rule: the standard library, the model and the light geometry modules; the store is passed
in. No OCP, no numpy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.geometry.errors import MeshOnly, OperationInvalid
from stokes.geometry.model import OPERATION_COMMON, OPERATIONS_PATH, TAG_TYPE, operation_type
from stokes.model.ids import is_id
from stokes.model.nodes import QuantityValue

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
    if check_cache:
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
        [part_spec(n, folder, bases[n.id]) for n in nodes],
        folder,
        refs,
        list(dict.fromkeys(rebuild)),
    )


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
    if found.params.get("family") == "mesh":
        raise MeshOnly(
            f"{found.path} is a mesh-only part (no B-rep): {op.type} needs a B-rep part; "
            "import the CAD file (STEP, IGES or BREP) instead",
            details={"op": op.id, "part": part},
        )
    return found


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
