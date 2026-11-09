"""project.* commands: create, open, save, inspect, close.

``project.new``, ``project.open``, ``project.save`` and ``project.close`` tell the session's
extensions (``Session.notify_extensions``) once the state has changed and before they emit their
event, so a subsystem's service is in step with the project when a client hears of the change.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.log import get_logger
from stokes.model import Project, SetDisplayUnits, load_project, save_project
from stokes.model.persist import project_dir
from stokes.units.dimensions import UNIT_SYSTEMS

log = get_logger(__name__)

if TYPE_CHECKING:
    from stokes.api.session import Session


def _saved(session: Session, project: Project, previous: Path | None) -> None:
    """Tell the extensions the project was written to ``session.project_path``.

    A save to a new folder takes files that sit beside the project along: the studies' design
    tables and the project's workspace overrides. The services that carry them may not exist
    yet, since nothing makes them before their first command, so each subsystem's
    ``carry_on_save_as`` makes its own when there is something to carry (a session without
    studies or overrides pays no import for them).
    """
    path = session.project_path
    # **The write is what makes the project clean**, so the stamp is taken here - the one place
    # every save path goes through.
    session.saved_revision = project.revision
    # Saved work is what the earlier session's copy held, or newer: nothing is left to offer.
    session.recovery_at = None
    if previous is not None and previous != path:
        from stokes.api.commands import study, workspace

        study.carry_on_save_as(session, project)
        workspace.carry_on_save_as(session, previous)
    session.notify_extensions("project_saved", project=project, path=path, previous=previous)


def _parts_in_file(folder: Path) -> int:
    """How many `part` nodes the project file in ``folder`` holds; 0 when there is none to read.

    A plain JSON walk rather than `load_project`: this runs before every save and must not pay
    for migrations or a node registry, and a file it cannot read is simply "no parts" - the
    save goes ahead as it always did.
    """
    import json

    from stokes.model.persist import PROJECT_FILE

    target = folder / PROJECT_FILE
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    count = 0
    stack: list[Any] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("type") == "part":
                count += 1
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return count


def _refuse_dropping_parts(project: Project, folder: Path) -> None:
    """Refuse a save that would write a project with NO parts over a file that has some.

    **Measured 2026-11-01 (`pipe4.stokes`):** a project with four parts, a mesh and a converged
    run was found as a 1.8 kB file with an empty geometry group, written at 06:10 while the
    server was being restarted with `--project` for a bug repro - its `runs/` and `cache/`
    folders still held everything. Whatever wrote it (a save before the geometry was in memory,
    or a new project pointed at the folder), the file it replaced was the user's work. A save
    that drops every part is refused with a way out: Save as writes elsewhere, and a project
    the user emptied on purpose still has its parts on disk until they confirm by saving to a
    new folder. A project that never had parts on disk is untouched by this.
    """
    on_disk = _parts_in_file(folder)
    if on_disk == 0:
        return
    in_memory = sum(1 for node in project.root.walk() if node.type == "part")
    if in_memory > 0:
        return
    raise ValidationError(
        f"refusing to save: the project in memory has no parts, the file in {folder} has "
        f"{on_disk}; use Save as to write it to a new folder",
        code="project.save_would_drop_parts",
        details={"path": str(folder), "parts_on_disk": on_disk},
    )


def ensure_stage_settings(project: Project) -> list[str]:
    """Create `/mesh/settings` and `/solver/settings` with their defaults if they are missing.

    **Non-journaled and non-undoable, like the structural skeleton** (G2, the owner's order of
    2026-10-15). A fresh project used to have an empty Mesh group and an empty Solver group
    until the
    first `mesh.configure` or `solve.configure`, so the tree showed no settings for those
    stages and a
    user had nothing to click. The nodes belong to the project the way the top-level groups do.

    **It is not an undo step and it does not dirty the project.** `Project.new` builds the
    skeleton the
    same way, and a user who opens a project and changes nothing has changed nothing.

    **Idempotent**, and it reports what it added so the caller can emit `node.added` for
    exactly those:
    `node.added` for a node that was already there would make a client duplicate it.
    """
    from stokes.engines.model import SETTINGS_PATH as SOLVER_PATH
    from stokes.mesh.model import SETTINGS_PATH as MESH_PATH
    from stokes.model.ids import stable_id

    added: list[str] = []
    for path, node_type in ((MESH_PATH, "mesh_settings"), (SOLVER_PATH, "solver_settings")):
        if project.find(path) is not None:
            continue
        # **Look for the node by its STABLE ID and by its TYPE before adding** (Opus review item 3).
        # A user may rename `/mesh/settings` - it is not structural - and then `project.add` raised
        # `node id 'n_...' already exists`, because the stable id is derived from the path and the
        # node was still there under another name. Measured: `project.open` raised and the session
        # was left holding a project whose `project_opened` never ran.
        node_id = stable_id("n", path)
        if _stage_node_taken(project, path, node_id, node_type):
            continue
        parent, _, name = path.rpartition("/")
        # **Skip when the parent group is missing.** A project file can lack `/mesh` or `/solver`
        # (written by an older build, edited by hand, produced by another tool), and `add` would
        # raise on the missing parent - half-failing the open for a file the user can do nothing
        # about. There is no group to put the settings in, so there is nothing to ensure.
        if project.find(parent) is None:
            continue
        project.add(parent, node_type, name, node_id=node_id)
        added.append(path)
    if added:
        # **The structural adds are not changes to announce.** `add` records the node as touched; a
        # freshly opened project must not look dirty because of a node the caller never asked for.
        project.drain_touched()
    return added


#: A Windows path longer than this is close enough to the 260-character limit that a case folder
#: inside it (a mesh's `constant/polyMesh/...`, a slice cache) can fail to open.
LONG_PATH_WARNING = 180


def path_warning(path: Path | str | None) -> str | None:
    """A few words when the project folder is deep enough to hit Windows' 260-character limit."""
    if path is None or os.name != "nt" or len(str(path)) <= LONG_PATH_WARNING:
        return None
    return (
        "This folder path is long; deep case files may fail to open. "
        "Move the project nearer the drive root."
    )


