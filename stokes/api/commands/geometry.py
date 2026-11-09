"""geometry.* commands: geometry import, part meshes and bulk styling (ADR-0014, decision G1;
ADR-0021 decision 1.1: the import is ``geometry.import`` for STEP, IGES and BREP, and
``geometry.import_step`` stays its alias; decisions 4.1 and 4.2: ``geometry.style``).

``geometry.import`` adds a placeholder ``part`` under ``/geometry/parts`` (creating the group
when it is missing) as one undo step and imports the file in a worker process. Once the file is
read, a single solid stays that part; several solids turn it into a group, of the same id, with
one part per solid. Progress and meshes arrive as ``geometry.*`` events, described in
:mod:`stokes.geometry.store`; each part's mesh is served by ``GET /api/geometry/{node_id}``.

Import rule: nothing from :mod:`stokes.geometry` at import time (no numpy, no OCP, and no cost
for sessions that never touch geometry); handlers import what they use.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.model import Command, Composite, SetParam

if TYPE_CHECKING:
    from stokes.geometry.store import GeometryStore
    from stokes.model import Node, Project

GEOMETRY_REF = ParamDef("node", "string", description="Part or group: node id or path.")


def _store(ctx: CommandContext) -> GeometryStore:
    from stokes.geometry.store import GeometryStore

    return GeometryStore.of(ctx.session)


def _geometry_node(ctx: CommandContext, ref: str) -> Node:
    node = ctx.session.require_project().get(ref)
    if node.type not in ("part", "group"):
        raise ValidationError(
            f"{node.path} is a {node.type}, not a part or a group of parts",
            details={"node": node.path},
        )
    return node


def _result(store: GeometryStore, node: Node) -> dict[str, Any]:
    """A node summary with its import state: a part's own, or a group's over its parts."""
    if node.type == "group":
        state = store.summarise(node)["import"]
        return {**node.summary(), "import": state}
    return {**node.summary(), "import": store.status(node.id), "url": f"/api/geometry/{node.id}"}


@command(
    "geometry.import",
    params=(
        ParamDef(
            "path",
            "string",
            description="A geometry file: STEP (.step/.stp), IGES (.iges/.igs), BREP (.brep/.brp) "
            "or a mesh-only format (STL .stl, OBJ .obj, 3MF .3mf).",
        ),
        ParamDef(
            "name",
            "string",
            required=False,
            description="Name of the part, or of the group when the file holds several solids "
            "(letters, digits, underscores); default: from the file name.",
        ),
        ParamDef(
            "units",
            "string",
            required=False,
            description="The length unit, for a format the file does not declare (BREP, STL, "
            "OBJ; default mm). STEP, IGES and 3MF files declare their own units and refuse it.",
        ),
        ParamDef(
            "wait",
            "boolean",
            required=False,
            default=False,
            description="Return only when the import has ended (scripts and the CLI); "
            "interactive clients follow the geometry.* events instead.",
        ),
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
    ),
)
def geometry_import(
    ctx: CommandContext,
    path: str,
    name: str | None = None,
    units: str | None = None,
    wait: bool = False,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Import a geometry file: one part per solid; undo removes them and their meshes.

    A mesh-only format (STL, OBJ, 3MF) gives parts of ``family: "mesh"`` with no B-rep: their
    faces are the triangulation's regions between feature edges, and the B-rep-only commands
    refuse them with ``geometry.mesh_only``.
    """
    from stokes.geometry import formats
    from stokes.geometry.commands import ImportStep
    from stokes.geometry.files import check_source_file, part_name_for, require_cad, unique_name
    from stokes.geometry.store import PARTS_PATH

    require_cad()
    source = check_source_file(path)
    fmt = formats.format_for(source)
    if units is not None:
        if fmt.units == "file":
            raise ValidationError(
                f"{fmt.label} files declare their own units; drop the units parameter",
                details={"format": fmt.id},
            )
        _length_unit(units)
    project = ctx.session.require_project()
    store = _store(ctx)
    if wait and store.blocks_session():
        raise ValidationError(
            "wait=true would block the server; follow the geometry.* events instead"
        )
    if name is None:
        parts = project.find(PARTS_PATH)
        taken = {c.name for c in parts.children} if parts is not None else set()
        name = unique_name(part_name_for(source), taken)
    cmd = ImportStep(store, str(source), name, units=units, node_id=node_id)
    result = ctx.session.undo.push(cmd, project)
    ctx.record(path=str(source), name=name, node_id=cmd.node_id)
    if cmd.created_parts_group:
        ctx.session.emit("node.added", node_id=cmd.parts_group_id, path=PARTS_PATH)
    ctx.session.emit("node.added", node_id=cmd.node_id, path=result["path"])
    if wait:
        try:
            store.wait(result["id"])
        except KeyboardInterrupt:
            store.cancel(result["id"])
            raise
    return _result(store, project.get(result["id"]))


