"""parameter.* commands: set and read parameters, define named project parameters."""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.model import AddNode, Command, Composite, QuantityValue, SetParam
from stokes.model.project import PARAMETERS_PATH
from stokes.units import DIMENSIONS, format_quantity

NODE_REF = ParamDef("node", "string", description="Node id or path.")


@command(
    "parameter.set",
    params=(
        NODE_REF,
        ParamDef("name", "string", description="Parameter name."),
        ParamDef(
            "value",
            "any",
            description="A JSON value, quantity text ('2 [m/s]') or an expression ('U_in * 1.5'); "
            "null clears an optional reference.",
            nullable=True,
        ),
    ),
)
def parameter_set(ctx: CommandContext, node: str, name: str, value: Any) -> dict[str, Any]:
    """Set one parameter of a node; null clears an optional reference (a region's material, a
    boundary's partner), and a required one (a boundary's tag) refuses it."""
    cmd = SetParam(node, name, value)
    result = ctx.session.undo.push(cmd, ctx.session.require_project())
    ctx.session.emit("node.changed", node_id=cmd.node_id, path=result["path"], param=name)
    # **A units change re-imports the part** (Claude's defect 2). The source file is byte-identical,
    # so nothing else notices: the record's basis moves, the mesh keeps the old scale, and the
    # viewport draws millimetres while the part says inches. `source` is the other parameter that
    # changes the geometry a part is made of, and it needs the same treatment; it is left for now
    # because a source edit changes which file is read, not how the same file is scaled.
    if name == "units":
        project = ctx.session.require_project()
        part = project.find(cmd.node_id) if cmd.node_id else None
        if part is not None and part.type == "part":
            from stokes.geometry.store import GeometryStore

            GeometryStore.of(ctx.session).reimport_part(part)
    return dict(result)


@command(
    "parameter.get",
    params=(
        NODE_REF,
        ParamDef("name", "string", description="Parameter name."),
        ParamDef("unit", "string", required=False, description="Display unit for quantities."),
    ),
    mutates=False,
)
def parameter_get(
    ctx: CommandContext, node: str, name: str, unit: str | None = None
) -> dict[str, Any]:
    """Read one parameter; quantities come back in SI plus the requested display unit."""
    project = ctx.session.require_project()
    target = project.get(node)
    spec = project.spec_of(target).param(name)
    raw = target.params.get(name)
    out: dict[str, Any] = {"node": target.path, "name": name, "kind": spec.kind}
    if isinstance(raw, QuantityValue):
        dim = project.param_dimension(target, spec)
        q = project.value_of(target.id, name)
        display = unit or dim.unit_for(project.display_units)
        out.update(
            {
                "value": raw.value,
                "unit": raw.unit,
                "expr": raw.expr,
                "dimension": dim.name,
                "display": format_quantity(q, display or None),
            }
        )
    else:
        out["value"] = raw
    return out


@command(
    "parameter.define",
    params=(
        ParamDef("name", "string", description="Parameter name usable in expressions."),
        ParamDef("dimension", "string", description=f"One of {', '.join(DIMENSIONS)}."),
        ParamDef("value", "any", description="Quantity text or expression."),
        ParamDef("description", "string", required=False, default=""),
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
    ),
)
def parameter_define(
    ctx: CommandContext,
    name: str,
    dimension: str,
    value: Any,
    description: str = "",
    node_id: str | None = None,
) -> dict[str, Any]:
    """Create a named project parameter under /physics/parameters."""
    if dimension not in DIMENSIONS:
        raise ValidationError(f"unknown dimension {dimension!r}")
    cmd = AddNode(
        PARAMETERS_PATH,
        "parameter",
        name,
        {"dimension": dimension, "value": value, "description": description},
        node_id=node_id,
    )
    result = ctx.session.undo.push(cmd, ctx.session.require_project())
    ctx.record(node_id=cmd.node_id)
    ctx.session.emit("node.added", node_id=cmd.node_id, path=result["path"])
    return dict(result)


@command("parameter.list", mutates=False)
def parameter_list(ctx: CommandContext) -> list[dict[str, Any]]:
    """All named project parameters with their current values."""
    project = ctx.session.require_project()
    group = project.get(PARAMETERS_PATH)
    items = []
    for node in group.children:
        if node.type != "parameter":
            continue
        value = node.params["value"]
        dim = project.param_dimension(node, project.spec_of(node).param("value"))
        q = project.value_of(node.id, "value")
        items.append(
            {
                "name": node.name,
                "id": node.id,
                "dimension": dim.name,
                "value": value.value,
                "unit": value.unit,
                "expr": value.expr,
                "display": format_quantity(q, dim.unit_for(project.display_units) or None),
                "status": node.status,
                "error": node.meta.get("error"),
            }
        )
    return items


@command(
    "parameter.set_many",
    params=(
        ParamDef(
            "nodes",
            "array",
            description="Node ids or paths: every node that has this parameter, e.g. three parts.",
        ),
        ParamDef("name", "string", description="The parameter to set on every one of them."),
        ParamDef(
            "value",
            "any",
            description="The same value for all, as `parameter.set` takes it (JSON or a quantity "
            "text like '2 [mm]').",
            nullable=True,
        ),
    ),
)
def parameter_set_many(
    ctx: CommandContext,
    nodes: list[str],
    name: str,
    value: Any,
) -> dict[str, Any]:
    """Set one parameter on many nodes as **ONE undo step**.

    The multi-selection page needs it (claude-win2's 1400 file: *"editing a field applies the new
    value to ALL selected items (one undo step, one journal record)"*), and `parameter.set` takes
    one node.

    **`Composite`, which already rolls back.** A partial failure refuses the whole change rather
    than leaving half the rows edited - that rule is the brief's, and `Composite.apply` implements
    it by reverting what it applied before re-raising. Nothing new is invented: this is the existing
    command for every node, wrapped so the undo stack sees one entry.
    """
    project = ctx.session.require_project()
    # **`project.get`, which takes an id OR a path** - the same resolution `geometry.style`
    # uses, so a multi-selection can be handed ids while a caller who knows paths can use those.
    ids = [project.get(ref).id for ref in nodes]
    if not ids:
        raise ValidationError("no nodes were given; nothing to set")
    # **Annotated as the base type**: `Composite` takes `list[Command]` and a `list[SetParam]` is
    # not one (lists are invariant), which mypy refuses without this line.
    commands: list[Command] = [SetParam(node_id, name, value) for node_id in ids]
    label = f"Set {name} on {len(commands)} nodes"
    result = ctx.session.undo.push(Composite(label=label, commands=commands), project)
    for node_id in ids:
        ctx.session.emit("node.changed", node_id=node_id, param=name)
    return {"nodes": ids, "name": name, "results": result}