def _info(ctx: CommandContext) -> dict[str, Any]:
    project = ctx.session.require_project()
    return {
        "path_warning": path_warning(ctx.session.project_path),
        "name": project.name,
        "path": str(ctx.session.project_path) if ctx.session.project_path else None,
        "display_units": project.display_units,
        "created": project.created,
        "modified": project.modified,
        "nodes": sum(1 for _ in project.nodes()),
        "can_undo": ctx.session.undo.can_undo,
        "can_redo": ctx.session.undo.can_redo,
        # **`dirty` is a comparison, not a stored flag** - see `Session.saved_modified`. A project
        # that has never been saved is dirty by definition: it exists only in memory.
        "dirty": project.revision != ctx.session.saved_revision,
        # Unsaved work an earlier session left (`project.recover` restores it), or null.
        "recovery": ctx.session.recovery_at,
    }


@command(
    "project.new",
    params=(
        ParamDef("name", "string", description="Project name (letters, digits, underscores)."),
        ParamDef("path", "string", required=False, description="Where to save (.stokes folder)."),
        ParamDef(
            "display_units",
            "string",
            required=False,
            default="si",
            description=f"One of {', '.join(UNIT_SYSTEMS)}.",
        ),
    ),
    requires_project=False,
)
def project_new(
    ctx: CommandContext, name: str, path: str | None = None, display_units: str = "si"
) -> dict[str, Any]:
    """Create a new project with the standard top-level groups."""
    project = Project.new(name, display_units=display_units)
    # **The session is NOT mutated until the folder is settled** (Opus F2, data loss). This line
    # used to sit here, before the folder check: a refused `project.new` - a non-empty folder, a
    # symlink escape - left the session holding the NEW empty project while `project_path` still
    # named the OLD one, so the next `project.save` wrote the empty project's `project.json` over
    # the first project's and destroyed it. Measured on Windows by Opus; the fix is to validate and
    # create FIRST and switch the session only once that succeeded, so a refusal leaves the session
    # exactly as it was.
    # **A PROJECT ALWAYS HAS A FOLDER** (claude-win2's 1745 message, decision 2). The owner asked
    # for a new project to take a folder and for everything to be created inside it, and a
    # folder-less project is what the three scratch directories existed to serve - `/tmp` runs,
    # `/tmp` imports, `/tmp` shapes. Removing the concept removes the cause rather than sweeping up
    # after it, which is what `SCRATCH_PREFIXES` and its 24-hour sweep did: measured on the owner's
    # machine, **127 imports folders and 5662 runs folders** had accumulated.
    #
    # **`path` stays optional so that the 195 existing call sites keep working**, and the fallback
    # is a real folder under the user's Stokes home rather than None. A caller that WANTS a
    # particular folder still passes one; a caller that does not gets a usable project instead of a
    # temporary one.
    # **THE FOLDER RULES ARE APPLIED HERE, not only in `stokes.model.folders`** (claude-win2,
    # reading 1, requirement 2). That module has 35 tests and **nothing in production called
    # `prepare` or `project_folder`** - measured - so the refusals the owner asked for (a non-empty
    # folder, a symlink escape, a name that is not a path segment) were **unreachable**: the tests
    # exercised the module directly and could not see whether the product wired it up.
    #
    # A caller that names a folder gets the strict rules. A caller that does not gets the default
    # helper, which picks a free name instead of refusing - a different question, and the only one
    # for which "just use the next number" is the right answer.
    if path:
        target = _chosen_folder(path)
    else:
        from stokes.model.folders import prepare

        # **The default folder is prepared too**, so a name that collides with a real file or a
        # stray folder is refused rather than written into - the same rule, applied to the folder
        # this side chose.
        target = prepare(_default_project_folder(name))
    # **The folder first, the session second** (Opus F2): `save_project` is the step that can fail
    # or be refused, and only a successful one may change what the session is looking at.
    saved_path = save_project(project, str(target))
    # A project that has never been saved has nothing to undo, and this is where it becomes the
    # session's. Both assignments sit together so a reader can see the switch happen once.
    ctx.session.project = project
    ctx.session.project_path = saved_path
    ctx.session.undo.clear()
    ctx.session.recovery_at = None
    # **A NEW project is DIRTY, so the baseline is deliberately NOT advanced.** It has never been
    # written: `project.new` builds the model and hands it to the session, and the file appears
    # when the user saves. That is why the fresh path resets the baseline while `open` sets it to
    # the loaded revision - the one difference between a project on disk and one that is not.
    ctx.session.saved_revision = -1
    # **No `node.added` here, deliberately.** `Session.emit` builds the node's summary, and a
    # `mesh_settings`/`solver_settings` summary formats a QUANTITY — which reaches
    # `stokes.units.quantities` -> pint -> **numpy**. The gate
    # `test_the_api_and_its_commands_load_no_numerics` exists precisely to keep numpy out of
    # `import stokes.api` + `project.new`, and emitting here broke it.
    #
    # Nothing is lost: `project.opened` follows immediately and carries the whole tree, so a client
    # sees both nodes either way. G2's point is that the nodes EXIST, not that each is announced.
    ensure_stage_settings(project)
    # **A new project is a recent project.** It was missing here because `project.new` saves
    # through a different branch from `project.open` and `project.save`, so recording in those two
    # alone left the commonest case out - a project created from the start screen and then nowhere
    # to be seen on it. Measured. **Unconditional now**, because every new project has a folder.
    _remember(ctx.session.project_path)
    ctx.session.notify_extensions("project_opened", project=project, path=ctx.session.project_path)
    ctx.session.emit("project.opened", **(ctx.session.project_opened_data() or {}))
    return _info(ctx)