def _length_unit(units: str) -> None:
    """The unit text must name a length unit; the pint registry is the unit table (ADR-0012)."""
    from stokes.units.registry import ureg

    try:
        ureg().Quantity(1.0, units).to("m")
    except Exception as exc:
        raise ValidationError(
            f"units {units!r} is not a length unit", details={"units": units}
        ) from exc


@command("geometry.formats", mutates=False, requires_project=False)
def geometry_formats(ctx: CommandContext) -> dict[str, Any]:
    """The import formats and the picker's filter, so the front end builds the picker from the
    API (ADR-0021 decision 1.1)."""
    from stokes.geometry import formats
    from stokes.geometry.files import MAX_SOURCE_BYTES

    return {
        "formats": [
            {
                "id": fmt.id,
                "label": fmt.label,
                "suffixes": list(fmt.suffixes),
                "family": fmt.family,
                "units": fmt.units,
            }
            for fmt in formats.FORMATS
        ],
        "filter": formats.filter_string(),
        # **`max_upload_bytes` is here so the browser can refuse an oversize file BEFORE
        # sending it** (Claude, 2026-10-15). It is the same `MAX_SOURCE_BYTES` the import check
        # and the upload route enforce - one constant, read from the place that defines it, so
        # the three cannot drift into disagreeing about what "too big" means. A client-side
        # check is a courtesy, not a boundary: the route refuses an over-cap body regardless.
        "max_upload_bytes": MAX_SOURCE_BYTES,
    }


@command("geometry.list", mutates=False)
def geometry_list(ctx: CommandContext) -> list[dict[str, Any]]:
    """Every part node with its import state and mesh counts."""
    project = ctx.session.require_project()
    store = _store(ctx)
    return [_result(store, node) for node in project.nodes() if node.type == "part"]


