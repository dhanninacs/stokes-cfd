"""The ``mesh.*`` commands (ADR-0024, T-143).

Three of them here; generation is T-145b, and there is deliberately no ``mesh.set``
(``parameter.set`` already edits any parameter) and no ``mesh.delete`` (``node.remove`` already
removes any node).

## Why ``mesh.configure`` is one `Composite` and not several commands

The brief's rule is "a refusal anywhere leaves the tree unchanged", and the only way to have
that for
free is to build every command first and push them as one undo step. If any validation fails while
the list is being built, nothing has been pushed and the tree is untouched — which is what the test
compares the tree before and after to check.
"""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.mesh.catalogue import MESHERS
from stokes.mesh.catalogue import mesher as find_mesher
from stokes.mesh.errors import MeshInvalid, MeshNotBuilt
from stokes.mesh.model import RESULTS_PATH, SETTINGS_PATH, active_result
from stokes.model.ids import stable_id

#: The settings node's id, fixed by its path: one settings node per project, and a stable id means a
#: journal replaying into a fresh session lands on the same node.
SETTINGS_ID = stable_id("n", SETTINGS_PATH)


def _as_si(values: dict[str, Any], kind: str) -> dict[str, Any]:
    """The ``values`` map with every quantity and sized array converted to SI.

    A caller may send ``"2 [mm]"`` (the palette and the card do) or a bare SI number. ``box`` is six
    lengths and may mix the two forms per entry, exactly like ``operation._arrays_in_si``.
    """
    from stokes.model import registry
    from stokes.units import dimension, parse_quantity, to_si

    spec = registry.get(kind)
    out: dict[str, Any] = {}
    for name, value in values.items():
        param = spec.param(name)  # raises UnknownParameter with code model.unknown_parameter
        if param.dimension is None:
            out[name] = value
            continue
        dim = dimension(param.dimension)
        if param.kind == "quantity" and isinstance(value, str):
            out[name] = to_si(parse_quantity(value, dim), dim)
        elif param.kind == "array" and isinstance(value, list):
            out[name] = [
                to_si(parse_quantity(item, dim), dim) if isinstance(item, str) else item
                for item in value
            ]
        else:
            out[name] = value
    return out


def _check_box(box: Any) -> None:
    """``box`` is six numbers with a positive extent on each axis.

    A degenerate box gives blockMesh a zero-thickness block, which it either refuses with a message
    about a face or accepts and produces a mesh with no cells. Refusing here says which axis
    is wrong.
    """
    if box is None:
        return
    if not isinstance(box, list) or len(box) != 6:
        raise MeshInvalid(f"box needs six numbers (xmin ymin zmin xmax ymax zmax); got {box!r}")
    for value in box:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MeshInvalid(f"every box entry must be a number; got {value!r}")
    for axis, (low, high) in enumerate(zip(box[:3], box[3:], strict=True)):
        if not low < high:
            name = "xyz"[axis]
            raise MeshInvalid(
                f"the box is empty on {name}: {name}min={low} is not below {name}max={high}"
            )