def _default_project_folder(name: str) -> Path:
    """Where a project goes when the caller did not choose: `$STOKES_HOME/projects/<name>`.

    **A real folder, not a temporary one.** The point of decision 2 is that nothing a project works
    with lives in `/tmp`, and that includes the project made by a test or a script that did not name
    a folder - the alternative is the scratch directory this change exists to remove.

    **A name already taken gets a numbered one** rather than a refusal, because the caller did
    not ask for a specific folder and failing here would be a surprise: `duct`, `duct-2`. A caller
    that wants a particular folder passes it and gets the strict rules in `stokes.model.folders`.
    """
    from stokes.model.folders import check_name
    from stokes.model.persist import project_dir
    from stokes.workspace_root import user_root

    # **`project_dir` is what actually decides the name**, because `save_project` runs every path
    # through it and it APPENDS `.stokes` when the suffix is absent. My first version built
    # `<base>/duct` and checked that for a collision, while the file went to `<base>/duct.stokes` -
    # measured: two projects called `duct` in one session both landed in `duct.stokes`, the second
    # overwriting the first. Normalising here means the check is on the folder that is used.
    base = user_root() / "Projects"
    stem = check_name(name)
    candidate = project_dir(base / stem)
    number = 2
    while candidate.exists() and any(candidate.iterdir()):
        candidate = project_dir(base / f"{stem}-{number}")
        number += 1
    return candidate


def _chosen_folder(path: str) -> Path:
    """A folder the CALLER named: validated by the owner's rules, or refused.

    **`project_folder` when the argument is a folder to put the project in, `prepare` for the folder
    itself.** The owner's wording is "ask for which folder to start in, and all files etc should be
    created in this folder", so the argument is the project's own folder - it is created if absent,
    refused if it already holds anything, and its name must be usable as a path segment.

    **The refusal comes with a code the front end can act on** (`project.folder_not_empty`,
    `project.name_invalid`, `project.folder_invalid`), which is what `FolderError` carries.
    """
    from stokes.model.folders import prepare
    from stokes.model.persist import project_dir

    # **`project_dir` normalises the name**, because `save_project` runs every path through it and
    # appends `.stokes` - so preparing a folder that is not the one that will be used would check
    # nothing. Measured: my first version checked `<base>/duct` while the write went to
    # `<base>/duct.stokes`, and two projects overwrote each other.
    return prepare(project_dir(Path(path)))


@command(
    "project.open",
    params=(ParamDef("path", "string", description="Path to a .stokes folder."),),
    requires_project=False,
    mutates=False,
)
def project_open(ctx: CommandContext, path: str) -> dict[str, Any]:
    """Open an existing project.

    **`recovery` says when unsaved work was kept** (the autosave's recovery copy, newer than the
    project file): an ISO time a client offers to restore with `project.recover`, or null.
    """
    from stokes.model.persist import recovery_time

    folder = project_dir(path)
    _switch_to(ctx, load_project(folder), folder, dirty=False, recovery=_iso(recovery_time(folder)))
    return _info(ctx)


def _iso(stamp: float | None) -> str | None:
    """An epoch time as ISO 8601 in UTC, or None."""
    if stamp is None:
        return None
    from datetime import UTC, datetime

    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds")


