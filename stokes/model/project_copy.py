"""Copying a whole project folder: Save as, and Save a copy.

**The owner's requirement**: a project is its folder, so a copy is the folder's contents. The old
`project.save path=` carried only what each extension remembered to carry - measured, a copy was
missing `imports/`, `cases/`, `logs/` and `preview.png`, because four subsystems each had to
remember and none owned the question.

## Atomic, so a half-copy never looks like a project

The copy is built in `<destination>.partial` and **renamed on success**. A failure part-way - a
full disk, an unreadable file, an in-flight run being written while it is read - removes the
partial and leaves the destination absent **rather than present and half-formed**. A project folder
that opens but is missing its imports is worse than one that is not there: the first looks like
the user's mistake.

## Streamed, so a large project does not arrive in memory

`shutil.copytree` with `copy_function=shutil.copy2`, which copies in chunks. The tests in
`test_project_copy.py` assert that a large cache is copied without being read into memory at once,
because a Save as on a 2 GB project must not need 2 GB of RAM.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Any

from stokes.log import fields, get_logger

log = get_logger(__name__)

#: Appended to the destination while the copy is being built.
PARTIAL_SUFFIX = ".partial"


class CopyError(Exception):
    """A copy that could not be completed. **The partial is already gone when this is raised.**"""


def copy_project(source: Path, destination: Path, *, skip: set[str] | None = None) -> list[str]:
    """Copy every file of `source` into `destination`, **or leave neither**.

    Returns the warnings: things that were deliberately not copied. An empty list is the ordinary
    case.

    **`skip` names entries to leave out, by path relative to `source`** (`runs/<id>`), which is how
    Save a copy avoids an in-flight run's directory - its contents are being written by the solver
    and are not consistent to read. Relative paths, not bare names: matching top-level names meant a
    nested `runs/<id>` could never match, so the exclusion silently did nothing (Opus F4). The
    caller records what it skipped; this function does not decide.
    """
    skip = set(skip or ())
    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
    warnings: list[str] = []

    if destination.exists() and any(destination.iterdir()):
        raise CopyError(f"{destination} already has files in it; choose an empty folder")
    _remove(partial)
    try:
        partial.mkdir(parents=True)
        for entry in sorted(source.iterdir()):
            # **The walk carries the relative path**, so a nested skip can match. `rel` is what
            # `skip` holds; `entry.name` alone is what made F4 invisible.
            _copy_entry(entry, partial, entry.name, skip, warnings)
        # **The rename is the commit.** Before it the destination does not exist; after it the copy
        # is whole, so a reader never sees a project that is partly there.
        partial.rename(destination)
    except BaseException as exc:
        _remove(partial)
        log.warning(
            "a project copy failed and was removed",
            extra=fields(source=str(source), destination=str(destination), error=str(exc)),
        )
        if isinstance(exc, CopyError):
            raise
        raise CopyError(f"could not copy {source} to {destination}: {exc}") from exc
    return warnings


def _copy_entry(
    entry: Path, target_dir: Path, rel: str, skip: set[str], warnings: list[str]
) -> None:
    """Copy one entry into `target_dir`, honouring `skip` **by relative path** and never following
    a link.

    **The walk is explicit rather than `copytree`** (Opus F4, F5). `copytree` cannot skip a nested
    path, and its `symlinks=True` copies a *symlink* but still walks into an NTFS **junction**
    (`is_dir()` follows it), which is how Opus's probe pulled an outside folder into a copy. Walking
    here means one rule at every depth.

    A link is **skipped with a warning**, not copied: a project that references outside itself is
    not self-contained, and a copy is meant to be movable - following it would import a folder the
    user did not choose, and copying the link would produce a copy that breaks on another machine.
    """
    if rel in skip:
        warnings.append(f"{rel}/ was in progress and is not in the copy")
        # A skipped DIRECTORY also matches anything under it, so a nested skip inside it is moot.
        return
    if entry.is_symlink() or _is_junction(entry):
        warnings.append(f"{rel} is a link and was not copied")
        return
    if entry.is_dir():
        target = target_dir / entry.name
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(entry.iterdir()):
            _copy_entry(child, target, f"{rel}/{child.name}", skip, warnings)
        return
    if entry.is_file():
        shutil.copy2(entry, target_dir / entry.name, follow_symlinks=False)


def _is_junction(path: Path) -> bool:
    """Whether `path` is an NTFS junction. **False everywhere but Windows**, and cheap there.

    `Path.is_junction` exists on 3.12+ and answers without a filesystem probe on POSIX, but it is
    guarded anyway so a missing attribute on an older interpreter cannot break a copy.
    """
    probe = getattr(path, "is_junction", None)
    if probe is None:  # pragma: no cover - Python < 3.12
        return False
    try:
        return bool(probe())
    except OSError:  # pragma: no cover - a race with a concurrent unlink
        return False


def _remove(folder: Path) -> None:
    """Delete a partial folder. **Never raises**: it runs on cleanup paths."""
    if folder.exists():
        with contextlib.suppress(OSError):
            shutil.rmtree(folder, ignore_errors=True)


RUNS_FOLDER = "runs"
"""The folder a project's run records live in, **mirrored from `stokes.run.store` rather than
imported**: `stokes.model` must not depend on `stokes.run` (the layering gate refuses it)."""


def in_flight_paths(run_id: str) -> set[str]:
    """What to leave out of a copy because `run_id` is being written, **by relative path**.

    **Paths, not names** (Opus F4). This used to return every run folder's NAME, and `copy_project`
    matched those against TOP-LEVEL entries - so nothing matched unless a run happened to be called
    `runs`, and the live run, its `runs/<id>/` directory and its case files were copied mid-write
    anyway. The caller's warning fired while the exclusion did nothing, which is the worst shape a
    safeguard can take.

    A run that is running is not consistent to read: the solver is still writing its case. Save a
    COPY records it as `interrupted` in the copy (never a spinner - the existing semantics) and
    warns; Save as refuses outright, because switching would abandon the job.
    """
    if not run_id:
        return set()
    return {f"{RUNS_FOLDER}/{run_id}"}


def signature(folder: Path) -> dict[str, Any]:
    """A cheap description of a folder for a warning or a test: how many files, how many bytes."""
    files = 0
    total = 0
    for path in folder.rglob("*"):
        if path.is_file():
            files += 1
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return {"files": files, "bytes": total}