def _positive(value: Any, name: str) -> None:
    """A size that must be greater than zero once converted."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    if value <= 0:
        raise MeshInvalid(f"{name} must be greater than zero; got {value}")


@command(
    "mesh.meshers",
    params=(),
    requires_project=False,
)
def mesh_meshers(ctx: CommandContext) -> dict[str, Any]:
    """The catalogue plus the `mesh_settings` JSON schema.

    No project needed: the command palette offers meshers before a project exists, and the
    schema is what a client uses to build the settings form.
    """
    from stokes.model import registry

    return {
        "meshers": [spec.to_json() for spec in MESHERS],
        "schema": registry.get("mesh_settings").json_schema(),
    }


@command(
    "mesh.body_role",
    params=(
        ParamDef(
            "part",
            "string",
            required=False,
            description="The ID or path of the part whose meshing role is being assigned. "
            "Exactly one of `part` and `bodies`.",
        ),
        ParamDef(
            "bodies",
            "array",
            required=False,
            description="The IDs or paths of several parts, for one choice applied to all of them "
            "in ONE undo step (the Bodies page's multi-select). Exactly one of the two names.",
        ),
        ParamDef(
            "role",
            "string",
            required=True,
            description="The role: fluid, hole (enclosure), solid, or left_out.",
        ),
    ),
    mutates=True,
)
def mesh_body_role(  # noqa: PLR0912 - one branch per refusal and per role, each naming its reason
    ctx: CommandContext,
    role: str,
    part: str | None = None,
    bodies: list[str] | None = None,
) -> dict[str, Any]:
    """Assign a meshing role to a body (part), or to several with one undo step.

    ``fluid`` and ``hole`` (enclosure) are mutually exclusive and each must be a single part,
    so assigning one to a part clears it from any other part and sets ``domain_part`` on the
    settings. ``solid`` and ``left_out`` can be any number of parts. Any role other than
    ``left_out`` marks the part as simulated (``simulate=True``); ``left_out`` marks it as not
    simulated (``simulate=False``), so a hole or a fluid never ends up excluded from the mesh.

    **`bodies` is the multi-select's one call** (brief M-502): one choice, every named part, ONE
    undo step — because a user who picks three solids and says "Solid" means one decision, and
    three undo steps would make Undo a game. Fluid and hole refuse a multi-body list: the slot
    is one part by definition.
    """
    from stokes.api.commands.region import Lister
    from stokes.model.commands import Composite, SetParam

    if role not in ("fluid", "hole", "solid", "left_out"):
        raise MeshInvalid(
            f"role must be one of fluid, hole, solid, left_out; got {role!r}",
            details={"code": "mesh.invalid", "parameter": "role"},
        )
    targets: list[str]
    if bodies is not None and part is not None:
        raise MeshInvalid(
            "name either `part` or `bodies`, not both",
            details={"code": "mesh.invalid", "parameter": "bodies"},
        )
    if bodies is not None:
        ok = isinstance(bodies, list) and bool(bodies) and all(isinstance(b, str) for b in bodies)
        if not ok:
            raise MeshInvalid(
                "`bodies` expects a non-empty list of part ids or paths",
                details={"code": "mesh.invalid", "parameter": "bodies"},
            )
        if role in ("fluid", "hole") and len(bodies) > 1:
            raise MeshInvalid(
                "only one body can be the fluid or the hole in the flow",
                details={"code": "mesh.invalid", "parameter": "role", "bodies": len(bodies)},
            )
        targets = list(dict.fromkeys(bodies))
    elif part is not None:
        targets = [part]
    else:
        raise MeshInvalid(
            "name the part or parts: pass `part` or `bodies`",
            details={"code": "mesh.invalid", "parameter": "part"},
        )

    project = ctx.session.require_project()
    nodes = []
    for target in targets:
        node = project.find(target)
        if node is None:
            raise MeshInvalid(
                f"no part named or id'd {target!r}",
                details={"code": "mesh.invalid", "parameter": "part"},
            )
        if node.type != "part":
            raise MeshInvalid(
                f"{target!r} is a {node.type}, not a part",
                details={"code": "mesh.invalid", "parameter": "part"},
            )
        nodes.append(node)

    from stokes.mesh.model import SETTINGS_PATH

    settings = project.find(SETTINGS_PATH)
    changes: list[Any] = []
    first = nodes[0]
    if role in ("fluid", "hole"):
        # **Exclusivity**: fluid and hole are one slot each. A part taking the role clears it
        # from whichever other part held it, exactly as a domain change does.
        field = "domain_part"
        old_holder = None
        if settings is not None:
            old_ref = settings.params.get(field)
            if old_ref:
                old_holder = project.find(str(old_ref))
                if old_holder is not None and old_holder.id != first.id:
                    changes.append(SetParam(old_holder.id, "simulate", True))
        for node in nodes:
            changes.append(SetParam(node.id, "simulate", True))
        if settings is not None:
            changes.append(SetParam(settings.id, field, first.id))
        # **The changes are APPLIED, not just described**: `Composite` in the reply is the receipt;
        # without the push here the command said it had assigned the role and did nothing, which
        # is exactly the failure the first test caught (`domain_part` stayed `None`).
        ctx.session.undo.push(Composite(label="mesh.body_role", commands=list(changes)), project)
        return {
            **Lister(ctx).entry(first),
            "changed": Composite(label="mesh.body_role", commands=changes),
        }

    # solid or left_out: set the flag, leave the domain alone.
    want = role != "left_out"
    # A left-out part must not keep a role it can no longer be: clear it if it held one.
    if (
        role == "left_out"
        and settings is not None
        and any(str(settings.params.get("domain_part") or "") == node.id for node in nodes)
    ):
        changes.append(SetParam(settings.id, "domain_part", None))
    for node in nodes:
        changes.append(SetParam(node.id, "simulate", want))
    ctx.session.undo.push(Composite(label="mesh.body_role", commands=list(changes)), project)
    return {
        **Lister(ctx).entry(first),
        "changed": Composite(label="mesh.body_role", commands=changes),
    }


def _settings_by_id(project: Any) -> Any:
    """The mesh settings node when it has been renamed, found by its stable id or its type.

    **Two fallbacks, in that order**: the stable id is derived from the original path and survives a
    rename, so it identifies the node exactly; the type is the last resort for a node whose id was
    somehow lost, and there can only be one `mesh_settings` in a project - the invariant the
    type's own spec holds (`children` is empty, and `project.add` refuses a second one by id).
    """
    from stokes.mesh.model import SETTINGS_PATH
    from stokes.model.ids import stable_id

    for node in project.nodes():
        if node.type != "mesh_settings":
            continue
        if node.id == stable_id("n", SETTINGS_PATH):
            return node
    for node in project.nodes():
        if node.type == "mesh_settings":
            return node
    return None


@command(
    "mesh.configure",
    params=(
        ParamDef(
            "mesher",
            "string",
            required=False,
            description="The mesher to use: " + ", ".join(spec.id for spec in MESHERS) + ".",
        ),
        ParamDef(
            "values",
            "object",
            required=False,
            description="Settings to set: base_size, domain, domain_part, box, margin, max_cells, "
            "cores, max_non_ortho, max_skewness. Quantities as text with a unit or SI numbers.",
        ),
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
    ),
)
def mesh_configure(  # noqa: PLR0912 - the brief's checks, in its order, in one readable place
    ctx: CommandContext,
    mesher: str | None = None,
    values: dict[str, Any] | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Create or change ``/mesh/settings``.

    Every check runs before anything is pushed, so a refusal leaves the tree exactly as it was.
    """
    from stokes.errors import ValidationError
    from stokes.model.commands import AddNode, Composite, SetParam

    project = ctx.session.require_project()
    given = dict(values or {})
    # **This check is not conditional on `mesher` being given.** It was nested inside the
    # `if mesher is not None` branch first, so `mesh.configure(values={"mesher": "snappy"})`
    # slipped past
    # it and the value was written as an ordinary parameter — measured: no refusal, and the
    # settings node
    # was created with `mesher` set through the wrong path.
    if "mesher" in given:
        # The brief's rule: `mesher` is the command's own parameter. Accepting it in both places
        # would
        # make the order of two contradicting values decide the result.
        raise MeshInvalid("give 'mesher' as the command's own parameter, not in values")
    if mesher is not None:
        spec = find_mesher(mesher)
        if not spec.built:
            raise MeshNotBuilt(f"{spec.label} is not available: {spec.why}")
    if "cores" in given:
        from stokes.cores import core_count

        given["cores"] = core_count(given["cores"])
    converted = _as_si(given, "mesh_settings")
    _check_box(converted.get("box"))
    if "base_size" in converted:
        _positive(converted["base_size"], "base_size")
    if "surface_size" in converted:
        # **Zero means "the base size", so only a NEGATIVE one is refused** (T-205 amendment 4).
        value = converted["surface_size"]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
            raise MeshInvalid(f"surface_size may not be negative; got {value}")

    # **Found by PATH, and a rename breaks that** (claude-win2's verdict, leftover 4): he saw the
    # `settings` row offering Rename and Delete, asked whether the node is protected, and the honest
    # answer is that it is NOT - deliberately, because `mesh.configure` can recreate it. But a
    # rename then left this lookup finding nothing, so the command tried to ADD a node whose
    # stable id was taken, and raised `ModelError: node id 'n_44373f58' already exists`.
    #
    # `ensure_stage_settings` already solved this for `project.open` by looking the node up by its
    # STABLE ID and its TYPE as well as its path; `mesh.configure` is the same question asked later,
    # so it asks it the same way.
    settings = project.find(SETTINGS_PATH) or _settings_by_id(project)
    created = settings is None
    # **Narrowed once, here.** `created` and `settings is None` are the same fact, but only an
    # explicit check lets a type checker see it — and a `settings.id` behind `if not created` is
    # exactly the shape that raises AttributeError the day a caller passes None.
    # The explicit form, not a ternary: this is the line that would raise AttributeError if the
    # narrowing were wrong, and a nested conditional expression hides which branch it took.
    if settings is None:  # noqa: SIM108 - an explicit branch, because the else branch can raise
        target_id = node_id or SETTINGS_ID
    else:
        target_id = str(settings.id)
    commands: list[Any] = []
    if created:
        # The node first, then its parameters: `AddNode` takes params, but going through
        # `SetParam` for
        # every value keeps ONE path for "how a setting is written", so a refusal on the third value
        # behaves the same whether the node was just created or has existed for weeks.
        commands.append(
            AddNode(
                parent="/mesh",
                type="mesh_settings",
                node_name="settings",
                params={},
                node_id=target_id,
            )
        )
    if mesher is not None:
        commands.append(SetParam(node=target_id, param="mesher", value=mesher))
    for name, value in converted.items():
        commands.append(SetParam(node=target_id, param=name, value=value))

    if commands:
        try:
            ctx.session.undo.push(
                Composite(label="mesh.configure", commands=list(commands)), project
            )
        except ValidationError as exc:
            # The model refused a value the catalogue allowed (a pattern, a range). Surface it
            # as a mesh
            # error with the model's own message, so a client sees one error family from this
            # command.
            raise MeshInvalid(str(exc)) from exc
    ctx.record(mesher=mesher, values=converted, node_id=target_id)

    # **The same two-step lookup as above**, because a RENAMED node is not at the path: the first
    # version of this line found None and raised "the settings node was not created" immediately
    # after successfully writing to it - measured.
    node = project.find(SETTINGS_PATH) or _settings_by_id(project)
    if node is None:  # pragma: no cover - the push above either created it or raised
        raise MeshInvalid("the settings node was not created")
    if created:
        ctx.session.emit("node.added", node_id=node.id, path=SETTINGS_PATH)
    for name in converted:
        ctx.session.emit("node.changed", node_id=node.id, path=SETTINGS_PATH, param=name)
    if mesher is not None and not created:
        ctx.session.emit("node.changed", node_id=node.id, path=SETTINGS_PATH, param="mesher")
    return {"settings": _summary(node), "created": created}