def _switch_to(
    ctx: CommandContext, project: Project, folder: Path, *, dirty: bool, recovery: str | None = None
) -> None:
    """Make ``project``, read from ``folder``, the session's; ``dirty`` when it is not as saved.

    ``recovery`` is set before `project.opened` goes out, so a client that reads `project.info` on
    that event sees the offer.
    """
    # **The ensure runs BEFORE the session switches project** (Opus review item 3). It used to
    # run after `session.project`, `project_path` and the undo history were set, so anything it
    # raised left the session holding the new project with `project_opened` never notified - the
    # tree showed the new file while the run and geometry stores kept serving the previous one.
    # Half a switch is worse than none: a failure now happens with the session untouched.
    #
    # **A project saved by an older build gains them here**, so opening one shows the stages
    # rather than two empty groups. `project.open` is not on the numerics gate's path (that gate
    # runs `project.new`), and this is the one case where a client genuinely needs telling: it
    # may already hold the old tree without these nodes.
    added_paths = ensure_stage_settings(project)
    ctx.session.project = project
    ctx.session.project_path = folder
    ctx.session.recovery_at = recovery
    ctx.session.undo.clear()
    # **A freshly-opened project has no unsaved changes, so the baseline moves with it.** Loading
    # re-evaluates every expression, which counts revisions - measured: a project saved and
    # reopened compared dirty (revision 0 against a stale baseline of 2), so every Close would have
    # prompted to discard work the user never did.
    # A recovered project is the unsaved work itself, so it stays dirty until it is saved.
    ctx.session.saved_revision = -1 if dirty else project.revision
    for added in added_paths:
        node = project.find(added)
        if node is not None:
            ctx.session.emit("node.added", node_id=node.id, path=added)
    ctx.session.notify_extensions("project_opened", project=project, path=folder)
    _remember(folder)
    ctx.session.emit("project.opened", **(ctx.session.project_opened_data() or {}))


@command("project.autosave", mutates=False)
def project_autosave(ctx: CommandContext) -> dict[str, Any]:
    """Keep the unsaved changes in the project's recovery copy; the project file is not touched.

    **A copy beside the file, never a save over it**: a Save the user did not ask for would take
    away the choice to discard. Nothing is written for a project without unsaved changes or
    without a folder. A crash or a server restart before Ctrl+S then loses at most the time since
    the last autosave instead of the whole set-up (the mixing-elbow walkthrough, 2026-11-08):
    `project.open` reports the copy and `project.recover` restores it.
    """
    from stokes.model.persist import PROJECT_FILE, save_recovery

    project = ctx.session.require_project()
    folder = ctx.session.project_path
    if folder is None or project.revision == ctx.session.saved_revision:
        return {"saved": False}
    # A project never saved has no file to open a copy against; its first Save is the safeguard.
    if not (Path(folder) / PROJECT_FILE).is_file():
        return {"saved": False}
    save_recovery(project, folder)
    return {"saved": True}


@command("project.recover", mutates=False)
def project_recover(ctx: CommandContext) -> dict[str, Any]:
    """Replace the open project with its recovery copy: the unsaved work, dirty until saved.

    The copy stays on disk until a Save or a discarding Close, so a second crash before the
    user saves loses nothing either.
    """
    from stokes.model.persist import load_recovery, recovery_time

    folder = ctx.session.project_path
    if folder is None or recovery_time(folder) is None:
        raise ValidationError("there is no unsaved work to recover", code="project.no_recovery")
    _switch_to(ctx, load_recovery(folder), Path(folder), dirty=True)
    return _info(ctx)


@command("project.discard_recovery", mutates=False)
def project_discard_recovery(ctx: CommandContext) -> dict[str, Any]:
    """Throw the recovery copy away and keep the project as it was saved."""
    from stokes.model.persist import discard_recovery

    ctx.session.require_project()
    folder = ctx.session.project_path
    discarded = folder is not None and discard_recovery(folder)
    ctx.session.recovery_at = None
    # `project.info` changed (`recovery`); clients re-read it on a project event.
    ctx.session.emit("project.recovery_discarded", discarded=discarded)
    return {"discarded": discarded}


@command(
    "project.save",
    params=(ParamDef("path", "string", required=False, description="New location, optional."),),
    mutates=False,
)
def project_save(ctx: CommandContext, path: str | None = None) -> dict[str, Any]:
    """Save the project to its folder, **or copy it to a new one and switch to it**.

    **`path=` is Save as** and now copies the WHOLE folder (claude-win2's 1805 message,
    decision 3), which it did not before: measured, a copy was missing `imports/`, `cases/`,
    `logs/` and `preview.png`, because four subsystems each had to remember to carry its own and
    none owned the question. `project.save_as` is the same operation under its own name.
    """
    if path is not None:
        # **`_copy_to` directly, not `project_save_as`.** That name is wrapped by `@command`,
        # so its static type is `Any` and returning it fails `mypy --strict` with
        # `no-any-return` - measured. The plain helper carries the type and says the same
        # thing: `path=` IS Save as.
        return _copy_to(ctx, name=None, path=path, switch=True)
    project = ctx.session.require_project()
    target = ctx.session.project_path
    if target is None:
        raise ValidationError("the project has no path yet; pass one")
    _refuse_dropping_parts(project, Path(target))
    ctx.session.project_path = save_project(project, Path(target))
    _saved(ctx.session, project, ctx.session.project_path)
    _drop_recovery(ctx.session.project_path)
    _remember(ctx.session.project_path)
    ctx.session.emit("project.saved", path=str(ctx.session.project_path))
    return _info(ctx)


