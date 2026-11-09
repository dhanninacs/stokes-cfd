"""history.* commands: undo and redo."""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, command


@command("history.undo")
def history_undo(ctx: CommandContext) -> dict[str, Any]:
    """Undo the last command."""
    cmd = ctx.session.undo.undo(ctx.session.require_project())
    ctx.session.emit("history.changed")
    return {"undone": cmd.describe() if cmd else None, "kind": cmd.name if cmd else None}


@command("history.redo")
def history_redo(ctx: CommandContext) -> dict[str, Any]:
    """Redo the last undone command."""
    cmd = ctx.session.undo.redo(ctx.session.require_project())
    ctx.session.emit("history.changed")
    return {"redone": cmd.describe() if cmd else None, "kind": cmd.name if cmd else None}


@command("history.state", mutates=False)
def history_state(ctx: CommandContext) -> dict[str, Any]:
    """What undo and redo would do next."""
    undo = ctx.session.undo
    return {
        "can_undo": undo.can_undo,
        "can_redo": undo.can_redo,
        "undo": undo.undo_text,
        "redo": undo.redo_text,
        "undo_kind": undo.undo_kind,
        "redo_kind": undo.redo_kind,
    }