def _summary(node: Any) -> dict[str, Any]:
    """A settings node as a client reads it.

    **The shape `node.summary()` already has**: ``{id, type, name, status, params: {…}, path,
    children_count}``. My first version spread the parameters to the top level, which would have
    made ``mesh.configure`` the one command whose reply is shaped differently from every
    ``node.get`` — a
    client would have needed two readers for the same object.
    """
    return dict(node.summary())


@command(
    "mesh.list",
    params=(),
)
def mesh_list(ctx: CommandContext) -> dict[str, Any]:
    """The settings node (or null) and the results, read from the tree only.

    A ``stale`` flag is deliberately absent: T-145b adds it when there is a basis to compare
    against, and a flag that cannot yet be computed would be a flag that always says false.
    """
    project = ctx.session.require_project()
    settings = project.find(SETTINGS_PATH)
    results_node = project.find(RESULTS_PATH)
    in_use = active_result(results_node)
    results: list[dict[str, Any]] = []
    if results_node is not None:
        for child in results_node.children if hasattr(results_node, "children") else []:
            if getattr(child, "type", "") != "mesh_result":
                continue
            results.append(
                {
                    "id": child.id,
                    "path": child.path,
                    "name": child.name,
                    "state": child.params.get("state"),
                    "status": _status(child.params.get("state")),
                    "cells": child.params.get("cells"),
                    "ok": child.params.get("ok"),
                    "mesher": child.params.get("mesher"),
                    "host": child.params.get("host"),
                    # The one the viewport shows and a run solves on (`mesh.use`).
                    "active": in_use is not None and child.id == in_use.id,
                }
            )
    return {
        "settings": _summary(settings) if settings is not None else None,
        "results": results,
    }


