"""Save and load ``<name>.stokes/project.json`` (ADR-0006).

Writes are atomic (temp file then replace). Loads are size-limited and go
through schema migrations: a file is upgraded one version at a time to
:data:`SCHEMA_VERSION`, which every save writes, and a newer file is refused.
Paths are resolved and must be directories the caller may write; the project
directory is the only place this module writes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from stokes.errors import PersistenceError
from stokes.model.project import Project
from stokes.model.schema import NODE_TYPE_ALIASES, NodeTypeRegistry

SCHEMA_VERSION = 3
PROJECT_FILE = "project.json"
MAX_PROJECT_BYTES = 64 * 1024 * 1024
SUBDIRS = ("geometry", "runs", "cache")

Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _from_version_1(data: dict[str, Any]) -> dict[str, Any]:
    """Version 1 to 2 (ADR-0021 decision 2.3): renamed node types, and named selections as tags.

    Every node's type goes through :data:`~stokes.model.schema.NODE_TYPE_ALIASES`. The group
    ``named_selections`` directly under ``/geometry`` becomes ``tags``. When the project has a
    ``tags`` group already, the old group's children move into it, after its own children, and a
    name taken there gets ``_2``, ``_3`` ...; a ``tags`` node that is not a group leaves the old
    group as it is. Ids, parameters and everything else are kept. The group names are written out
    here, not taken from the geometry module, because this step must go on meaning what version 2
    meant.
    """
    root = data.get("root")
    if not isinstance(root, dict):
        return data  # Project.from_json reports the malformed file
    pending: list[Any] = [root]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if isinstance(node.get("type"), str):
                node["type"] = _current_type(node["type"])
            pending.extend(_children(node))
    geometry = _child(root, "geometry")
    old = _child(geometry, "named_selections") if geometry is not None else None
    if geometry is None or old is None or old.get("type") != "group":
        return data
    tags = _child(geometry, "tags")
    if tags is None:
        old["name"] = "tags"
        return data
    if tags.get("type") != "group":
        return data
    merged = tags.setdefault("children", [])
    if not isinstance(merged, list):
        return data
    taken = {child.get("name") for child in merged if isinstance(child, dict)}
    for child in _children(old):
        if isinstance(child, dict) and isinstance(child.get("name"), str):
            base = name = child["name"]
            suffix = 2
            while name in taken:
                name = f"{base}_{suffix}"
                suffix += 1
            child["name"] = name
            taken.add(name)
        merged.append(child)
    geometry["children"] = [child for child in _children(geometry) if child is not old]
    return data


def _from_version_2(data: dict[str, Any]) -> dict[str, Any]:
    """Version 2 to 3 (brief M-504): a stored layer count of ``0`` becomes the empty state.

    ``part.layers`` existed briefly (added 2026-10-18, withdrawn the same day as a dead field:
    the snappy driver still wrote an empty ``layers {}`` block, so the control changed nothing)
    with the default ``0`` meaning **INHERIT**. The 2026-10-19 1130 semantics give ``0`` the
    meaning **NONE**, so a project saved in that window - where ``0`` may be stored as the
    user's own word for "inherit", or as the parameter's materialised default - would load with
    every layer count silently OFF under the new reading.

    The migration drops ``layers`` when it is the integer ``0`` on a ``part`` or a ``boundary``:
    the empty state (absent) is read as inherit by
    :func:`stokes.mesh.layers.effective_layers`, which is what ``0`` meant when it was written.
    A count from 1 to 10 keeps the meaning it had. A version-3 file written after this change
    keeps a stored ``0`` as an explicit "none", which is exactly the distinction the load-time
    version key makes computable.
    """
    root = data.get("root")
    if not isinstance(root, dict):
        return data  # Project.from_json reports the malformed file
    pending: list[Any] = [root]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        if node.get("type") in ("part", "boundary"):
            params = node.get("params")
            if isinstance(params, dict):
                stored = params.get("layers")
                # JSON has no separate bool that is also 0, but `True` is an int to Python, so
                # the check refuses one: a stored boolean was never a layer count.
                if isinstance(stored, int) and not isinstance(stored, bool) and stored == 0:
                    del params["layers"]
        pending.extend(_children(node))
    return data


def _current_type(node_type: str) -> str:
    """``node_type`` through the alias table, following a type renamed again later."""
    seen = {node_type}
    while node_type in NODE_TYPE_ALIASES:
        node_type = NODE_TYPE_ALIASES[node_type]
        if node_type in seen:  # a cycle, which only a broken table has: stop where it closes
            break
        seen.add(node_type)
    return node_type


def _children(node: dict[str, Any]) -> list[Any]:
    children = node.get("children")
    return children if isinstance(children, list) else []


def _child(node: dict[str, Any], name: str) -> dict[str, Any] | None:
    for child in _children(node):
        if isinstance(child, dict) and child.get("name") == name:
            return child
    return None


MIGRATIONS: dict[int, Migration] = {1: _from_version_1, 2: _from_version_2}
"""Maps a schema version to the function that upgrades data to the next version."""


def project_dir(path: str | Path) -> Path:
    """Normalise a user path to the project directory (``.stokes`` suffix added when absent)."""
    p = Path(path).expanduser()
    if p.suffix != ".stokes":
        p = p.with_name(p.name + ".stokes")
    return p.resolve()


def _keep_older_file(target: Path) -> None:
    """Keep a ``project.json`` of an older schema as ``project.v<N>.json`` before a save replaces
    it.

    A migration is one way (an older Stokes refuses the newer schema), so the first save over an old
    file leaves a copy of it beside the new one. An existing copy is never replaced, a current file
    needs none, and a file whose version cannot be read is left to the load that reports it. The
    version is read from the head of the file, where :meth:`Project.to_json` writes it first.
    """
    import re
    import shutil

    try:
        with target.open("rb") as handle:
            head = handle.read(256)
    except OSError:
        return
    found = re.search(rb'"schema_version"\s*:\s*(\d+)', head)
    if found is None or int(found.group(1)) >= SCHEMA_VERSION:
        return
    keep = target.with_name(f"project.v{int(found.group(1))}.json")
    if keep.exists():
        return
    try:
        shutil.copy2(target, keep)
    except OSError as exc:
        raise PersistenceError(
            f"cannot keep {target.name} as {keep.name} before saving: {exc}"
        ) from exc


def save_project(project: Project, path: str | Path) -> Path:
    """Write the project; returns the project directory."""
    folder = project_dir(path)
    folder.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (folder / sub).mkdir(exist_ok=True)
    target = folder / PROJECT_FILE
    _keep_older_file(target)
    _write_atomic(target, project)
    return folder


def _write_atomic(target: Path, project: Project) -> None:
    """Write ``project`` as JSON to ``target`` through a temporary file and a rename."""
    import tempfile  # lazy: pulls in shutil, bz2 and lzma, which cost ~250 ms at start-up here

    text = json.dumps(project.to_json(), indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".project-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        _replace_with_retry(Path(tmp_name), target)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise PersistenceError(f"cannot write {target}: {exc}") from exc


# ---------------------------------------------------------------- the recovery copy (autosave)

#: Unsaved work, written beside the project file and never over it. A Save removes it, and so
#: does a Close that discards the changes. A crash or a server restart leaves it for the next open.
RECOVERY_FILE = "recovery.json"


def save_recovery(project: Project, path: str | Path) -> Path:
    """Write the unsaved project to the folder's recovery copy; the project file is untouched."""
    folder = project_dir(path)
    if not (folder / PROJECT_FILE).is_file():
        raise PersistenceError(f"no project at {folder} to keep a recovery copy for")
    target = folder / RECOVERY_FILE
    _write_atomic(target, project)
    return target


