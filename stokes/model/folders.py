"""Choosing a project folder: validation, creation, and the refusals (start screen, step 2).

**One rule, three callers.** `project.new`, `project.save_as` and `project.save_copy` all answer
the same question - "may this folder become a project?" - and the owner's requirement is specific
about the answer:

> Starting a new project should also ask for which folder to start in, and all files etc should be
> created in this folder.

So the checks live here rather than being repeated, because three copies of a containment rule are
three chances to get it wrong. **Every refusal has its own code**, because the front end shows a
different message for each and a single `project.invalid` would leave it guessing.

## What "empty" means, and why it is strict

A folder may be **absent** or **empty**. Anything else is refused, and **nothing in it is
touched** - not cleaned, not merged, not renamed. The failure this prevents is a user pointing at
their Documents folder, or at a project they already have, and losing what was there. Refusing
costs a click; the other mistake costs work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stokes.errors import StokesError
from stokes.log import get_logger

log = get_logger(__name__)

#: The project marker. A folder holding one is already a project.
PROJECT_FILE = "project.json"
#: A name becomes a path segment, so it follows the same rule a preset name does.
_NAME_BAD = set('/\\:*?"<>|')


class FolderError(StokesError):
    """A folder may not become a project, and the message says which rule refused it."""

    code = "project.folder_invalid"


def check_name(name: str) -> str:
    """A project name, or a refusal. **It becomes a path segment**, so a separator is out.

    Deliberately narrow: letters, digits, spaces, `-`, `_`, `.` and a few marks, with no path
    separator, no drive colon, no Windows-reserved character and no control character. A name is
    shown in a title bar and used as a folder, and the two roles have to agree on what is legal.
    """
    text = (name or "").strip()
    if not text:
        raise FolderError("a project needs a name", code="project.name_invalid")
    if text in (".", ".."):
        raise FolderError(f"{text!r} is not a project name", code="project.name_invalid")
    if any(ch in _NAME_BAD for ch in text) or any(ord(ch) < 32 for ch in text):
        raise FolderError(
            f"a project name cannot contain any of {''.join(sorted(_NAME_BAD))}",
            code="project.name_invalid",
            details={"name": text},
        )
    if text.startswith("-"):
        # **The same rule the host and python fields follow**: a leading `-` is an option to
        # something, and a name reaches a command line through the case directory.
        raise FolderError("a project name cannot start with '-'", code="project.name_invalid")
    return text


def project_folder(parent: str | Path, name: str) -> Path:
    """`<parent>/<name>`, resolved, with every escape refused.

    **The parent must be a directory and the result must land inside it.** Resolving the child as
    well as the parent is what catches a `name` that is a symlink pointing elsewhere - the check is
    on the RESULT rather than on the string, which is the same rule `project.browse` follows.
    """
    root = Path(parent).expanduser()
    try:
        root = root.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise FolderError(f"cannot use {parent}: {exc}") from exc
    if not root.is_dir():
        raise FolderError(f"{root} is not a folder", details={"path": str(root)})
    child = root / check_name(name)
    # **`resolve()` on the child too**, so a symlink called like the project cannot point out of the
    # parent. A path that does not exist resolves to itself, which is the ordinary case.
    try:
        target = child.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise FolderError(f"cannot use {child}: {exc}") from exc
    if not target.is_relative_to(root):
        raise FolderError(f"{target} is not inside {root}")
    return target


def prepare(folder: Path) -> Path:
    """Make `folder` ready to be a project, or refuse **without touching what is there**.

    Returns the folder. A refusal never creates, never cleans and never renames: the three things a
    user would most regret.
    """
    check(folder)
    if folder.exists():
        return folder
    try:
        folder.mkdir(parents=True)
    except OSError as exc:
        raise FolderError(f"cannot create {folder}: {exc}") from exc
    return folder


def check(folder: Path) -> Path:
    """Whether `folder` is usable as a project, **creating nothing**.

    **The copy path needs this, and using `prepare` there was a data-integrity bug on Windows**
    (Opus F1). A copy is built in `<dest>.partial` and committed by renaming that onto `<dest>`;
    POSIX renames onto an existing EMPTY directory happily, but Windows fails it with
    `WinError 183`, so `save_as` returned `project.copy_failed` and left the empty destination
    folder behind. Validating and creating are different questions, and the copy only asks the
    first one.
    """
    if folder.exists():
        if not folder.is_dir():
            raise FolderError(f"{folder} is a file, not a folder", details={"path": str(folder)})
        try:
            entries = [entry.name for entry in folder.iterdir() if not entry.name.startswith(".")]
        except OSError as exc:
            raise FolderError(
                f"cannot read {folder}: {exc}", details={"path": str(folder)}
            ) from exc
        if entries:
            # **Its own code**, because the front end says something different for it: "that folder
            # already has files in it" is actionable ("choose another") in a way a generic invalid
            # path is not.
            # **The class carries a generic code; this case overrides it.** The message is
            # actionable in a way the generic one is not: "choose an empty folder".
            raise FolderError(
                f"{folder} already has files in it; choose an empty folder",
                code="project.folder_not_empty",
                details={"path": str(folder)},
            )
    return folder


def inside(child: Path, parent: Path) -> bool:
    """Whether `child` is `parent` or inside it. Used to refuse a copy onto its own source."""
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def describe(folder: Path) -> dict[str, Any]:
    """What a folder holds, for a message or a test. **Read-only**, and it never raises."""
    out: dict[str, Any] = {"path": str(folder), "exists": folder.is_dir(), "entries": []}
    if out["exists"]:
        import contextlib

        with contextlib.suppress(OSError):
            out["entries"] = sorted(entry.name for entry in folder.iterdir())
    return out