def _status(state: Any) -> str:
    """A sentence a user can read for a result's state.

    ``interrupted`` is spelled out because it is the one state whose name does not say what
    happened: the job did not fail, the agent stopped reporting it.
    """
    return {
        "queued": "Waiting to start",
        "running": "Meshing",
        "cancelling": "Stopping",
        "done": "Finished",
        "failed": "Failed",
        "cancelled": "Cancelled",
        "interrupted": "Interrupted: the agent stopped reporting this job",
        "skipped": "Skipped: no host was available",
    }.get(str(state), str(state))


def _service(ctx: CommandContext) -> Any:
    """The session's mesh service, imported here so `stokes.run` stays off the start-up path."""
    from stokes.mesh.service import MeshService

    return MeshService.of(ctx.session)


@command(
    "mesh.generate",
    params=(
        ParamDef(
            "host",
            "string",
            required=False,
            description="The host to mesh on; default: the settings' own host.",
        ),
        ParamDef(
            "wait",
            "boolean",
            required=False,
            default=False,
            description="Wait for the mesh to finish (not allowed on the server thread).",
        ),
        ParamDef(
            "force",
            "boolean",
            required=False,
            default=False,
            description="Mesh again even when nothing that decides the mesh has changed.",
        ),
        ParamDef("result_id", "string", required=False, description="Reuse an id (journals)."),
    ),
)
def mesh_generate(
    ctx: CommandContext,
    host: str | None = None,
    wait: bool = False,
    force: bool = False,
    result_id: str | None = None,
) -> dict[str, Any]:
    """Mesh the model: build the case, send it to a host and follow the job.

    **Not an undo step.** A result is written by a job, like an operation's output; undoing past a
    completed mesh would leave the settings that produced it. The command is journaled with the
    result id
    it chose, so a replay recreates the same node.

    **A host that cannot mesh does not raise**: the result is written `skipped` with the reason,
    so a
    journal recorded on a machine with OpenFOAM replays on one without.
    """
    from stokes.api.commands.compute import default_host
    from stokes.errors import ValidationError
    from stokes.geometry.store import GeometryStore

    project = ctx.session.require_project()
    service = _service(ctx)
    settings = project.find(SETTINGS_PATH)
    chosen = (
        host
        or (str(settings.params.get("host") or "") if settings is not None else "")
        or default_host(ctx)
    )
    if wait and GeometryStore.of(ctx.session).blocks_session():
        raise ValidationError("wait=true would block the server; follow the mesh.* events")
    # **`dict(...)`, so the declared return type is real.** `MeshService.generate` is a session
    # extension
    # reached through `Any`, and mypy cannot see through it: an explicit copy says the command
    # returns a
    # JSON object rather than leaving it to be inferred as `Any` from a branch.
    info = dict(service.generate(project, chosen, force, result_id))
    ctx.record(result_id=str(info["result"]))
    if wait:
        service.wait(None)
        info = dict(service.status())
        info["reused"] = False
    return info


