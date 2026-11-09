"""The ``tutorial.*`` and ``case.*`` commands: the tutorial catalogue and imported cases (ADR-0029).

The ``tutorial.*`` commands need no project: the catalogue is a fact about this machine. The
folder the user chooses is a user setting (`stokes.cases.roots`), so `tutorial.set_root` is
journaled like `host.add` - it changes state outside the project - while `tutorial.roots` and
`tutorial.catalogue` only read.

The ``case.*`` commands (T-307) add, read and remove `foam_case` nodes. `case.import` copies the
case into the project (`stokes.cases.store`) and judges it with **the agent's own rules** - the
`external` lint profile and the external flag allow-list - which this module imports from
`stokes_agent` because `stokes.cases` may not.

The install's own tutorial folder and what it can run come from the local host's probe record
(`HostRegistry.inventory`), which is cached per session; `stokes.run`, `stokes.cases` and
`stokes_agent` are imported inside the functions so `import stokes.api` stays within its start-up
budget.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.model import AddNode, Command, Composite, RemoveNode

if TYPE_CHECKING:
    from stokes.cases.importer import Source
    from stokes.cases.recipe import ArgvCheck
    from stokes.cases.store import Lint
    from stokes.model import Node


def _install(ctx: CommandContext) -> dict[str, Any] | None:  # Any: JSON probe record
    """The local install's inventory, or None when it cannot be probed."""
    from stokes.run.hosts import HostRegistry

    return HostRegistry.of(ctx.session).inventory("local")


#: How long a classified catalogue is reused, per folder and install. A tutorial tree does not
#: change under a running session; `tutorial.set_root` is the one thing that changes the folder.
CATALOGUE_TTL_S = 300.0
_catalogue_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


def _classified_cached(folder: Path, inventory: Any) -> list[dict[str, Any]]:  # Any: Inventory
    """`classified(folder, inventory)`, remembered for `CATALOGUE_TTL_S`.

    **Measured 2026-11-01 (py-spy on the server):** the start screen's tutorials card asks for the
    catalogue as soon as a client connects, and classifying the 260 cases of an OpenFOAM-14 tree
    is tens of seconds of file stats - run on the event loop by the dispatcher, so the tree read,
    `project.info` and every other request waited behind it and the window sat on the Start
    screen for half a minute with a project open. The walk is the same every time; one answer per
    folder and install is kept and the filters (`category`, `text`) are applied to the copy.
    """
    import time

    from stokes.cases.catalogue import classified

    key = (str(folder), "" if inventory is None else repr(sorted(inventory.__dict__.items())))
    now = time.monotonic()
    held = _catalogue_cache.get(key)
    if held is not None and now - held[0] < CATALOGUE_TTL_S:
        return held[1]
    everything = classified(folder, inventory)
    _catalogue_cache[key] = (now, everything)
    return everything