@command(
    "project.save_as",
    params=(
        ParamDef("name", "string", required=False, description="Name for the copy."),
        ParamDef("path", "string", required=False, description="Folder for the copy."),
    ),
)
def project_save_as(
    ctx: CommandContext, name: str | None = None, path: str | None = None
) -> dict[str, Any]:
    """Copy the whole project to a new folder **and switch to it**. The original is untouched.

    **Refused while something is running** (`project.busy`). Switching away from a live job
    would abandon it, which is what the `interrupted` marking exists to make visible - so the
    honest answer is to say no and offer the alternative: "Stop the run first, or use Save a
    copy".
    """
    # **The refusal is `_copy_to`'s**, which every switching caller reaches (Opus F6): keeping a
    # copy of it here would let the two drift and leave `project.save path=` - Save as under its
    # other name - as the one way past it.
    return _copy_to(ctx, name=name, path=path, switch=True)


@command(
    "project.save_copy",
    params=(
        ParamDef("name", "string", required=False, description="Name for the copy."),
        ParamDef("path", "string", required=False, description="Folder for the copy."),
    ),
)
def project_save_copy(
    ctx: CommandContext, name: str | None = None, path: str | None = None
) -> dict[str, Any]:
    """Copy the whole project to a new folder **and stay in the original** - a snapshot.

    **Allowed while something runs, with one exclusion**: the in-flight run's folder is left out,
    because a solver is writing it and its contents are not consistent to read. In the copy that run
    is recorded as **`interrupted`** - the existing semantics for "this copy has no live job",
    never a
    spinner - and the reply carries a warning naming it.
    """
    return _copy_to(ctx, name=name, path=path, switch=False)