@command(
    "mesh.use",
    params=(ParamDef("result", "string", description="The mesh_result node id to use."),),
)
def mesh_use(ctx: CommandContext, result: str) -> dict[str, Any]:
    """Choose which kept mesh is in use: the one the viewport shows and a run solves on.

    **One result per mesher is kept** (ADR-0034), so a project can hold, say, a tetrahedral and a
    polyhedral mesh of the same part. Not an undo step, like the results themselves; journaled, so
    a replay solves on the same mesh. Returns `mesh.status` for the result now in use, whose
    `stale` says whether the current settings would still make it.
    """
    return dict(_service(ctx).use(result))


@command("mesh.cancel", params=(), mutates=False)
def mesh_cancel(ctx: CommandContext) -> dict[str, Any]:
    """Ask the running mesh to stop. The job's own event reports what happened."""
    return {"cancelled": bool(_service(ctx).cancel())}


@command("mesh.status", params=(), mutates=False)
def mesh_status(ctx: CommandContext) -> dict[str, Any]:
    """What the mesh is doing: its state, the step it is on, and whether it is stale."""
    return dict(_service(ctx).status())


@command(
    "mesh.log",
    params=(
        ParamDef(
            "lines",
            "integer",
            required=False,
            default=200,
            description="How many lines of the running step's log to return.",
        ),
    ),
    mutates=False,
)
def mesh_log(ctx: CommandContext, lines: int = 200) -> dict[str, Any]:
    """The tail of the running step's log, read from the agent."""
    return dict(_service(ctx).log(lines))


