"""`view.*` commands: the saved views of a run's results (plan RESULTS-OVERHAUL §3.5, brief R4).

A view is a `view` node under `/views` (`stokes.model.views`) holding a name and the figure's
record, the sidecar object the image export writes. `view.save` adds or replaces by name as ONE
undo step (the `/views` group is created inside it when the project has none, as `case.import`
makes `/cases`), so undo takes a save back whole and a replay finds the same node ids. The server
never reads a record.

There is no `view.apply`: applying a view is the front end setting its own stores from the record
(`web/src/panels/viewsModel.ts`), and the server has nothing to change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.model import AddNode, Command, Composite, RemoveNode, SetParam

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.model.nodes import Node

VIEW_REF = ParamDef("view", "string", description="A view's id, its tree path or its name.")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _views(ctx: CommandContext) -> list[Node]:
    """The project's view nodes, in tree order (the order they were first saved in)."""
    from stokes.model.views import VIEW_TYPE, VIEWS_PATH

    group = ctx.session.require_project().find(VIEWS_PATH)
    return [] if group is None else [n for n in group.children if n.type == VIEW_TYPE]


def _view(ctx: CommandContext, ref: str) -> Node:
    """The view an id, a path or a name points at. A name is the text the user gave it."""
    found = ctx.session.require_project().find(ref)
    if found is not None:
        from stokes.model.views import VIEW_TYPE

        if found.type == VIEW_TYPE:
            return found
    for node in _views(ctx):
        if node.params.get("name") == ref:
            return node
    raise ValidationError(
        f"there is no saved view {ref!r}", code="view.unknown", details={"view": ref}
    )


def _summary(node: Node) -> dict[str, Any]:  # Any: JSON
    return {
        "id": node.id,
        "name": node.params.get("name", ""),
        "created": node.params.get("created", ""),
    }


@command(
    "view.save",
    params=(
        ParamDef("name", "string", description="What the view is called; the same name replaces."),
        ParamDef(
            "record",
            "object",
            description="The figure's settings: the sidecar object the image export writes.",
        ),
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
        ParamDef("created", "string", required=False, description="The stamp to keep (journals)."),
    ),
)
def view_save(
    ctx: CommandContext,
    name: str,
    record: dict[str, Any],  # Any: JSON
    node_id: str | None = None,
    created: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Save a view under a name, as one undo step: adds it, or replaces the view of that name.

    A replaced view keeps its place in the list and its id; its record and its `created` stamp are
    the new ones. Reply `{id, name, created, replaced}`. Codes: `view.name_invalid` (empty, over 80
    characters or a control character), `validation` for a record that is not an object or is over
    256 KB.
    """
    from stokes.model.ids import is_id, new_id, stable_id
    from stokes.model.views import VIEW_TYPE, VIEWS_GROUP, VIEWS_PATH, clean_name

    project = ctx.session.require_project()
    text = clean_name(name)
    if node_id is not None and not is_id(node_id):
        raise ValidationError(f"{node_id!r} is not a node id", code="validation")
    stamp = created or _now()
    existing = next((n for n in _views(ctx) if n.params.get("name") == text), None)
    steps: list[Command] = []
    if existing is not None:
        steps.append(SetParam(existing.id, "record", record))
        steps.append(SetParam(existing.id, "created", stamp))
        chosen = existing.id
    else:
        chosen = node_id or new_id()
        if project.find(chosen) is not None:
            raise ValidationError(f"node id {chosen} is taken", code="validation")
        group = project.find(VIEWS_PATH)
        if group is None:
            steps.append(AddNode("/", "group", VIEWS_GROUP, node_id=stable_id("g", VIEWS_PATH)))
        steps.append(
            AddNode(
                VIEWS_PATH,
                VIEW_TYPE,
                f"v_{chosen[2:]}",
                {"name": text, "record": record, "created": stamp},
                node_id=chosen,
            )
        )
    made_group = any(isinstance(s, AddNode) and s.type == "group" for s in steps)
    ctx.session.undo.push(Composite(label=f"Save view {text}", commands=steps), project)
    ctx.record(node_id=chosen, created=stamp)
    if made_group:
        ctx.session.emit("node.added", node_id=stable_id("g", VIEWS_PATH), path=VIEWS_PATH)
    if existing is None:
        ctx.session.emit("node.added", node_id=chosen, path=project.get(chosen).path)
    else:
        ctx.session.emit("node.changed", node_id=chosen, path=existing.path, param="record")
    return {"id": chosen, "name": text, "created": stamp, "replaced": existing is not None}


@command("view.list", params=(), mutates=False)
def view_list(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """The saved views, in the order they were first saved: `{views: [{id, name, created}]}`."""
    return {"views": [_summary(n) for n in _views(ctx)]}


@command("view.get", params=(VIEW_REF,), mutates=False)
def view_get(ctx: CommandContext, view: str) -> dict[str, Any]:  # Any: JSON
    """A saved view's record, as it was saved (the sidecar object). Code: `view.unknown`."""
    record = _view(ctx, view).params.get("record")
    return dict(record) if isinstance(record, dict) else {}


@command("view.remove", params=(VIEW_REF,))
def view_remove(ctx: CommandContext, view: str) -> dict[str, Any]:  # Any: JSON
    """Remove a saved view, as one undo step. Reply `{id, name}`. Code: `view.unknown`."""
    project = ctx.session.require_project()
    target = _view(ctx, view)
    summary = _summary(target)
    cmd = RemoveNode(target.id)
    result = ctx.session.undo.push(cmd, project)
    ctx.session.emit("node.removed", node_id=cmd.node_id, path=result["path"], parent=cmd.parent_id)
    return {"id": summary["id"], "name": summary["name"]}
