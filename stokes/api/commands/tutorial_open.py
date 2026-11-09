"""`tutorial.open`: a tutorial from the user's install, in a new project (ADR-0029, T-312).

**What the Start screen's Tutorials card calls.** One command, not a client sequence of
`project.new` then `case.import`, so the GUI, the CLI and an assistant open a tutorial the same way
and a journal replays it as one step (CLAUDE.md rule 4).

The project goes in the user's projects folder (`project.defaults`), named after the case: the
case's folder name made into a project name, with `_2`, `_3`, ... when that folder is taken, so
opening a tutorial twice gives two independent projects and never writes over the first. The case
is then imported with `case.import`'s own rules: a case the external lint refuses closes the new
project again, drops it from Recent and removes the folder, and the reply says why.

`stokes.cases` and the project helpers are imported inside the function so `import stokes.api`
stays within its start-up budget.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from stokes.api.commands.case import import_into
from stokes.api.commands.project import project_close, project_new, project_save
from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.log import get_logger

log = get_logger(__name__)

#: How many numbered folders are tried before giving up on finding a free one.
MAX_FOLDER_TRIES = 999


def _free_folder(base: Path, stem: str) -> tuple[str, Path]:
    """The first `<stem>`, `<stem>_2`, ... whose project folder does not exist yet."""
    from stokes.model.persist import project_dir

    for number in range(1, MAX_FOLDER_TRIES + 1):
        name = stem if number == 1 else f"{stem}_{number}"
        target = project_dir(base / name)
        if not target.exists():
            return name, target
    raise ValidationError(
        f"every project folder from {stem} to {stem}_{MAX_FOLDER_TRIES} is taken",
        code="tutorial.no_folder",
    )


def _discard(ctx: CommandContext, folder: str, made: Path | None) -> None:
    """Close the project made for a refused case, forget it in Recent, remove ``made`` if given."""
    from stokes.model.recent import forget

    project_close(ctx)
    try:
        forget(folder)
    except OSError as exc:
        log.warning(
            "could not drop a refused tutorial from Recent", extra={"error": str(exc)[:200]}
        )
    if made is not None and made.is_dir():
        # Only a folder this command created a moment ago: nothing of the user's is in it.
        shutil.rmtree(made, ignore_errors=True)


@command(
    "tutorial.open",
    params=(
        ParamDef(
            "tutorial",
            "string",
            description="A tutorial id from tutorial.catalogue (incompressibleFluid/cavity).",
        ),
        ParamDef(
            "path",
            "string",
            required=False,
            description="The new project's folder; default: a free one in the projects folder.",
        ),
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
        ParamDef(
            "snapshot",
            "string",
            required=False,
            description="The expected snapshot hash (journals).",
        ),
    ),
    requires_project=False,
)
def tutorial_open(
    ctx: CommandContext,
    tutorial: str,
    path: str | None = None,
    node_id: str | None = None,
    snapshot: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Open a tutorial from the active tutorial folder in a new project, and import it.

    Returns `{project: {name, path}, case: <case.import's reply>}`. The project is created and
    opened first; `case.status` is `refused` (with the file, line and reason of each finding) when
    the case did not pass the external lint, and the project is then closed, dropped from Recent
    and its new folder removed (`project` is null). Codes:
    `tutorial.no_root`, `case.source_missing`, `case.source_escape`, `tutorial.no_folder`, and
    `project.new`'s own.
    """
    from stokes.cases.importer import node_name
    from stokes.model.browse import projects_folder

    leaf = PurePosixPath(tutorial.replace("\\", "/")).name
    if not leaf:
        raise ValidationError("a tutorial id names a case folder", code="case.source_invalid")
    stem = node_name(leaf, set())
    if path is None:
        name, target = _free_folder(projects_folder(), stem)
    else:
        name, target = stem, Path(path)
    fresh = not target.exists()
    created = project_new(ctx, name=name, path=str(target))
    folder = str(ctx.session.project_path or target)
    case, journal = import_into(ctx, tutorial, None, None, node_id, snapshot)
    if case.get("status") == "refused":
        # **A refused case leaves nothing behind**: the project made for it is closed, dropped from
        # Recent and, when this command made its folder, the folder is removed. Before, every
        # refused tutorial left an empty project folder and a Recent row opening to "Import CAD".
        _discard(ctx, folder, target if fresh else None)
        return {"project": None, "case": case}
    # **Saved at once**: the case is the project's whole content, and `project.new` wrote the
    # folder before the import. Unsaved, reopening the tutorial from Recent showed an empty
    # project with "Import CAD file" although the case's copy was on disk.
    project_save(ctx)
    ctx.record(
        path=folder,
        **({"node_id": journal["node_id"], "snapshot": journal["snapshot"]} if journal else {}),
    )
    return {"project": {"name": created.get("name", name), "path": folder}, "case": case}
