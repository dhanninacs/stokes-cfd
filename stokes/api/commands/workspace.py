"""workspace.* commands (ADR-0015): the arrangement of the interface as API state.

The workspace is not project model state. These commands write the user's
document (``~/.stokes/workspace.json``, or ``$STOKES_HOME/workspace.json``) or the
open project's overrides (``<name>.stokes/workspace.json``) at once; the
writing ones are journaled like any mutating command, but they never touch the
project's undo stack and ``history.undo`` does not revert them. Every write
emits ``workspace.changed {scope, path}``; ``path`` is null when a whole
document changed. The service lives in ``session.extensions["workspace"]`` and
is created on first use; ``stokes.workspace`` is imported then, not when the
registry loads. It follows the session's project through the session's extension
hooks (``project.new``, ``.open``, ``.save`` and ``.close`` call them); a Save as
creates it when the folder the project leaves holds overrides (:func:`carry_on_save_as`),
so they are carried along whether or not a ``workspace.*`` command ran.
Contract: ``docs/engineering/workspace-schema.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from stokes.api.registry import CommandContext, ParamDef, command

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.workspace.store import WorkspaceStore

EXTENSION = "workspace"
PROJECT_FILE = "workspace.json"
"""A project's overrides are ``<name>.stokes/workspace.json``. ``stokes.workspace.merge`` names the
file too; it is repeated here because that package is not imported until a service is needed
(``tests/test_session_hooks.py`` checks that the two agree)."""

_SCOPE = ParamDef(
    "scope",
    "string",
    required=False,
    default="user",
    description="user (the user's workspace.json) or project (the open project's overrides).",
)
_PATH_HELP = (
    "Dotted path into the workspace, e.g. layout.left.size, layout.left.panels.0.size, "
    'shortcuts.view.fit or shortcuts["view.fit"].'
)


def service_of(session: Session) -> WorkspaceStore:
    """The session's workspace service, created on first use and kept in its extensions.

    It follows the project through the session's extension hooks, so nothing subscribes here."""
    found = session.extensions.get(EXTENSION)
    if found is not None:
        return cast("WorkspaceStore", found)
    from stokes.workspace.store import WorkspaceStore

    def notify(event: str, data: dict[str, Any]) -> None:  # Any: JSON event payload
        session.emit(event, **data)

    service = WorkspaceStore(notify=notify, project_dir=session.project_path)
    session.extensions[EXTENSION] = service
    return service


def store(ctx: CommandContext) -> WorkspaceStore:
    """The session's workspace service, for a command."""
    return service_of(ctx.session)


def carry_on_save_as(session: Session, previous: Path) -> None:
    """``project.save`` to a new folder, from ``previous``: make the service when that folder
    holds overrides, so the save carries them along even when no ``workspace.*`` command has
    run (a session that has none to carry pays no import for the workspace package)."""
    try:
        holds_overrides = (previous / PROJECT_FILE).is_file()
    except OSError:  # a folder that cannot be read has nothing to carry
        return
    if holds_overrides:
        service_of(session)


def _project_dir(ctx: CommandContext, scope: str) -> Path | None:
    """The project folder whose overrides take part; project scope insists on one."""
    if scope == "project":
        ctx.session.require_project()
        if ctx.session.project_path is None:
            from stokes.workspace.errors import ProjectFolderRequired

            raise ProjectFolderRequired(
                "the project has no folder yet; save it (project.save path=...) before giving it "
                "workspace overrides"
            )
    return ctx.session.project_path


@command(
    "workspace.get",
    params=(ParamDef("path", "string", required=False, description=_PATH_HELP + " Omit for all."),),
    mutates=False,
    requires_project=False,
)
def workspace_get(ctx: CommandContext, path: str | None = None) -> dict[str, Any]:
    """The effective workspace (defaults, then the user's, then the project's) with the layer
    each value comes from and every warning; or one value of it when path is given."""
    return store(ctx).get(path, _project_dir(ctx, "effective"))


@command(
    "workspace.set",
    params=(
        ParamDef("path", "string", description=_PATH_HELP),
        ParamDef("value", "any", description="Any JSON value valid at the path."),
        _SCOPE,
    ),
    requires_project=False,
)
def workspace_set(
    ctx: CommandContext, path: str, value: Any, scope: str = "user"
) -> dict[str, Any]:  # Any: JSON value
    """Set one workspace value in the user's document or the project's overrides. Not undoable
    with history.undo: the workspace is not project state."""
    return store(ctx).set(path, value, scope, _project_dir(ctx, scope))


@command(
    "workspace.reset",
    params=(
        ParamDef("scope", "string", description="user or project: whose document to reset."),
        ParamDef(
            "path",
            "string",
            required=False,
            description="Remove only this key, so the layer below shows through.",
        ),
    ),
    requires_project=False,
)
def workspace_reset(ctx: CommandContext, scope: str, path: str | None = None) -> dict[str, Any]:
    """Delete a scope's workspace document, or one key of it."""
    return store(ctx).reset(scope, path, _project_dir(ctx, scope))


@command("workspace.presets", mutates=False, requires_project=False)
def workspace_presets(ctx: CommandContext) -> dict[str, Any]:
    """The shipped presets and the user's own, each with a one-line description."""
    return store(ctx).presets()


@command(
    "workspace.apply_preset",
    params=(ParamDef("name", "string", description="A name from workspace.presets."), _SCOPE),
    requires_project=False,
)
def workspace_apply_preset(ctx: CommandContext, name: str, scope: str = "user") -> dict[str, Any]:
    """Apply a preset: every section it carries replaces that section in the scope. Shipped
    presets carry layout, islands, topbar and tree; theme and shortcuts stay yours."""
    return store(ctx).apply_preset(name, scope, _project_dir(ctx, scope))


@command(
    "workspace.save_preset",
    params=(
        ParamDef("name", "string", description="Lower-case letters, digits, _ and -."),
        ParamDef("title", "string", required=False, description="Display name."),
        ParamDef("description", "string", required=False, description="One line."),
    ),
    requires_project=False,
)
def workspace_save_preset(
    ctx: CommandContext, name: str, title: str | None = None, description: str | None = None
) -> dict[str, Any]:
    """Save the effective workspace as a user preset in ~/.stokes/presets/<name>.json."""
    return store(ctx).save_preset(name, title, description, _project_dir(ctx, "effective"))


@command(
    "workspace.export",
    params=(
        ParamDef(
            "scope",
            "string",
            required=False,
            default="effective",
            description="effective (the merged workspace), user or project.",
        ),
        ParamDef(
            "sections",
            "array",
            required=False,
            description='Section names to include, e.g. ["theme"]; all when omitted.',
        ),
    ),
    mutates=False,
    requires_project=False,
)
def workspace_export(
    ctx: CommandContext, scope: str = "effective", sections: list[Any] | None = None
) -> dict[str, Any]:  # Any: section names checked by the store
    """A workspace document as JSON text, to share or to import elsewhere."""
    return store(ctx).export(scope, sections, _project_dir(ctx, scope))


@command(
    "workspace.import",
    params=(
        ParamDef("text", "string", description="A workspace document as JSON text."),
        _SCOPE,
    ),
    requires_project=False,
)
def workspace_import(ctx: CommandContext, text: str, scope: str = "user") -> dict[str, Any]:
    """Import a workspace document: each section it holds replaces that section in the scope;
    invalid values are left out and reported."""
    return store(ctx).import_text(text, scope, _project_dir(ctx, scope))