def _copy_to(
    ctx: CommandContext, *, name: str | None, path: str | None, switch: bool
) -> dict[str, Any]:
    """The one copy operation behind Save as and Save a copy.

    **Both are undo-free and neither dirties the original**: the copy is a new folder built
    atomically by `copy_project`, and the original's files are only ever read.
    """
    from stokes.model import project_copy as copier
    from stokes.model.folders import check as check_folder
    from stokes.model.persist import project_dir, save_project
    from stokes.run.store import RunStore

    project = ctx.session.require_project()
    source = ctx.session.project_path
    if source is None:
        raise ValidationError("the project has no folder yet; pass one")

    # **The refusal lives HERE, not only in `project_save_as`** (Opus F6). `project.save path=` is
    # Save as by another name and reached this function directly, so a running job could still be
    # abandoned through it - the one entry point the check did not cover. Doing it here means every
    # caller that switches is covered, including any future one. `switch=False` is Save a copy: it
    # stays put, so a live run is not abandoned and the run's own folder is simply left out.
    store = RunStore.of(ctx.session)
    live = store.running()
    if switch and live is not None:
        raise ValidationError(
            # **claude-win2's 1805 text, verbatim.** My first version paraphrased it as "a run is
            # in progress; stop it first, or use Save a copy to keep working", and a test asserting
            # the specified wording failed - correctly, because the wording is part of the contract
            # the front end shows the user.
            "Stop the run first, or use Save a copy",
            code="project.busy",
        )

    folder = _copy_destination(name, path, source)

    # **The in-flight run is excluded from a copy, never from a Save as** - a Save as would have
    # been
    # refused before reaching here, so nothing is excluded for it.
    skip: set[str] = set()
    warnings: list[str] = []
    if live is not None and not switch:
        # **Paths, not names** (Opus F4): `runs/<id>`, so the exclusion actually matches. The old
        # form returned every run folder's bare name, which could only match a TOP-LEVEL entry and
        # so never matched `runs/<id>` - the live run was copied mid-write while the warning said
        # it had been left out.
        skip = copier.in_flight_paths(live.id)
        warnings.append(f"run {live.id} was in progress and is recorded as interrupted in the copy")

    # **Validated, NOT created** (`check`, not `prepare`): the copy is committed by renaming
    # `<dest>.partial` onto this path, and a rename onto an existing directory fails on Windows with
    # `WinError 183` - both commands then reported `project.copy_failed` and left an empty
    # destination folder behind (Opus F1). POSIX never showed it, which is why the suite was green
    # on Linux and the owner's machine broke.
    prepared = check_folder(project_dir(folder))
    # **The shape cache is NOT copied here - the extensions carry it.** `cache_dir` holds the
    # geometry store's B-reps and pack, the run store's `results/` fields and the mesh service's
    # `mesh/` surfaces, and each of those has its own `project_saved` hook with its own rules. The
    # copy carrying the lot was a regression: it put a CLOSED store's `cache/geometry/<part>.mesh`
    # into the copy, which is what `test_a_closed_store_ignores_the_hooks` forbids, and it
    # pre-empted the run store's `record.origin` re-stamp that a reopened project needs to find its
    # runs. What the blanket copy is FOR is the folders no extension owns: `imports/`, `cases/`,
    # `logs/`, `preview.png` (measured, round 158: those four were the ones being lost).
    from stokes.geometry.cache import SUBDIR as _CACHE_SUBDIR

    # **The WHOLE cache, and the hook decides what travels.** `GeometryStore.project_saved`
    # already decides this correctly and the tests measure the contract: an OPEN store's save
    # writes `cache/geometry/<part>.mesh` (compared against `store.transport(...)`), and a
    # CLOSED store must contribute nothing - that hook's `if self._closed: return`. Copying
    # any part of the cache here overrides that decision. Measured: skipping only `results/`
    # and `mesh/` let a closed store's `cache/geometry/*.mesh` travel, and
    # `test_a_closed_store_ignores_the_hooks` failed.
    skip = set(skip) | {_CACHE_SUBDIR[0]}
    try:
        warnings.extend(copier.copy_project(Path(source), prepared, skip=skip))
    except copier.CopyError as exc:
        raise ValidationError(str(exc), code="project.copy_failed") from exc
    # The source's recovery copy is its unsaved work, which the copy gets as its project file below.
    _drop_recovery(prepared)

    # **The copy gets the model in MEMORY, not the one the source folder holds.** The file on disk
    # is only as fresh as the last save: `project.new` creates the folder and the skeleton, then an
    # import adds its `part` node to memory alone, so a copy that inherited the source's
    # `project.json` came out missing the part entirely - and with it the whole
    # `geometry/parts` subtree. **Measured**: the reopened copy's tree had no part node, and
    # `test_a_project_whose_source_vanished_still_works_from_its_cache` failed on
    # "the part vanished from the reopened project" while `find(part_id)` in the SAME session
    # still answered. Writing here also covers Save a copy, whose session does not move.
    save_project(project, prepared)

    if live is not None and not switch:
        _mark_interrupted_in_copy(prepared, live.id)

    # **The extensions own their own cache subtrees**, and the blanket copy must not pre-empt
    # them: `GeometryStore.project_saved` deliberately contributes NOTHING when its store is
    # closed, and the copy carrying `cache/geometry` anyway is a regression (measured:
    # `test_a_closed_store_ignores_the_hooks`). They also do work the copy cannot - the run store
    # re-stamps each record's `origin`, without which a reopened project finds no runs.
    previous_here = ctx.session.project_path
    if switch:
        ctx.session.project_path = prepared
        _saved(ctx.session, project, previous_here)
        # Save as saved the unsaved work, in the new folder: the original's copy of it is done with.
        _drop_recovery(previous_here)
        _remember(prepared)
    else:
        # **Save a copy does NOT send `project_saved`** (Opus F3). The session stays in the
        # original, and the stores that keep a folder of their own - the study store's tables, the
        # workspace store's overrides - took `path` as their new home, so the next table written
        # from the ORIGINAL session was written into the copy. `project_copied` says a snapshot
        # exists without moving them; the extensions that only contribute files to the copy (the
        # geometry, run and mesh stores) implement it and leave the session's own folder alone.
        ctx.session.notify_extensions("project_copied", project=project, path=prepared)

    ctx.session.emit("project.saved", path=str(ctx.session.project_path))
    reply = _info(ctx)
    reply["copy"] = str(prepared)
    if warnings:
        reply["warnings"] = warnings
    return reply


