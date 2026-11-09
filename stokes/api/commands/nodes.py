"""node.* commands: add, remove, rename, get, tree, types."""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.api.summaries import summarise, tree_dict
from stokes.model import AddNode, Node, Project, RemoveNode, RenameNode
from stokes.model.project import referenced_error

NODE_REF = ParamDef("node", "string", description="Node id or path, e.g. /physics/parameters/U_in.")


def _summary(project: Project, node: Node) -> dict[str, Any]:
    """Node summary plus display-unit strings for its quantity parameters."""
    return summarise(project, node)


@command(
    "node.add",
    params=(
        ParamDef("parent", "string", description="Parent node id or path."),
        ParamDef("type", "string", description="Node type (see schema.nodes)."),
        ParamDef("name", "string", description="Name, unique among siblings."),
        ParamDef("params", "object", required=False, description="Initial parameter values."),
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
        ParamDef("index", "integer", required=False, description="Position among siblings."),
    ),
)
def node_add(
    ctx: CommandContext,
    parent: str,
    type: str,  # noqa: A002 - mirrors the node field name
    name: str,
    params: dict[str, Any] | None = None,
    node_id: str | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """Add a node under a parent."""
    cmd = AddNode(parent, type, name, dict(params or {}), node_id=node_id, index=index)
    result = ctx.session.undo.push(cmd, ctx.session.require_project())
    ctx.record(node_id=cmd.node_id, index=cmd.index)
    ctx.session.emit("node.added", node_id=cmd.node_id, path=result["path"])
    return dict(result)


@command("node.remove", params=(NODE_REF,))
def node_remove(ctx: CommandContext, node: str) -> dict[str, Any]:
    """Remove a node and its subtree. Refused (model.referenced) while a node outside the
    subtree refers to it or to anything below it through a reference parameter: a boundary's
    or region's tag, a region's material, a boundary's partner, an objective's response
    (ADR-0022 decision 2.8); boundary.delete, region.delete and tag.delete with force remove
    what refers to them in the same step."""
    project = ctx.session.require_project()
    target = project.get(node)
    found = project.referrers(target.id, subtree=True)
    if found:
        raise referenced_error(target, found)
    cmd = RemoveNode(target.id)
    result = ctx.session.undo.push(cmd, project)
    ctx.session.emit("node.removed", node_id=cmd.node_id, path=result["path"], parent=cmd.parent_id)
    return dict(result)


@command("node.rename", params=(NODE_REF, ParamDef("name", "string", description="New name.")))
def node_rename(ctx: CommandContext, node: str, name: str) -> dict[str, Any]:
    """Rename a node."""
    cmd = RenameNode(node, name)
    result = ctx.session.undo.push(cmd, ctx.session.require_project())
    ctx.session.emit("node.changed", node_id=cmd.node_id, path=result["path"])
    return dict(result)


@command("node.get", params=(NODE_REF,), mutates=False)
def node_get(ctx: CommandContext, node: str) -> dict[str, Any]:
    """A node with its parameters and display strings."""
    project = ctx.session.require_project()
    return _summary(project, project.get(node))


@command(
    "node.tree",
    params=(
        ParamDef("root", "string", required=False, description="Start node; default the root."),
        ParamDef("depth", "integer", required=False, description="Levels to include."),
    ),
    mutates=False,
)
def node_tree(
    ctx: CommandContext, root: str | None = None, depth: int | None = None
) -> dict[str, Any]:
    """The tree (or a subtree) with children nested."""
    project = ctx.session.require_project()
    start = project.get(root) if root else project.root
    return tree_dict(project, start, depth)


@command("node.types", mutates=False, requires_project=False)
def node_types(ctx: CommandContext) -> dict[str, Any]:
    """Registered node types with their parameter schemas."""
    from stokes.model import registry

    return registry.json_schema()