def _roots(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """The user's folder, the install's folder, and which one the catalogue uses."""
    from stokes.cases.roots import read_root

    user = read_root()
    inventory = _install(ctx)
    found = inventory.get("tutorials_root") if inventory else None
    install = found if isinstance(found, str) and found else None
    rows = [
        {"path": path, "source": source, "exists": Path(path).is_dir()}
        for path, source in ((user, "user"), (install, "install"))
        if path
    ]
    active = next((row["path"] for row in rows if row["exists"]), None)
    return {"active": active, "user": user, "install": install, "roots": rows}


@command("tutorial.roots", params=(), requires_project=False, mutates=False)
def tutorial_roots(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """The tutorial folders: the one the user set, the install's own, and the active one.

    The user's folder wins when it still exists; otherwise the install's tutorials folder from the
    host probe is used. `active` is None when neither is available.
    """
    return _roots(ctx)


@command(
    "tutorial.set_root",
    params=(
        ParamDef(
            "path",
            "string",
            nullable=True,
            description="An absolute folder holding OpenFOAM cases; null to forget it.",
        ),
    ),
    requires_project=False,
)
def tutorial_set_root(ctx: CommandContext, path: str | None) -> dict[str, Any]:  # Any: JSON
    """Choose the tutorial catalogue folder, stored in the user settings, never in the project.

    The folder must exist, be a directory and hold at least one case (`*/system/controlDict`);
    each refusal has its own code (`tutorial.root_missing`, `tutorial.root_not_folder`,
    `tutorial.root_empty`, `tutorial.root_relative`). Null clears the choice.
    """
    from stokes.cases.roots import validate_root, write_root

    write_root(str(validate_root(path)) if path is not None else None)
    return _roots(ctx)


@command(
    "tutorial.catalogue",
    params=(
        ParamDef(
            "root",
            "string",
            required=False,
            description="A folder to list instead of the active tutorial folder.",
        ),
        ParamDef(
            "category",
            "string",
            required=False,
            description="Only cases in this top-level folder, such as incompressibleFluid.",
        ),
        ParamDef(
            "text",
            "string",
            required=False,
            description="Only cases whose id, application, solver or status contains this text.",
        ),
    ),
    requires_project=False,
    mutates=False,
)
def tutorial_catalogue(
    ctx: CommandContext,
    root: str | None = None,
    category: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Every case under the tutorial folder, with whether this install can run it and why not.

    Read only: nothing under the folder is written or run. Each case is
    `{id, category, path, application, solver, regions, recipe_kind, status, why}` with `status`
    one of `ready`, `missing_module`, `missing_utility`, `missing_library`, `coded`,
    `needs_recipe`, `unverified`. When the install cannot be probed every case that is not
    `coded` is `unverified`, and `probed` is false.
    """
    from stokes.cases.catalogue import Inventory, counts, select
    from stokes.cases.roots import validate_root

    inventory = Inventory.from_record(_install(ctx))
    if root is None:
        root = _roots(ctx)["active"]
        if root is None:
            raise ValidationError(
                "no tutorial folder: set one with tutorial.set_root, or probe an install that "
                "has tutorials",
                code="tutorial.no_root",
            )
    folder = validate_root(root)
    everything = _classified_cached(folder, inventory)
    entries = select(everything, category=category, text=text)
    return {
        "root": str(folder),
        "probed": inventory is not None,
        "totals": counts(everything),
        "counts": counts(entries),
        "cases": entries,
    }


# ------------------------------------------------------------------------------- case.* (T-307)

CASE_REF = ParamDef("node", "string", description="A foam_case node: id or path.")


def _checks(ctx: CommandContext) -> tuple[Lint, ArgvCheck | None]:
    """The external lint and argv check, bound to the local install's probe (ADR-0029 5, 6).

    **The agent's own code**, imported here because `stokes.cases` may not import the agent: the
    copy is judged by the rules the agent will apply when the case is staged. With no probe the
    lint runs against an empty install (stricter: every `libs`, `#includeEtc` and `#includeFunc`
    is refused) and the recipe is left unchecked.
    """
    from stokes_agent.flags_external import ExternalFlagRefused, check_external_argv
    from stokes_agent.lint_external import ProbedInstall, lint_file_external

    inventory = _install(ctx)
    record = inventory or {}

    def names(key: str) -> list[str]:
        value = record.get(key)
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    install = ProbedInstall.from_lists(
        solver_modules=names("solver_modules"),
        utilities=names("utilities"),
        libraries=names("libraries"),
        etc_functions=names("etc_functions"),
        etc_files=names("etc_case_dicts"),
    )

    def lint(rel: str, path: Path) -> list[dict[str, Any]]:  # Any: JSON
        return [finding.to_json() for finding in lint_file_external(rel, path, install)]

    def check(argv: Sequence[str]) -> str | None:
        try:
            check_external_argv(argv, install)
        except ExternalFlagRefused as exc:
            return exc.message
        return None

    return lint, (check if inventory is not None else None)


def _case_node(ctx: CommandContext, ref: str) -> Node:
    from stokes.cases.model import CASE_TYPE

    node = ctx.session.require_project().get(ref)
    if node.type != CASE_TYPE:
        raise ValidationError(
            f"{node.path} is a {node.type}, not an imported case",
            code="case.not_a_case",
            details={"node": node.path},
        )
    return node


def _project_folder(ctx: CommandContext) -> Path:
    if ctx.session.project_path is None:
        raise ValidationError("the project has no folder", code="case.no_project_folder")
    return Path(ctx.session.project_path)


def _source(ctx: CommandContext, tutorial: str | None, folder: str | None) -> Source:
    from stokes.cases.importer import folder_source, tutorial_source

    if (tutorial is None) == (folder is None):
        raise ValidationError("give exactly one of tutorial and folder", code="case.source_invalid")
    if folder is not None:
        return folder_source(folder)
    root = _roots(ctx)["active"]
    if root is None:
        raise ValidationError(
            "no tutorial folder: set one with tutorial.set_root, or probe an install that has "
            "tutorials",
            code="tutorial.no_root",
        )
    return tutorial_source(Path(root), str(tutorial))


def _copy_in(
    ctx: CommandContext,
    home: Path,
    node_id: str,
    tutorial: str | None,
    folder: str | None,
    snapshot: str | None,
) -> dict[str, Any]:  # Any: JSON
    """Read the source, judge it and copy it in: `{status, source, derived, snapshot}`, or the
    refused reply `{node_id: None, status: refused, refusals, ...}`."""
    from stokes.cases.importer import import_case
    from stokes.cases.store import case_dir, remove_tree

    source = _source(ctx, tutorial, folder)
    lint, check = _checks(ctx)
    done = import_case(home, node_id, source, lint, check)
    recipe = done.recipe.to_json() if done.recipe else {}
    if done.status == "refused":
        return {
            "node_id": None,
            "path": None,
            "status": "refused",
            "snapshot": None,
            "recipe": recipe,
            "refusals": done.refusals,
        }
    if snapshot is not None and done.snapshot != snapshot:
        remove_tree(case_dir(home, node_id))
        raise ValidationError(
            "the case differs from the one the journal imported",
            code="case.snapshot_mismatch",
            details={"expected": snapshot, "found": done.snapshot},
        )
    return {
        "status": done.status,
        "source": source.to_json(),
        "derived": {"recipe": recipe, "solver": done.solver, "regions": done.regions},
        "snapshot": done.snapshot,
    }


@command(
    "case.import",
    params=(
        ParamDef(
            "tutorial",
            "string",
            required=False,
            description="A tutorial id, such as "
            "incompressibleFluid/cavity, under the active tutorial folder.",
        ),
        ParamDef("folder", "string", required=False, description="An absolute case folder."),
        ParamDef(
            "name",
            "string",
            required=False,
            description="Tree name; default from the case folder's name.",
        ),
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
        ParamDef(
            "snapshot",
            "string",
            required=False,
            description="The expected snapshot "
            "hash (journals); a copy in the project with it is reused.",
        ),
    ),
)
def case_import(
    ctx: CommandContext,
    tutorial: str | None = None,
    folder: str | None = None,
    name: str | None = None,
    node_id: str | None = None,
    snapshot: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Import an OpenFOAM 14 case as a `foam_case` node under /cases, as one undo step.

    The case, and the `resources/` files its Allrun copies in, are copied into
    `<project>/cases/<node id>/source/`, content-addressed and read-only; the source is only read.
    Every copied file must pass the agent's `external` lint profile: a refused case returns
    `status: refused` with the file, line and reason of each finding, and adds and stages nothing.
    Otherwise `status` is `ready`, `needs_recipe` (an Allrun line not understood or a step the
    agent would refuse) or `unverified` (the install was not probed). Codes:
    `case.source_invalid`, `case.source_missing`, `case.source_escape`, `case.source_relative`,
    `case.source_in_project`, `case.snapshot_mismatch`, `tutorial.no_root`.
    """
    reply, journal = import_into(ctx, tutorial, folder, name, node_id, snapshot)
    if journal:
        ctx.record(**journal)
    return reply


def import_into(
    ctx: CommandContext,
    tutorial: str | None,
    folder: str | None,
    name: str | None,
    node_id: str | None,
    snapshot: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:  # Any: JSON
    """`case.import`'s work: the reply, and the values a journal needs to replay it exactly.

    **Split from the handler so `tutorial.open` can import without recording `case.import`'s
    fields under its own command**: the caller records what its own replay needs. A refused import
    returns an empty journal, because nothing was added.
    """
    from stokes.cases.importer import node_name, reuse, status_of
    from stokes.cases.model import CASE_TYPE, CASES_GROUP, CASES_PATH, is_snapshot
    from stokes.cases.store import case_dir, remove_tree
    from stokes.model.ids import is_id, new_id, stable_id

    project = ctx.session.require_project()
    home = _project_folder(ctx)
    if node_id is not None and not is_id(node_id):
        raise ValidationError(f"{node_id!r} is not a node id", code="case.source_invalid")
    if snapshot is not None and not is_snapshot(snapshot):
        raise ValidationError("the snapshot must be a sha256 hash", code="case.source_invalid")
    chosen = node_id or new_id()
    if project.find(chosen) is not None:
        raise ValidationError(f"node id {chosen} is taken", code="case.source_invalid")
    group = project.find(CASES_PATH)
    taken = {child.name for child in group.children} if group is not None else set()
    if name is not None and name in taken:
        raise ValidationError(
            f"{CASES_PATH} already has a case named {name!r}",
            code="case.name_taken",
            details={"name": name},
        )
    # **A journal replay reuses the copy the record names** when the project holds it, so it
    # needs neither the source nor this machine's install (plan T-307).
    derived = reuse(home, chosen, snapshot) if snapshot else None
    created = derived is None
    if derived is None:
        fresh = _copy_in(ctx, home, chosen, tutorial, folder, snapshot)
        if fresh["status"] == "refused":
            return fresh, {}
        origin, derived, snapshot = fresh["source"], fresh["derived"], fresh["snapshot"]
    elif tutorial:
        origin = {"kind": "install", "tutorial": tutorial}
    else:
        origin = {"kind": "folder", "path": str(folder)}
    folder_name = PurePosixPath(str(origin.get("tutorial") or origin.get("path") or "")).name
    name = name or node_name(PureWindowsPath(folder_name).name, taken)
    steps: list[Command] = []
    if group is None:
        steps.append(AddNode("/", "group", CASES_GROUP, node_id=stable_id("g", CASES_PATH)))
    params = {
        "source": origin,
        "snapshot": snapshot,
        "dialect": "of14",
        "solver": derived.get("solver"),
        "recipe": derived["recipe"],
        "overrides": [],
    }
    steps.append(AddNode(CASES_PATH, CASE_TYPE, name, params, node_id=chosen))
    try:
        ctx.session.undo.push(Composite(label=f"Import case {name}", commands=steps), project)
    except Exception:
        if created:  # a copy nothing refers to would stay in the project
            remove_tree(case_dir(home, chosen))
        raise
    # The source as resolved, so a replay names the folder this import read.
    named = (
        {"tutorial": origin.get("tutorial")}
        if origin.get("kind") == "install"
        else {"folder": origin.get("path")}
    )
    journal: dict[str, Any] = {"node_id": chosen, "name": name, "snapshot": snapshot, **named}
    if group is None:
        ctx.session.emit("node.added", node_id=stable_id("g", CASES_PATH), path=CASES_PATH)
    path = f"{CASES_PATH}/{name}"
    ctx.session.emit("node.added", node_id=chosen, path=path)
    reply = {
        "node_id": chosen,
        "path": path,
        "status": status_of(derived["recipe"]),
        "snapshot": snapshot,
        "recipe": derived["recipe"],
        "refusals": [],
    }
    return reply, journal


@command("case.get", params=(CASE_REF,), mutates=False)
def case_get(ctx: CommandContext, node: str) -> dict[str, Any]:  # Any: JSON
    """An imported case: source, snapshot, solver, regions, recipe, overrides and status.

    `status` is `missing` when the copy is no longer in the project (a project whose `cases/`
    folder was deleted), else the recipe's: `ready`, `needs_recipe` or `unverified`.
    """
    from stokes.cases.importer import status_of
    from stokes.cases.store import has_snapshot

    target = _case_node(ctx, node)
    params = target.params
    recipe = params.get("recipe") or {}
    solver = params.get("solver")
    stored = has_snapshot(_project_folder(ctx), target.id, str(params.get("snapshot") or ""))
    return {
        "id": target.id,
        "path": target.path,
        "source": params.get("source"),
        "snapshot": params.get("snapshot"),
        "dialect": params.get("dialect"),
        "solver": solver,
        "regions": sorted(solver) if isinstance(solver, dict) else None,
        "recipe": recipe,
        "overrides": params.get("overrides") or [],
        "status": status_of(recipe) if stored else "missing",
    }


@command("case.files", params=(CASE_REF,), mutates=False)
def case_files(ctx: CommandContext, node: str) -> list[dict[str, Any]]:  # Any: JSON
    """The files of an imported case's copy: `{path, area, kind, size, parsed}`.

    `area` is `source` (staged when the case runs) or `scripts` (Allrun and friends, shown but
    never staged); `kind` is `dict`, `field`, `mesh`, `binary` or `script`. Refused with
    `case.source_missing` when the copy is not in the project.
    """
    from stokes.cases.store import list_files

    target = _case_node(ctx, node)
    found = list_files(_project_folder(ctx), target.id)
    if found is None:
        raise ValidationError(
            f"the copy of {target.path} is not in the project",
            code="case.source_missing",
            details={"node": target.path},
        )
    return found


@command("case.remove", params=(CASE_REF,))
def case_remove(ctx: CommandContext, node: str) -> dict[str, Any]:  # Any: JSON
    """Remove an imported case from the tree, as one undo step.

    The read-only copy stays in the project folder so undo brings the case back whole.
    """
    project = ctx.session.require_project()
    target = _case_node(ctx, node)
    cmd = RemoveNode(target.id)
    result = ctx.session.undo.push(cmd, project)
    ctx.session.emit("node.removed", node_id=cmd.node_id, path=result["path"], parent=cmd.parent_id)
    return dict(result)