def _mark_interrupted_in_copy(folder: Path, run_id: str) -> None:
    """Record the in-flight run as `interrupted` **in the copy only**.

    **`interrupted`, not `cancelled`**: the run is not stopped, the COPY simply has no live job.
    That
    is the existing semantics for a record whose job cannot be reached, and it is what stops a
    spinner appearing over a run nobody is watching.
    """
    import json

    record_file = folder / "runs" / run_id / "record.json"
    try:
        data = json.loads(record_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    data["state"] = "interrupted"
    with contextlib.suppress(OSError):
        record_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _copy_destination(name: str | None, path: str | None, source: Path) -> Path:
    """Where the copy goes, **refusing a destination inside the source**.

    A copy into its own subfolder would read what it is writing, and the second generation of that
    is
    a folder that contains itself. Refused rather than special-cased.
    """
    from stokes.model.folders import inside

    candidate = Path(path) if path is not None else source.with_name(f"{name or source.stem}-copy")
    if inside(candidate, source):
        raise ValidationError(
            f"{candidate} is inside {source}; a copy must go somewhere else",
            code="project.copy_inside_source",
        )
    return candidate


@command("project.info", mutates=False)
def project_info(ctx: CommandContext) -> dict[str, Any]:
    """Summary of the open project."""
    return _info(ctx)


@command(
    "project.set_units",
    params=(ParamDef("system", "string", description=f"One of {', '.join(UNIT_SYSTEMS)}."),),
)
def project_set_units(ctx: CommandContext, system: str) -> dict[str, Any]:
    """Change the display unit system (values stay in SI)."""
    result = ctx.session.undo.push(SetDisplayUnits(system), ctx.session.require_project())
    ctx.session.emit("project.changed", display_units=system)
    return dict(result)


@command("project.close", mutates=False)
def project_close(ctx: CommandContext) -> dict[str, Any]:
    """Close the open project without saving.

    **The recovery copy goes too**: closing without saving is the user discarding the changes, and
    offering them back on the next open would undo that choice. A crash or a restart never comes
    through here, which is what keeps the copy for them.
    """
    name = ctx.session.require_project().name
    _drop_recovery(ctx.session.project_path)
    ctx.session.recovery_at = None
    ctx.session.project = None
    ctx.session.project_path = None
    ctx.session.undo.clear()
    ctx.session.notify_extensions("project_closed")
    ctx.session.emit("project.closed", name=name)
    return {"closed": name}


def _stage_node_taken(project: Project, path: str, node_id: str, node_type: str) -> bool:
    """Whether the settings node this path names already exists, under any name.

    **Two ways to be there.** The path can find it, which is the ordinary case; or a node with
    the same STABLE ID can, which is what a RENAME leaves behind - `stable_id` is derived from
    the path, so the renamed node keeps the id the ensure is about to create. The type is
    checked as well, so a node of another kind holding the id does not make the ensure skip a
    real one.
    """
    if project.find(path) is not None:
        return True
    return any(str(node.id) == node_id and node.type == node_type for node in project.root.walk())


def _drop_recovery(folder: Path | None) -> None:
    """Remove the recovery copy after a save or a discarding close. **Never raises**: the save or
    the close has happened, and a copy that could not be removed is older than the file, so the
    next open does not offer it (`recovery_time`)."""
    if folder is None:
        return
    from stokes.model.persist import discard_recovery

    try:
        discard_recovery(folder)
    except Exception as exc:
        log.warning("could not remove the recovery copy", extra={"error": str(exc)[:200]})


def _remember(folder: Path | None) -> None:
    """Add a project to the recent list. **Never raises.**

    A start screen's list is a convenience; a project that saved correctly must not be reported
    as failed because a list could not be updated.
    """
    if folder is None:
        return
    from stokes.model.recent import remember

    try:
        remember(folder)
    except Exception as exc:
        log.warning("could not record a recent project", extra={"error": str(exc)[:200]})


@command("project.samples", params=(), requires_project=False, mutates=False)
def project_samples(ctx: CommandContext) -> dict[str, Any]:
    """The shipped samples, with **where a copy of each would go**.

    **The location is shown before anything is created** (claude-win2's 1805 message: "a short
    line under a tile saying where the copy will go"). It is computed rather than reserved, so
    listing samples costs no folder and cannot fail on a read-only home.
    """
    del ctx
    from stokes.model.samples import describe, registry
    from stokes.workspace_root import user_root

    home = user_root()
    return {"samples": [describe(sample, home) for sample in registry()]}


@command(
    "project.sample_open",
    params=(ParamDef("sample", "string", description="The sample id, from `project.samples`."),),
    requires_project=False,
)
def project_sample_open(ctx: CommandContext, sample: str) -> dict[str, Any]:
    """Open a sample **as a working copy in a folder of its own**, and return that project.

    **The shipped sample is never opened or edited in place** (claude-win2's 1805 message): a
    copy is made under `<STOKES_HOME>/projects/samples/<id>-<NN>/` and THAT is opened. Opening
    the same sample twice gives two independent projects, so a user experimenting cannot lose it.

    **The template is not a project on disk at all** - it is a builder function - so there is
    nothing a user can modify even by accident. That is stronger than a read-only folder, and it is
    why the sample's files need neither shipping nor protecting.
    """
    # **The builders are registered by importing `stokes.app.demo`**, which both hosts do at
    # start-up (`stokes/app/__main__.py`, `stokes/server/__main__.py`). It cannot happen here:
    # `stokes.api` must not import `stokes.app` - measured, the layering gate refused it - because
    # the API is headless and every client of it is not.
    #
    # A process that never imported the app gets `project.sample_unavailable` from the unregistered
    # builder, which is the honest answer for a build without the optional `cad` extra.
    from stokes.model.samples import find, next_folder
    from stokes.workspace_root import user_root

    chosen = find(sample)
    if chosen is None:
        from stokes.errors import ValidationError
        from stokes.model.samples import registry

        known = ", ".join(item.id for item in registry())
        # **The code goes on the exception, not in `details`** - `StokesError` takes a per-case
        # `code` override for exactly this, and putting it in `details` leaves `exc.code` reading
        # the class's own `validation`. Measured: my first version reported `validation`.
        raise ValidationError(
            f"no sample called {sample!r}; the shipped ones are: {known}",
            code="project.unknown_sample",
            details={"sample": sample},
        )

    folder = next_folder(chosen.id, user_root())
    # **A normal `project.new` at the copy's folder**, so every rule the owner asked for applies:
    # everything is created inside it, it is refused if something is already there, and it is
    # remembered in Recent like any other project.
    project_new(ctx, name=chosen.id, path=str(folder))
    chosen.build(ctx.session)
    return _info(ctx)


@command(
    "project.defaults",
    params=(
        ParamDef("name", "string", required=False, description="A name to resolve a path for."),
        ParamDef(
            "folder",
            "string",
            required=False,
            description="The folder the project goes in, when not the projects folder (the New "
            "dialog's Browse); `path` and `exists` are then answered for it.",
        ),
    ),
    requires_project=False,
    mutates=False,
)
def project_defaults(
    ctx: CommandContext, name: str | None = None, folder: str | None = None
) -> dict[str, Any]:
    """Where a new project would go, and the full path it would occupy.

    **The server resolves the path, because only the server knows it** (the menu spec's "will be
    created at <full path>"). `project_dir` appends `.stokes` and resolves `~`, and `$STOKES_HOME`
    decides the projects folder - three rules the front end would otherwise have to duplicate and
    could then disagree with the command that actually creates the folder.

    **`exists` is answered for the path `name` resolves to**, so the dialog can refuse a collision
    before the user presses Create rather than after. It is computed per call for the same reason
    `project.recent` computes it: the disk changes without us.
    """
    del ctx
    from stokes.model.browse import projects_folder
    from stokes.model.persist import PROJECT_FILE, project_dir

    default = projects_folder()
    reply: dict[str, Any] = {
        "folder": str(default),
        "suffix": ".stokes",
    }
    if name:
        # The same normalisation `project.new` applies to the path the dialog sends
        # (`<folder>/<name>`), so the path shown is the path created, for a chosen folder too.
        # Read-only: nothing is made.
        base = Path(folder.strip()) if folder and folder.strip() else default
        target = project_dir(base / name)
        reply["path"] = str(target)
        reply["exists"] = (target / PROJECT_FILE).is_file()
    return reply


@command("project.recent", params=(), requires_project=False, mutates=False)
def project_recent(ctx: CommandContext) -> dict[str, Any]:
    """The projects to offer on a start screen, most recent first.

    **`exists` is computed here, on every call**, because it is the one thing that can change
    without us: a project moved or deleted on disk. A stored copy goes stale exactly when a start
    screen needs it, and a row that claims to open something it cannot is worse than one saying so.
    """
    del ctx
    from stokes.model.preview import exists as has_preview
    from stokes.model.recent import read_recent

    projects: list[dict[str, Any]] = []
    for entry in read_recent():
        projects.append(
            {
                "name": entry.name,
                "path": entry.path,
                "modified": entry.modified,
                "opened": entry.opened,
                "exists": entry.exists,
                "has_preview": has_preview(Path(entry.path)) if entry.exists else False,
            }
        )
    return {"projects": projects}


@command(
    "project.recent.remove",
    params=(ParamDef("path", "string", description="The project to forget."),),
    requires_project=False,
    # **`mutates=False`**: it removes a LIST ENTRY, not model state. Nothing about the project
    # changes, no node is touched, and there is nothing to undo - a user who removed the wrong row
    # wants to open the project again, not to press Ctrl-Z. Marking it mutating would also put a
    # change on the undo stack for a start screen the user has not left yet.
    mutates=False,
)
def project_recent_remove(ctx: CommandContext, path: str) -> dict[str, Any]:
    """Drop one project from the recent list. **The folder is never touched.**

    A user clearing a row is tidying a list, not deleting their work - so this removes the entry and
    nothing else. That distinction is the whole reason this is a separate command from anything that
    deletes.
    """
    del ctx
    from stokes.model.recent import forget

    remaining = forget(path)
    return {"removed": path, "projects": len(remaining)}


@command(
    "project.browse",
    params=(ParamDef("path", "string", required=False, description="The folder to list."),),
    requires_project=False,
    mutates=False,
)
def project_browse(ctx: CommandContext, path: str | None = None) -> dict[str, Any]:
    """List folders that could hold a project, for the start screen's Browse.

    **The server decides what may be listed.** A browser cannot open a native dialog, so the
    listing is made here and confined to two roots - the user's home and `$STOKES_HOME/projects`
    - with symlinks and hidden names skipped. See `stokes/model/browse.py` for each rule.
    """
    del ctx
    from stokes.model.browse import list_folder

    return list_folder(path)


@command(
    "project.preview.set",
    params=(ParamDef("png_base64", "string", description="A small PNG, base64."),),
    mutates=False,
)
def project_preview_set(ctx: CommandContext, png_base64: str) -> dict[str, Any]:
    """Store a thumbnail for the open project.

    **`mutates=False` deliberately.** The image is not part of the model: not journalled, not an
    undo step, and storing it must not make the project look modified - a user who saved and then
    moved a window has not changed their model. `project.save` writes the project file; this
    writes a file beside it.

    The bytes are validated before anything touches the disk; see `stokes/model/preview.py`.
    """
    project = ctx.session.require_project()
    folder = ctx.session.project_path
    if folder is None:
        raise ValidationError("save the project before adding a preview; it has no folder yet")
    from stokes.model.preview import decode, write

    raw = decode(png_base64)
    written = write(Path(folder), raw)
    del project
    return {"path": str(written), "bytes": len(raw)}
