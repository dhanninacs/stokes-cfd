"""The settings of an imported case: read, override, reset, diff, preview (ADR-0029 4, plan T-308).

An imported case's copy never changes; what the user edits is the node's `overrides` list
(`stokes.cases.overrides`), and what runs is `stokes.compile.of14.external.write(copy, overrides)`.
These commands are the API over that: `case.settings` shows one file as it will be staged,
`case.set` and `case.reset` record and drop edits as single undo steps, `case.diff` shows every
edit as a unified diff, `case.preview` lists or shows the staged files, and `case.boundary` reads
the patch-by-field grid of the initial conditions.

A known physical key (`stokes.cases.keys`) takes a quantity with its unit and is written in SI
(rule 6); every other value is a typed token. `stokes.cases` and `stokes.compile` are imported
inside the functions so `import stokes.api` stays within its start-up budget.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from stokes.api.commands.case import CASE_REF, _case_node, _project_folder
from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.model import SetParam

if TYPE_CHECKING:
    from stokes.compile.of14.external import Override
    from stokes.model import Node

FILE_REF = ParamDef(
    "file", "string", description="A file of the case, such as system/controlDict or 0/U."
)
#: The largest file `case.preview` returns as text.
MAX_PREVIEW_BYTES = 1024 * 1024


def _items(target: Node) -> list[dict[str, Any]]:  # Any: JSON
    value = target.params.get("overrides") or []
    return [item for item in value if isinstance(item, dict)]


def _parsed(target: Node) -> list[Override]:
    from stokes.cases.overrides import parse_list

    return parse_list(_items(target))


def _read(ctx: CommandContext, target: Node, file: str, limit: int | None = None) -> bytes:
    from stokes.cases.overrides import check_file
    from stokes.cases.store import read_source
    from stokes.compile.foam_read import MAX_BYTES

    return read_source(_project_folder(ctx), target.id, check_file(file), limit or MAX_BYTES)


def _staged(ctx: CommandContext, target: Node, file: str, items: Sequence[Override]) -> bytes:
    """One file as it will be staged: the copy with the overrides for it applied."""
    from stokes.compile.of14.external import apply

    return apply(_read(ctx, target, file), [item for item in items if item.file == file])


@command("case.settings", params=(CASE_REF, FILE_REF), mutates=False)
def case_settings(ctx: CommandContext, node: str, file: str) -> dict[str, Any]:  # Any: JSON
    """One file of an imported case as it will be staged, as rows a settings editor shows.

    Each row is `{path, kind: entry | block | directive, text, truncated, overridden}`; an entry
    also carries `dimension` and `unit` (SI) when it is a known physical key, and `original` (the
    copy's own text) when it is overridden. Codes: `case.file_missing`, `case.file_too_large`,
    `foam.read.syntax`.
    """
    from stokes.cases.keys import dimension_of
    from stokes.compile.of14.external import MAX_ROWS, apply, entry_text, rows
    from stokes.units import DIMENSIONS

    target = _case_node(ctx, node)
    original = _read(ctx, target, file)
    mine = [item for item in _parsed(target) if item.file == file]
    staged = apply(original, mine)
    edited = {item.path for item in mine}
    out = rows(staged)
    for row in out:
        path = tuple(row["path"])
        row["overridden"] = row["kind"] == "entry" and path in edited
        dimension = dimension_of(file, path) if row["kind"] == "entry" else None
        if dimension is not None:
            row["dimension"] = dimension
            row["unit"] = DIMENSIONS[dimension].si_unit
        if row["overridden"]:
            row["original"] = entry_text(original, path)
    return {
        "file": file,
        "rows": out,
        "truncated": len(out) >= MAX_ROWS,
        "overrides": [item for item in _items(target) if item.get("file") == file],
    }


@command(
    "case.set",
    params=(
        CASE_REF,
        FILE_REF,
        ParamDef("path", "array", description='The key path, such as ["PISO", "nCorrectors"].'),
        ParamDef(
            "value",
            "any",
            description="A number, boolean, word, list, {tokens: [...]}, or for a known physical "
            "key a quantity such as '0.5 [s]'.",
        ),
    ),
)
def case_set(
    ctx: CommandContext,
    node: str,
    file: str,
    path: list[Any],  # Any: JSON from the caller, checked by stored()
    value: Any,  # Any: JSON from the caller, checked by stored()
) -> dict[str, Any]:  # Any: JSON
    """Record one edit to an imported case's settings, as one undo step.

    Setting a key again replaces its earlier edit. The edit is applied to the file before it is
    recorded, so a value the file cannot take is refused here and never reaches a run. Codes:
    `case.override_value`, `case.override_path`, `case.override_unit`, `case.override_block`,
    `case.override_file`, `case.file_missing`.
    """
    from stokes.cases.overrides import parse_list, stored, upsert
    from stokes.compile.of14.external import entry_text

    project = ctx.session.require_project()
    target = _case_node(ctx, node)
    item = stored(file, path, value)
    items = upsert(_items(target), item)
    staged = _staged(ctx, target, item["file"], parse_list(items))
    cmd = SetParam(target.id, "overrides", items)
    ctx.session.undo.push(cmd, project)
    ctx.session.emit("node.changed", node_id=target.id, path=target.path, param="overrides")
    return {
        "file": item["file"],
        "path": item["path"],
        "value": item["value"],
        "text": entry_text(staged, item["path"]),
        "overrides": len(items),
    }


@command(
    "case.reset",
    params=(
        CASE_REF,
        FILE_REF,
        ParamDef(
            "path",
            "array",
            required=False,
            description="The key path to reset; without it every edit to the file is dropped.",
        ),
    ),
)
def case_reset(
    ctx: CommandContext,
    node: str,
    file: str,
    path: list[Any] | None = None,  # Any: JSON from the caller
) -> dict[str, Any]:  # Any: JSON
    """Drop edits to an imported case's file, as one undo step: the copy's own text returns.

    Nothing to drop is not an error: the reply says `removed: 0` and no undo step is made.
    """
    from stokes.cases.overrides import check_file, check_path, remove

    project = ctx.session.require_project()
    target = _case_node(ctx, node)
    keys = list(check_path(path)) if path is not None else None
    before = _items(target)
    after = remove(before, check_file(file), keys)
    removed = len(before) - len(after)
    if removed:
        ctx.session.undo.push(SetParam(target.id, "overrides", after), project)
        ctx.session.emit("node.changed", node_id=target.id, path=target.path, param="overrides")
    return {"file": file, "path": keys, "removed": removed, "overrides": len(after)}


@command("case.diff", params=(CASE_REF,), mutates=False)
def case_diff(ctx: CommandContext, node: str) -> dict[str, Any]:  # Any: JSON
    """Every edited file of an imported case as a unified diff against the copy.

    `{files: [{file, overrides, diff}]}`, in the order the files were first edited.
    """
    import difflib

    from stokes.compile.of14.external import apply

    target = _case_node(ctx, node)
    items = _parsed(target)
    files: list[str] = []
    for item in items:
        if item.file not in files:
            files.append(item.file)
    out: list[dict[str, Any]] = []
    for file in files:
        original = _read(ctx, target, file)
        staged = apply(original, [item for item in items if item.file == file])
        lines = difflib.unified_diff(
            original.decode("latin-1").splitlines(keepends=True),
            staged.decode("latin-1").splitlines(keepends=True),
            fromfile=f"a/{file}",
            tofile=f"b/{file}",
        )
        out.append(
            {
                "file": file,
                "overrides": sum(1 for item in items if item.file == file),
                "diff": "".join(lines),
            }
        )
    return {"files": out}


@command(
    "case.preview",
    params=(
        CASE_REF,
        ParamDef(
            "file",
            "string",
            required=False,
            description="A file to return as staged text; without it every staged file is listed.",
        ),
    ),
    mutates=False,
)
def case_preview(
    ctx: CommandContext, node: str, file: str | None = None
) -> dict[str, Any]:  # Any: JSON
    """The case as it will be staged: every file, or one file's text.

    Without `file`: `{writer, files: [{path, size, overridden}]}` for the copy's `source` area.
    With `file`: `{file, text, overridden}`, refused with `case.file_too_large` past 1 MiB.
    """
    from stokes.cases.store import source_rows
    from stokes.compile.of14.external import WRITER_VERSION, apply

    target = _case_node(ctx, node)
    items = _parsed(target)
    edited = {item.file for item in items}
    if file is not None:
        data = _read(ctx, target, file, MAX_PREVIEW_BYTES)
        staged = apply(data, [item for item in items if item.file == file])
        return {"file": file, "text": staged.decode("latin-1"), "overridden": file in edited}
    rows = source_rows(_project_folder(ctx), target.id)
    if rows is None:
        raise ValidationError(
            f"the copy of {target.path} is not in the project",
            code="case.source_missing",
            details={"node": target.path},
        )
    return {
        "writer": WRITER_VERSION,
        "files": [
            {"path": row["path"], "size": row.get("size"), "overridden": row["path"] in edited}
            for row in rows
        ],
    }


def _initial_folder(rows: Sequence[dict[str, Any]], region: str | None) -> str | None:  # Any: JSON
    """`0` when the copy has initial fields there, else `0.orig` (Allrun copies it to `0`)."""
    for folder in ("0", "0.orig"):
        prefix = f"{folder}/{region}/" if region else f"{folder}/"
        if any(
            row["path"].startswith(prefix) and "/" not in row["path"][len(prefix) :] for row in rows
        ):
            return folder
    return None


@command(
    "case.boundary",
    params=(
        CASE_REF,
        ParamDef(
            "region",
            "string",
            required=False,
            description="A region of a multi-region case; its fields are under 0/<region>.",
        ),
    ),
    mutates=False,
)
def case_boundary(
    ctx: CommandContext, node: str, region: str | None = None
) -> dict[str, Any]:  # Any: JSON
    """The initial conditions as a patch-by-field grid, with the edits applied.

    `{folder, fields, patches, grid: {field: {patch: {type, value?}}}, unread}`. A cell is edited
    with `case.set` on `<folder>/<field>` and the path `["boundaryField", <patch>, <key>]`.
    Patch names are as the files write them, so a regular-expression patch (`".*"`) is its own
    column. Fields that cannot be read are listed in `unread` with the reason.
    """
    from stokes.cases.store import source_rows
    from stokes.compile.of14.external import check_key, entry_text, rows
    from stokes.errors import StokesError

    target = _case_node(ctx, node)
    if region is not None:
        check_key(region)
    found = source_rows(_project_folder(ctx), target.id) or []
    folder = _initial_folder(found, region)
    if folder is None:
        return {"folder": None, "fields": [], "patches": [], "grid": {}, "unread": []}
    prefix = f"{folder}/{region}/" if region else f"{folder}/"
    names = sorted(
        row["path"][len(prefix) :]
        for row in found
        if row["path"].startswith(prefix) and "/" not in row["path"][len(prefix) :]
    )
    items = _parsed(target)
    grid: dict[str, dict[str, dict[str, Any]]] = {}
    patches: list[str] = []
    unread: list[dict[str, str]] = []
    for name in names:
        file = prefix + name
        try:
            staged = _staged(ctx, target, file, items)
            cells: dict[str, dict[str, Any]] = {}
            for row in rows(staged):
                keys = row["path"]
                if row["kind"] != "block" or len(keys) != 2 or keys[0] != "boundaryField":
                    continue
                patch = str(keys[1])
                cell: dict[str, Any] = {
                    "type": entry_text(staged, ("boundaryField", patch, "type"))
                }
                value = entry_text(staged, ("boundaryField", patch, "value"))
                if value is not None:
                    cell["value"] = value
                cells[patch] = cell
                if patch not in patches:
                    patches.append(patch)
        except StokesError as exc:  # an unreadable field is listed, not fatal to the grid
            unread.append({"field": name, "why": exc.message})
            continue
        grid[name] = cells
    return {
        "folder": prefix.rstrip("/"),
        "fields": list(grid),
        "patches": patches,
        "grid": grid,
        "unread": unread,
    }