def recovery_time(path: str | Path) -> float | None:
    """When the recovery copy was written (epoch seconds), if it is newer than the project file.

    A copy older than the file is work that a later save already holds, so there is nothing to
    offer; it is left for `discard_recovery` or the next autosave to replace.
    """
    folder = project_dir(path)
    try:
        written = (folder / RECOVERY_FILE).stat().st_mtime
        saved = (folder / PROJECT_FILE).stat().st_mtime
    except OSError:
        return None
    return written if written > saved else None


def load_recovery(path: str | Path, node_registry: NodeTypeRegistry | None = None) -> Project:
    """The project as the recovery copy holds it, read and migrated like the project file."""
    return _load_file(project_dir(path) / RECOVERY_FILE, node_registry)


def discard_recovery(path: str | Path) -> bool:
    """Remove the recovery copy; True when there was one."""
    target = project_dir(path) / RECOVERY_FILE
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PersistenceError(f"cannot remove {target}: {exc}") from exc
    return True


_REPLACE_ATTEMPTS = 6
"""Tries for the final atomic rename; see :func:`_replace_with_retry`."""


def _replace_with_retry(source: Path, target: Path) -> None:
    """``source.replace(target)``, retrying a transient Windows ``PermissionError``.

    On Windows ``os.replace`` fails with ``PermissionError`` when anything holds the target open,
    even for a moment: an antivirus scanner, the search indexer or a backup agent opening the file
    right after the previous save is enough. The write is already atomic — a temp file and a
    rename — so the whole value of that pattern is that a reader never sees half a file; retrying
    the rename keeps that promise instead of failing a save the user asked for.

    This was not hypothetical. ``tests/test_cli.py::test_cli_boundary_and_region_verbs`` failed
    about one run in six with ``cannot write …\\project.json``, and only ever inside a full-suite
    run, where the machine is busiest (T-094, 2026-10-06). The failure was a bare
    ``assert code == 0``,
    so the reason was invisible until the assertion was made to say what the CLI answered.
    """
    import time  # lazy, as tempfile above: only a retrying save pays for it

    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            # Back off a little: a scanner holding the file releases it in milliseconds, and the
            # last attempt raises the real error with the path the caller needs.
            time.sleep(0.02 * (attempt + 1))


def load_project(path: str | Path, node_registry: NodeTypeRegistry | None = None) -> Project:
    return _load_file(project_dir(path) / PROJECT_FILE, node_registry)


def _load_file(target: Path, node_registry: NodeTypeRegistry | None) -> Project:
    if not target.is_file():
        raise PersistenceError(f"no project at {target.parent} (missing {target.name})")
    size = target.stat().st_size
    if size > MAX_PROJECT_BYTES:
        raise PersistenceError(f"{target} is {size} bytes; limit is {MAX_PROJECT_BYTES}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PersistenceError(f"cannot read {target}: {exc}") from exc
    data = migrate(data)
    return Project.from_json(data, node_registry=node_registry)


def migrate(data: Any) -> dict[str, Any]:  # Any: raw JSON
    if not isinstance(data, dict):
        raise PersistenceError("project file must be a JSON object")
    version = data.get("schema_version")
    if not isinstance(version, int):
        raise PersistenceError("project file has no integer schema_version")
    if version > SCHEMA_VERSION:
        raise PersistenceError(
            f"project schema {version} is newer than this Stokes ({SCHEMA_VERSION}); upgrade Stokes"
        )
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise PersistenceError(f"no migration from schema {version}")
        data = step(data)
        version += 1
        data["schema_version"] = version
    result: dict[str, Any] = data
    return result