@command("mesh.tasks", params=(), mutates=False)
def mesh_tasks(ctx: CommandContext) -> dict[str, Any]:
    """The guided-meshing tasks in order, each with its state and what it needs.

    **Read-only and cheap**: the states come from the tree and the service's cached basis parts +
    precheck, which the debounced stale pass fills after edits, so this builds a snapshot only on
    a cache miss (never per call). Neither this nor `mesh.precheck` is journaled.
    """
    from stokes.api.commands.compute import default_host
    from stokes.mesh.tasks import mesh_tasks as task_rows

    project = ctx.session.require_project()
    service = _service(ctx)
    settings = project.find(SETTINGS_PATH)
    result = active_result(project.find(RESULTS_PATH))
    return dict(
        task_rows(
            project,
            prepared=service.prepared(),
            result=result,
            status=service.status(),
            settings=settings,
            host=(
                str(settings.params.get("host") or "")
                if settings is not None and settings.params.get("host")
                else default_host(ctx)
            ),
        )
    )


@command("mesh.precheck", params=(), mutates=False)
def mesh_precheck(ctx: CommandContext) -> dict[str, Any]:
    """Build the mesh case without sending it and report everything wrong or worth knowing.

    The same cached record `mesh.tasks` reads; a cache miss builds the case dry and keeps it.
    """
    prepared = _service(ctx).prepared()
    return dict(prepared.get("precheck") or {})


@command(
    "mesh.estimate",
    params=(
        ParamDef(
            "values",
            "object",
            required=False,
            description="Settings to try without saving: base_size, surface_size. "
            "Quantities as text with a unit or SI numbers.",
        ),
    ),
    mutates=False,
)
def mesh_estimate(ctx: CommandContext, values: dict[str, Any] | None = None) -> dict[str, Any]:
    """How many cells the current (or tried) sizes give, as a range, before meshing.

    The estimate is pure over the snapshot and cached with the prepared record; `values` tries
    sizes without saving anything — the tree is untouched, and every surface re-resolves through
    the same precedence the dictionary uses. On a project with no meshable case the reply says
    so with the precheck's findings instead of a number nobody could trust.
    """
    prepared = _service(ctx).prepared()
    snapshot = prepared.get("snapshot")
    if snapshot is None:
        # **No meshable case is an answer, not an error**: a failed build's findings are what the
        # Sizing page shows, and a number invented from nothing would be worse than none.
        return {
            "ok": False,
            "message": "no meshable case to estimate; the precheck says what is missing",
            "findings": list((prepared.get("precheck") or {}).get("findings") or []),
        }
    tried: dict[str, Any] = {}
    if values is not None:
        if not isinstance(values, dict):
            raise MeshInvalid(
                "`values` must be an object of settings to try",
                details={"code": "mesh.invalid", "parameter": "values"},
            )
        # **The same conversion `mesh.configure` uses**: quantities as text with a unit or SI
        # numbers, so the page can send what its fields hold. Only the two sizes the estimate
        # can move are accepted — anything else is a refusal, not a silently ignored key.
        tried = _as_si(dict(values), "mesh_settings")
        unknown = set(tried) - {"base_size", "surface_size"}
        if unknown:
            raise MeshInvalid(
                f"only base_size and surface_size can be tried; got {sorted(unknown)!r}",
                details={"code": "mesh.invalid", "parameter": "values"},
            )
    from stokes.mesh.estimate import estimate

    numbers = estimate(
        snapshot,
        base_size=tried.get("base_size"),
        surface_size=tried.get("surface_size"),
    )
    numbers["ok"] = True
    return numbers


