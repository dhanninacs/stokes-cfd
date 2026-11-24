"""parameter.* commands: set and read parameters, define named project parameters."""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError, ValidationError
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
    project = ctx.session.require_project()
    cmd = SetParam(node, name, value)
    drop = _profiles_to_drop(project, node, name, value)
    if drop is None:
        result = ctx.session.undo.push(cmd, project)
    else:
        kept, dropped = drop
        step = Composite(
            label=f"set {name} (drops the {', '.join(dropped)} time profile)",
            commands=[cmd, SetParam(node, "profiles", kept)],
        )
        result = {**ctx.session.undo.push(step, project)[0], "profiles_dropped": list(dropped)}
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


def _profiles_to_drop(
    project: Any,  # Any: Project
    ref: str,
    name: str,
    value: Any,  # Any: JSON value
) -> tuple[dict[str, Any] | None, tuple[str, ...]] | None:  # Any: JSON
    """For a boundary's kind or switch (``direction``), the time profiles it keeps and the ones
    it drops when ``name`` becomes ``value`` (ADR-0022 addendum 2026-11-24), as ``boundary.set``
    drops them: none lies hidden to come back when the change is undone by hand. None when the
    set drops nothing."""
    target = project.find(ref)
    if target is None or target.type != "boundary" or not target.params.get("profiles"):
        return None
    if not isinstance(value, str):
        return None  # kind and the switches are words; anything else is refused by the set
    from stokes.physics.conditions.profiles import kept_for_kind

    after = {**target.params, name: value}
    kept, dropped = kept_for_kind(str(after.get("kind")), after)
    return (kept, dropped) if dropped else None


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
    if spec.kind in ("vector", "quantity_map"):
        # ADR-0037: each component is a quantity of its own, under <name>.<component>.
        prefix = f"{name}."
        out["components"] = {
            key[len(prefix) :]: parameter_get(ctx, node, key, unit)
            for key in target.params
            if key.startswith(prefix)
        }
        return out
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


MAX_UPDATE = 32


@command(
    "parameter.update",
    params=(
        NODE_REF,
        ParamDef(
            "values",
            "object",
            description="Parameter name to value, each as `parameter.set` takes it, applied in "
            "the order given (a sketch's dimensions before the entities that name them).",
        ),
    ),
)
def parameter_update(ctx: CommandContext, node: str, values: Any) -> dict[str, Any]:
    """Set several parameters of ONE node as **one undo step** and one journal record.

    The sketcher's Finish (ADR-0036 addendum) writes a sketch's plane, dimensions, entities and
    profile together: four ``parameter.set`` calls were four undo steps, and the first undo
    changed nothing visible. A refusal of any value leaves every parameter as it was
    (``Composite`` reverts what it applied before re-raising).
    """
    if not isinstance(values, dict) or not values:
        raise ValidationError("values must be an object of at least one parameter")
    if len(values) > MAX_UPDATE:
        raise ValidationError(f"at most {MAX_UPDATE} parameters at once")
    project = ctx.session.require_project()
    node_id = project.get(node).id
    commands: list[Command] = [SetParam(node_id, str(name), v) for name, v in values.items()]
    label = f"Set {', '.join(str(n) for n in values)}"
    result = ctx.session.undo.push(Composite(label=label, commands=commands), project)
    for name in values:
        ctx.session.emit("node.changed", node_id=node_id, param=str(name))
    return {"node": node_id, "names": [str(n) for n in values], "results": result}


MAX_LOCALS = 64


@command(
    "expression.evaluate",
    params=(
        ParamDef("text", "string", description="Quantity text ('20 [mm]') or an expression."),
        ParamDef(
            "dimension",
            "string",
            required=False,
            description="The dimension the result must have (length, angle, ...).",
        ),
        ParamDef(
            "names",
            "object",
            required=False,
            description="Names known to this evaluation only, each quantity text or an "
            "expression ({'W': '20 [mm]', 'H': 'W / 2'}): a sketch's dimensions before they are "
            "stored. They are looked up before the project's parameters.",
        ),
    ),
    mutates=False,
)
def expression_evaluate(
    ctx: CommandContext, text: str, dimension: str | None = None, names: Any = None
) -> dict[str, Any]:
    """Evaluate an expression against the project's parameters and quantities (and ``names``),
    with the grammar every quantity field uses; changes nothing. The sketcher previews a drawn
    coordinate such as 'W / 2' with it."""
    from stokes.errors import CycleError
    from stokes.units import dimension as named
    from stokes.units.expressions import evaluate
    from stokes.units.quantities import is_quantity_text, parse_quantity
    from stokes.units.registry import Quantity

    project = ctx.session.require_project()
    local = dict(names or {})
    if len(local) > MAX_LOCALS:
        raise ValidationError(f"at most {MAX_LOCALS} names")
    found: dict[str, Quantity] = {}
    active: set[str] = set()

    def resolve(ref: str) -> Quantity:
        if ref in found:
            return found[ref]
        if ref in local:
            if ref in active:
                raise CycleError(f"{ref} refers to itself", details={"ref": ref})
            active.add(ref)
            try:
                found[ref] = value_of(str(local[ref]))
            finally:
                active.discard(ref)
            return found[ref]
        return project.resolve(ref)

    def value_of(source: str) -> Quantity:
        source = source.strip()
        if is_quantity_text(source):
            for guess in ("length", "angle", "dimensionless"):
                try:
                    return parse_quantity(source, named(guess))
                except StokesError:
                    continue
        return evaluate(source, resolve)

    expect = named(dimension) if dimension else None
    result = evaluate(text, resolve, expect=expect) if expect else value_of(text)
    si = result.to_base_units()
    return {
        "text": text,
        "value": float(si.magnitude),
        "unit": str(si.units),
        "dimension": expect.name if expect else None,
    }


@command(
    "parameter.assign",
    params=(
        ParamDef(
            "values",
            "object",
            description="{reference: value}: each quantity as an expression names it ('R', "
            "'geometry.cyl.radius', 'geometry.cyl.position.x'), set to an SI number, quantity text "
            "or an expression.",
        ),
    ),
)
def parameter_assign(ctx: CommandContext, values: Any) -> dict[str, Any]:
    """Set quantities by the names expressions use, as one undo step: what applying an
    optimiser's best point does."""
    if not isinstance(values, dict) or not values:
        raise ValidationError("values is an object of reference: value")
    project = ctx.session.require_project()
    commands: list[Command] = []
    targets: list[tuple[str, str, str]] = []
    for reference, value in values.items():
        node, name = project.resolve_param(str(reference).strip().strip("/").replace("/", "."))
        commands.append(SetParam(node.id, name, value))
        targets.append((node.id, node.path, name))
    ctx.session.undo.push(Composite(label="assign parameters", commands=commands), project)
    for node_id, path, name in targets:
        ctx.session.emit("node.changed", node_id=node_id, path=path, param=name)
    return {"assigned": [{"node": path, "name": name} for _, path, name in targets]}