@command("palette.list", mutates=False, requires_project=False)
def palette_list(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """The colours this build offers for a part or a tag, and which of them a condition may use.

    **The palette is server-owned** (`geometry.model.SELECTION_PALETTE`), so the client reads it
    here rather than keeping a copy: a second list would drift, and the tag commands assign from
    this one in turn. It exists for the style card's palette swatches — a UI that accepted any
    `#rrggbb` could produce a project whose colours no longer match the ones the commands pick, and
    the condition catalogue refuses a tag colour outside this set (`conditions.catalogue`: "no new
    colour enters the palette").
    """
    from stokes.geometry.model import SELECTION_PALETTE

    return {"colors": [color.lower() for color in SELECTION_PALETTE]}


@command("geometry.info", params=(GEOMETRY_REF,), mutates=False)
def geometry_info(ctx: CommandContext, node: str) -> dict[str, Any]:
    """A part (parameters, import state, mesh counts, cache, mesh URL), or a group's parts."""
    target = _geometry_node(ctx, node)
    store = _store(ctx)
    return store.summarise(target) if target.type == "group" else store.describe(target)


@command(
    "geometry.cancel_import",
    params=(
        ParamDef(
            "node",
            "string",
            required=False,
            description="Part or group whose import to stop; default: every import.",
        ),
    ),
    mutates=False,
)
def geometry_cancel_import(ctx: CommandContext, node: str | None = None) -> dict[str, Any]:
    """Stop running imports; unfinished parts stay, marked failed, until undone or removed."""
    store = _store(ctx)
    if node is None:
        return {"cancelled": store.cancel()}
    target = _geometry_node(ctx, node)
    return {"cancelled": store.cancel_many([n.id for n in target.walk()])}


@command("geometry.prepare", mutates=False, requires_project=False)
def geometry_prepare(ctx: CommandContext) -> dict[str, Any]:
    """Start a worker process now, so the next import does not wait seconds for OCP to load.

    Call it when an import dialog opens; the worker exits after five idle minutes.
    """
    from stokes.geometry.files import require_cad

    require_cad()
    return {"workers": _store(ctx).prewarm()}


# ---------------------------------------------------------------- styling (ADR-0021, 4.1 to 4.2)

_STYLE_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
"""A colour as #rrggbb, either case: the command lowers it before it reaches the model, whose
pattern admits lower case only."""


def _style_color(text: str) -> str:
    if not _STYLE_COLOR.match(text):
        raise ValidationError(f"a colour is #rrggbb; got {text!r}", details={"color": text})
    return text.lower()


def _styled_nodes(project: Project, refs: list[str]) -> list[Node]:
    """The nodes the references name: parts, tags, the parts under a group (a plain group
    holds none, so a boundary that is still one, from before ADR-0022, resolves to nothing) and
    the tag a boundary points at. Duplicates collapse."""
    found: dict[str, Node] = {}
    for ref in refs:
        node = project.get(ref)
        match node.type:
            case "part" | "tag":
                targets = [node]
            case "group":
                targets = [n for n in node.walk() if n.type == "part"]
            case "boundary":  # T-035: a boundary names the tag it styles
                tag = project.find(node.params.get("tag") or "")
                targets = [tag] if tag is not None and tag.type == "tag" else []
            case _:
                raise ValidationError(
                    f"{node.path} is a {node.type}, not a part, tag or group of parts",
                    details={"node": node.path},
                )
        for target in targets:
            found.setdefault(target.id, target)
    return list(found.values())


def _current(project: Project, node: Node, name: str) -> Any:  # Any: JSON value
    """What the node holds for ``name`` now; a value absent from an old project is its default."""
    value = node.params.get(name)
    return value if value is not None else project.spec_of(node).param(name).default


@command(
    "geometry.style",
    params=(
        ParamDef(
            "nodes",
            "array",
            description="Parts, tags or groups of parts: node ids or paths. A group styles every "
            "part beneath it, at any depth; duplicates collapse.",
        ),
        ParamDef("visible", "boolean", required=False, description="Show or hide."),
        ParamDef("color", "string", required=False, description="Display colour as #rrggbb."),
        ParamDef(
            "opacity",
            "number",
            required=False,
            description="0 transparent to 1 opaque.",
        ),
    ),
)
def geometry_style(
    ctx: CommandContext,
    nodes: list[Any],  # Any: JSON references
    visible: bool | None = None,
    color: str | None = None,
    opacity: float | None = None,
) -> dict[str, Any]:
    """Style many parts, tags or groups at once: one undo step, one journal record, a
    ``node.changed`` per changed node and parameter. A node already in the target state is
    unchanged, and a call that changes nothing pushes no undo step."""
    if not nodes or not all(isinstance(ref, str) and ref for ref in nodes):
        raise ValidationError("give node ids or paths of parts, tags or groups of parts")
    if visible is None and color is None and opacity is None:
        raise ValidationError("give at least one of visible, color or opacity")
    target: dict[str, Any] = {}
    if visible is not None:
        target["visible"] = visible
    if color is not None:
        target["color"] = _style_color(color)
    if opacity is not None:
        if not 0.0 <= opacity <= 1.0:
            raise ValidationError(f"opacity must lie in [0, 1]; got {opacity!r}")
        target["opacity"] = float(opacity)
    project = ctx.session.require_project()
    styled = _styled_nodes(project, list(nodes))
    steps: list[Command] = []
    changed: list[str] = []
    unchanged: list[str] = []
    emitted: list[tuple[Node, str]] = []
    for node in styled:
        diff = [name for name, value in target.items() if _current(project, node, name) != value]
        if diff:
            changed.append(node.id)
            steps.extend(SetParam(node.id, name, target[name]) for name in diff)
            emitted.extend((node, name) for name in diff)
        else:
            unchanged.append(node.id)
    if steps:
        ctx.session.undo.push(
            Composite(f"style {len(changed)} " + ("node" if len(changed) == 1 else "nodes"), steps),
            project,
        )
    for node, name in emitted:
        ctx.session.emit("node.changed", node_id=node.id, path=node.path, param=name)
    return {"changed": changed, "unchanged": unchanged, "resolved": len(styled)}


@command(
    "geometry.simulate",
    params=(
        ParamDef(
            "nodes",
            "array",
            description="Parts, or groups of parts: node ids or paths. A group takes every part "
            "beneath it, at any depth; duplicates collapse.",
        ),
        ParamDef(
            "simulate",
            "boolean",
            description=(
                "True to use these bodies in the mesh and the solve; false to leave them out."
            ),
        ),
    ),
)
def geometry_simulate(ctx: CommandContext, nodes: list[Any], simulate: bool) -> dict[str, Any]:
    """Leave bodies out of the simulation, or put them back: one undo step, one journal record.

    **Not the same control as the eye** (the workflow-v2 plan §2): `visible` is display-only and the
    mesher never reads it, while this decides whether a body is meshed, compiled and checked. That
    is
    why it is its own parameter and its own command rather than another keyword on `geometry.style`
    -
    one word meaning two things is how the eye and the row menu came to disagree about Hide.

    **A group resolves to the parts beneath it**, through the same `_styled_nodes` the styling
    command
    uses: a multi-solid import makes a group, and "leave this assembly out" is the sentence a user
    says. A node already in the target state is unchanged, and a call that changes nothing pushes no
    undo step.
    """
    if not nodes or not all(isinstance(ref, str) and ref for ref in nodes):
        raise ValidationError("give node ids or paths of parts or groups of parts")
    project = ctx.session.require_project()
    # **Only parts carry the flag**, so the resolution is narrower than styling's: a tag or a
    # boundary is not something a mesh can leave out. A reference that names no part is
    # reported rather than silently ignored, because "I left the housing out" and nothing
    # happening is the worst outcome.
    resolved: dict[str, Node] = {}
    for ref in nodes:
        node = project.get(ref)
        match node.type:
            case "part":
                resolved[node.id] = node
            case "group":
                for found in node.walk():
                    if found.type == "part":
                        resolved[found.id] = found
            case other:
                raise ValidationError(
                    f"{node.path} is a {other}, not a part or a group of parts; only bodies can be "
                    "left out of the simulation"
                )
    changed: list[str] = []
    unchanged: list[str] = []
    steps: list[Command] = []
    for node in resolved.values():
        if bool(node.params.get("simulate", True)) == simulate:
            unchanged.append(node.id)
            continue
        changed.append(node.id)
        steps.append(SetParam(node.id, "simulate", simulate))
    if steps:
        ctx.session.undo.push(
            Composite(
                f"{'use' if simulate else 'leave out'} {len(changed)} "
                + ("body" if len(changed) == 1 else "bodies"),
                steps,
            ),
            project,
        )
    for node_id in changed:
        node = project.get(node_id)
        ctx.session.emit("node.changed", node_id=node_id, path=node.path, param="simulate")
    return {"changed": changed, "unchanged": unchanged, "resolved": len(resolved)}