@command(
    "mesh.layer_height",
    params=(
        ParamDef("y_plus", "number", description="The wanted y+ of the first cell, e.g. 1 or 30."),
        ParamDef(
            "velocity",
            "any",
            description="Bulk or free-stream speed: text with a unit, or SI number.",
        ),
        ParamDef(
            "length",
            "any",
            description="Reference length: text with a unit, or SI number.",
        ),
        ParamDef(
            "region",
            "string",
            required=False,
            description="Region whose material gives density and viscosity; default: the fluid "
            "region.",
        ),
    ),
    mutates=False,
)
def mesh_layer_height(
    ctx: CommandContext,
    y_plus: float,
    velocity: Any,
    length: Any,
    region: str | None = None,
) -> dict[str, Any]:
    """The first-layer thickness that gives the wanted y+, from a flat-plate estimate.

    **The height is the first CELL's, not its centre's**: y+ is defined at the centre, so the
    height is twice the centre distance — both numbers are in the reply, and the page says so.
    The fluid's density and viscosity come from the region's material at its own reference
    temperature, through the same evaluation the materials panel uses.
    """
    import numpy as np

    from stokes.errors import ValidationError
    from stokes.units import dimension as named
    from stokes.units import is_quantity_text, parse_quantity, to_si

    project = ctx.session.require_project()

    def si(raw: Any, what: str, dimension: str) -> float:
        """A quantity as SI number, quantity text or an expression — the material module's
        accepted forms, so the page can send what its fields hold."""
        dim = named(dimension)
        if isinstance(raw, bool):
            raise ValidationError(f"{what} expects a quantity, got a boolean")
        if isinstance(raw, int | float):
            return float(raw)
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            q = parse_quantity(text, dim) if is_quantity_text(text) else project.evaluate(text, dim)
            return to_si(q, dim)
        raise ValidationError(
            f"{what} expects a quantity; got {raw!r}", details={"parameter": what}
        )

    from stokes.api.commands.setup_plan import region_nodes

    regions = region_nodes(project)
    if region is not None:
        node = next((r for r in regions if str(r.name) == region or str(r.id) == region), None)
        if node is None:
            raise MeshInvalid(
                f"no region named or id'd {region!r}",
                details={"code": "mesh.invalid", "parameter": "region"},
            )
    else:
        node = next((r for r in regions if str(r.params.get("kind", "")) == "fluid"), None)
        if node is None:
            raise MeshInvalid(
                "set a material on the fluid region first: there is no fluid region to read",
                details={"code": "mesh.no_material"},
            )
    material_ref = node.params.get("material")
    if not isinstance(material_ref, str) or not material_ref:
        raise MeshInvalid(
            f"set a material on the fluid region first ({node.name!r} has none); "
            f"{node.path} is where it goes",
            details={"code": "mesh.no_material", "region": str(node.id)},
        )
    material = project.find(material_ref)
    if material is None or material.type != "material":
        raise MeshInvalid(
            f"the region {node.name!r} names a material that is not one",
            details={"code": "mesh.no_material", "region": str(node.id)},
        )

    from stokes.physics.materials.evaluate import definitions_of, evaluate_all, state_of

    state = state_of(material)
    definitions = definitions_of(material)
    missing = [k for k in ("density", "viscosity") if k not in definitions]
    if missing:
        raise MeshInvalid(
            f"the material {material.name!r} has no {', '.join(missing)} property; "
            "the flat-plate estimate needs both",
            details={"code": "mesh.no_material", "properties": missing},
        )
    grid = np.array([state.T_ref], dtype=np.float64)
    pressures = np.array([state.p_ref], dtype=np.float64)
    values = evaluate_all(definitions, state, ["density", "viscosity"], grid, pressures)

    def at(kind: str) -> float:
        return float(np.asarray(values[kind]).ravel()[0])

    try:
        from stokes.mesh.yplus import yplus_height

        result = yplus_height(
            y_plus=float(y_plus),
            velocity=si(velocity, "velocity", "velocity"),
            length=si(length, "length", "length"),
            density=at("density"),
            viscosity=at("viscosity"),
        )
    except ValueError as exc:
        raise MeshInvalid(str(exc), details={"code": "mesh.invalid"}) from exc
    return {
        "first_height": result.first_height,
        "y_centre": result.y_centre,
        "reynolds": result.reynolds,
        "skin_friction": result.skin_friction,
        "density": result.density,
        "viscosity": result.viscosity,
        "temperature": state.T_ref,
        "region": str(node.id),
        "material": str(material.id),
        "method": result.method,
    }


@command("mesh.quality", params=(), mutates=False)
def mesh_quality(ctx: CommandContext) -> dict[str, Any]:
    """The current mesh's quality: checkMesh's verdict, the limits, distributions, cell types,
    worst faces.

    **Everything from one record**: the checkMesh numbers and the `cell_types` (Part A) from
    the result's `quality` parameter, the distributions and the worst faces (Part B) from
    `meta.quality_dist` — which says `{"pending": true}` until the follow-up fetch returns, and
    `{"error": ...}` when it failed. The Quality page reads this one command.
    """
    project = ctx.session.require_project()
    result = active_result(project.find(RESULTS_PATH))
    if result is None:
        return {"ok": False, "message": "no mesh result yet"}
    quality = dict(result.params.get("quality") or {})
    return {
        "ok": True,
        "result": str(result.id),
        "checkmesh": quality,
        "distributions": result.meta.get("quality_dist", {"pending": True}),
    }
