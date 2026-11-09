"""operation.* commands (ADR-0021 decision 8.7): geometry operations as parametric nodes.

``operation.create`` adds an operation node under ``/geometry/operations`` (one undo step that
also hides the parts it reads) and starts its run in a worker; its outputs are derived parts
under ``/geometry/parts`` that the run adds as the worker reports them. ``operation.run`` runs
an operation again, ``operation.cancel`` stops a run, ``operation.delete`` removes an operation
with or without its outputs, and ``operation.list``, ``operation.info`` and ``operation.types``
answer questions (the last one gives the cards their schemas). Changing an operation's
parameter or input (``parameter.set``, undo, a re-imported part) makes it stale and, with
``auto_run``, runs it again 300 ms later (:mod:`stokes.geometry.opstore`).

``create`` and ``run`` are journaled; a replay runs the operations again and waits for each before
the next record (``Session.settle``), so a journal needs no mesh and rebuilds the same ids.

Events: ``geometry.op_progress``, ``geometry.op_done``, ``geometry.op_failed``; meshes through
the ``geometry.*`` events of the import. Errors: ``geometry.op_invalid`` (inputs, a type whose
module is not built yet), ``geometry.mesh_only``, ``geometry.op_failed``, ``validation``.
The contract is ``docs/engineering/geometry-schema.md`` ("Operations").

Import rule: the registry and the light ``stokes.geometry.model`` at module level; the service,
the store and everything heavier inside the handlers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import UnknownNode, ValidationError
from stokes.geometry.model import (
    OPERATION_COMMON,
    OPERATION_TYPE_NAMES,
    OPERATION_TYPES,
    OPERATIONS_PATH,
    operation_type,
)

if TYPE_CHECKING:
    from stokes.geometry.opstore import OperationStore
    from stokes.model import Node, Project

PARTS_PATH = "/geometry/parts"
OP = ParamDef(
    "op",
    "string",
    description="Operation: node id, path (/geometry/operations/cut_1) or name.",
)
WAIT = ParamDef(
    "wait",
    "boolean",
    required=False,
    default=False,
    description="Return once the run has ended (scripts and the CLI); interactive clients "
    "follow the geometry.op_* events instead.",
)


def _service(ctx: CommandContext) -> OperationStore:
    from stokes.geometry.opstore import OperationStore

    return OperationStore.of(ctx.session)


def _operation(project: Project, ref: str) -> Node:
    """An operation node by id, path or name (under /geometry/operations)."""
    node = project.find(ref)
    if node is None and "/" not in ref and "." not in ref:
        group = project.find(OPERATIONS_PATH)
        node = group.child(ref) if group is not None else None
    if node is None:
        raise UnknownNode(f"no operation {ref!r}", details={"op": ref})
    if operation_type(node.type) is None:
        raise ValidationError(
            f"{node.path} is a {node.type}, not an operation", details={"op": node.path}
        )
    return node


def _parts(project: Project, refs: Any, what: str) -> list[str]:  # Any: JSON array
    """Part ids from references to parts or groups (every part beneath a group), each once. A
    reference is an id, a path, or the name of one part under /geometry/parts (the palette)."""
    from stokes.geometry.store import parts_under

    if not isinstance(refs, list) or not all(isinstance(r, str) and r for r in refs):
        raise ValidationError(f"{what} must be a list of part or group ids or paths")
    found: dict[str, None] = {}
    for ref in refs:
        node = _named_part(project, ref) or project.get(ref)
        if node.type == "part":
            found.setdefault(node.id)
        elif node.type == "group":
            for part in parts_under(node):
                found.setdefault(part.id)
        else:
            raise ValidationError(
                f"{node.path} is a {node.type}, not a part or a group of parts",
                details={"node": node.path},
            )
    return list(found)


def _arrays_in_si(op_type: str, own: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    """Array parameters with a dimension (a primitive's ``size``) as SI numbers: an item may be
    quantity text (``"20 [mm]"``), as the card and the palette send it; the model stores SI."""
    from stokes.model import registry
    from stokes.units import dimension, parse_quantity, to_si

    out = dict(own)
    for spec in registry.get(op_type).params:
        value = out.get(spec.name)
        if spec.kind != "array" or spec.dimension is None or not isinstance(value, list):
            continue
        dim = dimension(spec.dimension)
        out[spec.name] = [
            to_si(parse_quantity(item, dim), dim) if isinstance(item, str) else item
            for item in value
        ]
    return out


def _named_part(project: Project, ref: str) -> Node | None:
    """The one part called ``ref`` under /geometry/parts, when ``ref`` is a bare name that no
    id or path matches; a name two parts share is refused rather than guessed."""
    from stokes.geometry.store import parts_under

    if "/" in ref or "." in ref or project.find(ref) is not None:
        return None
    group = project.find(PARTS_PATH)
    named = [p for p in parts_under(group) if p.name == ref] if group is not None else []
    if len(named) > 1:
        raise ValidationError(
            f"{len(named)} parts are called {ref!r}; give an id or a path",
            details={"part": ref, "paths": [p.path for p in named]},
        )
    return named[0] if named else None


def _refuse_mesh_only(project: Project, op_type: str, parts: list[str]) -> None:
    """ADR-0021 decision 1.3: a mesh-only part has no B-rep for an operation to read."""
    from stokes.geometry.errors import MeshOnly

    for part in parts:
        node = project.get(part)
        if node.params.get("family") == "mesh":
            raise MeshOnly(
                f"{node.path} is a mesh-only part (no B-rep): {op_type} needs a B-rep part; "
                "import the CAD file (STEP, IGES or BREP) instead",
                details={"part": part},
            )


def _info(service: OperationStore, node: Node) -> dict[str, Any]:  # Any: JSON
    """What ``operation.info`` reports: the node, its last run and the run under way."""
    from stokes.geometry.opstore import run_record

    kind = operation_type(node.type)
    run = run_record(node)
    return {
        "id": node.id,
        "path": node.path,
        "name": node.name,
        "type": node.type,
        "label": kind.label if kind is not None else node.type,
        "status": node.status,
        "stale": node.status == "stale",
        "auto_run": bool(node.params.get("auto_run")),
        "inputs": list(node.params.get("inputs") or []),
        "outputs": list(node.params.get("outputs") or []),
        "params": node.summary()["params"],
        "error": node.meta.get("error"),
        "run": {k: v for k, v in run.items() if k != "report"},
        "report": run.get("report", {}),
        "state": service.state(node.id),
    }


@command(
    "operation.create",
    params=(
        ParamDef(
            "type",
            "string",
            description="The operation type: " + ", ".join(OPERATION_TYPE_NAMES) + ".",
        ),
        ParamDef(
            "inputs",
            "array",
            required=False,
            default=[],
            description="The parts it reads: part or group ids or paths (a group stands for every "
            "part beneath it). None for a primitive.",
        ),
        ParamDef(
            "name",
            "string",
            required=False,
            description="Name of the operation node; default: the type and a number.",
        ),
        ParamDef(
            "params",
            "object",
            required=False,
            description="Its own parameters (operation.types lists them): quantities as text with "
            "a unit or SI numbers; part lists (a boolean's tools) as ids or paths.",
        ),
        ParamDef(
            "auto_run",
            "boolean",
            required=False,
            default=True,
            description="Run again 300 ms after a parameter or an input changes.",
        ),
        WAIT,
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
    ),
)
def operation_create(
    ctx: CommandContext,
    type: str,  # noqa: A002 - the operation type, as node.add names it
    inputs: list[Any] | None = None,  # Any: JSON references
    name: str | None = None,
    params: dict[str, Any] | None = None,  # Any: JSON values
    auto_run: bool = True,
    wait: bool = False,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Create an operation and run it: one undo step (the node, its group when missing, the
    parts it reads hidden); the outputs arrive as the run makes them."""
    from stokes.geometry import operations
    from stokes.geometry.errors import OperationInvalid
    from stokes.geometry.files import require_cad
    from stokes.geometry.opcommands import CreateOperation

    kind = operation_type(type)
    if kind is None:
        raise ValidationError(
            f"unknown operation type {type!r}; known: {', '.join(OPERATION_TYPE_NAMES)}",
            details={"type": type},
        )
    if not operations.is_built(type):
        raise OperationInvalid(f"the {type} operation is not built yet", details={"type": type})
    require_cad()
    project = ctx.session.require_project()
    service = _service(ctx)
    if wait and service.geometry.blocks_session():
        raise ValidationError("wait=true would block the server; follow the geometry.op_* events")
    own = dict(params or {})
    common = sorted(set(own) & set(OPERATION_COMMON))
    if common:
        raise ValidationError(
            f"give {', '.join(common)} as the command's own parameters, not in params",
            details={"params": common},
        )
    own = _arrays_in_si(type, own)
    ids = _parts(project, list(inputs or []), "inputs")
    if len(ids) < kind.min_inputs:
        raise OperationInvalid(
            f"a {type} needs at least {kind.min_inputs} input part(s)", details={"type": type}
        )
    for part_param in kind.part_params:
        if part_param in own:
            own[part_param] = _parts(project, own[part_param], part_param)
    read = list(dict.fromkeys(ids + [p for n in kind.part_params for p in own.get(n) or []]))
    _refuse_mesh_only(project, type, read)
    if name is None:
        group = project.find(OPERATIONS_PATH)
        taken = {c.name for c in group.children} if group is not None else set()
        index = 1
        while f"{type}_{index}" in taken:
            index += 1
        name = f"{type}_{index}"
    full = {**own, "inputs": ids, "auto_run": auto_run}
    cmd = CreateOperation(service, type, name, full, hide=read, node_id=node_id)
    result = ctx.session.undo.push(cmd, project)
    ctx.record(node_id=cmd.node_id, name=name, inputs=ids, params=own)
    if cmd.created_group:
        ctx.session.emit("node.added", node_id=cmd.group_id, path=OPERATIONS_PATH)
    ctx.session.emit("node.added", node_id=cmd.node_id, path=result["path"])
    for part in cmd.hidden():
        hidden = project.find(part)
        if hidden is not None:
            ctx.session.emit("node.changed", node_id=part, path=hidden.path, param="visible")
    node = project.get(result["id"])
    if wait:
        service.wait(node.id)
    return _info(service, node)


@command("operation.run", params=(OP, WAIT))
def operation_run(ctx: CommandContext, op: str, wait: bool = False) -> dict[str, Any]:
    """Run an operation now (one that does not run on change, or a stale or failed one); a run
    of it under way is cancelled first. Not an undo step: the outputs follow the node."""
    project = ctx.session.require_project()
    service = _service(ctx)
    node = _operation(project, op)
    service.plan(project, node)  # what cannot run is refused here, not in the background
    if wait and service.geometry.blocks_session():
        raise ValidationError("wait=true would block the server; follow the geometry.op_* events")
    service.start(node.id, reason="run")
    ctx.record(op=node.id)
    if wait:
        service.wait(node.id)
    return _info(service, node)


@command("operation.cancel", params=(OP,), mutates=False)
def operation_cancel(ctx: CommandContext, op: str) -> dict[str, Any]:
    """Stop the run of an operation, under way or waiting to start; it stays stale."""
    node = _operation(ctx.session.require_project(), op)
    return {"op": node.id, "cancelled": _service(ctx).cancel(node.id)}


@command(
    "operation.delete",
    params=(
        OP,
        ParamDef(
            "keep_outputs",
            "boolean",
            required=False,
            default=False,
            description="Keep the outputs as plain parts (their cached B-rep stays); otherwise "
            "they go and the parts the operation read are shown again.",
        ),
    ),
)
def operation_delete(ctx: CommandContext, op: str, keep_outputs: bool = False) -> dict[str, Any]:
    """Delete an operation, as one undo step. Refused while another operation reads one of its
    outputs (``model.referenced``), unless the outputs are kept."""
    from stokes.geometry.opcommands import DeleteOperation
    from stokes.geometry.opplan import consumed, operations_in
    from stokes.geometry.store import parts_under
    from stokes.model.project import referenced_error

    project = ctx.session.require_project()
    service = _service(ctx)
    node = _operation(project, op)
    outputs = {n.id for n in parts_under(project.root) if n.params.get("derived") == node.id}
    if not keep_outputs:
        readers = [(o, "inputs") for o in operations_in(project) if outputs & set(consumed(o))]
        if readers:
            raise referenced_error(node, readers, hint="delete them first, or keep the outputs")
    paths = {part: project.get(part).path for part in outputs}
    hidden = [
        p.id
        for part in consumed(node)
        if (p := project.find(part)) is not None and p.params.get("visible") is False
    ]
    cmd = DeleteOperation(service, node.id, keep_outputs)
    result = ctx.session.undo.push(cmd, project)
    for part, path in paths.items():
        if keep_outputs:
            ctx.session.emit("node.changed", node_id=part, path=path, param="derived")
        else:
            ctx.session.emit("node.removed", node_id=part, path=path)
    for part in [] if keep_outputs else hidden:  # shown again with the outputs gone
        ctx.session.emit("node.changed", node_id=part, path=project.get(part).path, param="visible")
    ctx.session.emit("node.removed", node_id=node.id, path=result["path"])
    return {
        "id": node.id,
        "path": result["path"],
        "outputs": result["outputs"],
        "kept": keep_outputs,
    }


@command("operation.list", mutates=False)
def operation_list(ctx: CommandContext) -> list[dict[str, Any]]:
    """Every operation with its status, inputs, outputs and the run under way."""
    from stokes.geometry.opplan import operations_in

    service = _service(ctx)
    return [
        {
            key: value
            for key, value in _info(service, node).items()
            if key not in ("params", "report")
        }
        for node in operations_in(ctx.session.require_project())
    ]


@command("operation.info", params=(OP,), mutates=False)
def operation_info(ctx: CommandContext, op: str) -> dict[str, Any]:
    """An operation: its parameters, status, last run (report, timings, the faces its tags
    could not follow) and the run under way."""
    node = _operation(ctx.session.require_project(), op)
    return _info(_service(ctx), node)


@command("operation.types", mutates=False, requires_project=False)
def operation_types(ctx: CommandContext) -> dict[str, Any]:
    """The operation types with their parameter schemas, for the operation cards (ADR-0021
    decision 7): ``built`` is false for a type whose module comes in a later task."""
    from stokes.geometry import operations
    from stokes.model import registry

    return {
        "types": [
            {
                "type": kind.type,
                "label": kind.label,
                "built": operations.is_built(kind.type),
                "min_inputs": kind.min_inputs,
                "part_params": list(kind.part_params),
                "tag_params": list(kind.tag_params),
                "schema": registry.get(kind.type).json_schema(),
            }
            for kind in OPERATION_TYPES
        ]
    }
