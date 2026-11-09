"""Browsing for a project folder, from a browser (start screen).

**Why this is server-side.** The app is served to a browser, often on another machine, so a
native file dialog is not available to it. That leaves a listing done by the server - which
means the server decides what may be listed, and that decision is the whole security here.

## The rules, and what each one stops

* **Two roots only**: the server user's home, and `$STOKES_HOME/projects`. A `path` is resolved
  and must land inside one of them, so `..`, a symlink and an absolute path all fail the same
  check - the check is on the RESULT, not the string, because a string check is what gets
  bypassed.
* **Directories and project folders only.** A listing that could reveal arbitrary files is a
  file browser, which is not what a start screen needs and not what we should offer.
* **Hidden names and symlinks are skipped.** A `.ssh` folder is never a project, and a symlink
  is the classic way out of a root.
* **A cap on entries**, so a home directory with 200,000 files cannot be used to exhaust memory.
* **No contents, ever.** Only a name, a kind and a modification time.

`roots()` is a function rather than a constant because `$STOKES_HOME` is read per call - a test
sets it - and the projects folder is created on demand.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stokes.errors import StokesError
from stokes.log import get_logger

log = get_logger(__name__)

#: The project marker: a folder with this file is a project.
PROJECT_FILE = "project.json"
#: Where projects are offered by default, under `$STOKES_HOME`. Created on first use.
PROJECTS_FOLDER = "projects"
#: The most entries one listing returns. A directory larger than this is truncated and says so.
MAX_ENTRIES = 500


class BrowseError(StokesError):
    """A listing was refused, or could not be made."""

    code = "project.browse_denied"


@dataclass(frozen=True)
class Entry:
    """One row of a listing. **No contents, no size of anything but a project.**"""

    name: str
    kind: str  # "dir" or "project"
    modified: str

    def to_json(self) -> dict[str, str]:
        return {"name": self.name, "kind": self.kind, "modified": self.modified}


def projects_folder(home: Path | None = None) -> Path:
    """`<Documents>/Stokes/Projects`, created if it is not there.

    **Visible on purpose** (the owner's 0700 order): the folder used to be `$STOKES_HOME/projects`
    under a hidden `~/.stokes`, so every new project, every sample copy and - on Windows - the whole
    tree lived where the user never looks. Nothing a user would want to find or back up belongs in a
    dot-folder.

    `home` is the TEST seam: a caller that passes one gets `<home>/Projects`, which keeps every test
    in its temp root. `$STOKES_USER_HOME` overrides the visible root for a server.
    """
    from stokes.workspace_root import PROJECTS_SUBFOLDER, user_root

    folder = (home if home is not None else user_root()) / PROJECTS_SUBFOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def roots(home: Path | None = None) -> list[Path]:
    """The folders a listing may reach into. **Home and the projects folder, and nothing else.**"""
    out: list[Path] = []
    for candidate in (Path.home(), projects_folder(home)):
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved.is_dir() and resolved not in out:
            out.append(resolved)
    return out


def resolve_inside(raw: str | None, home: Path | None = None) -> Path:
    """`raw` as a folder inside a root, or a refusal.

    **The check is on the RESOLVED path.** `..`, a symlink and an absolute path outside the
    roots all collapse to the same test - and a string check is the one an attacker writes
    around.
    """
    allowed = roots(home)
    if not allowed:
        raise BrowseError("there is nowhere to browse", details={"code": "project.browse_denied"})
    # **No path means the PROJECTS folder, not the home folder.** Home is a root because a user's
    # projects can be anywhere in it, but it is the wrong place to OPEN at: a start screen that
    # greets you with `src`, `Videos` and your other work is a file browser, and it shows a stranger
    # at the browser whatever the home directory happens to hold. The projects folder is ours, empty
    # on a new install, and the obvious place to put a project.
    candidate = Path(raw).expanduser() if raw else projects_folder(home)
    try:
        target = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise BrowseError(f"cannot use {raw!r}: {exc}", details={"path": str(raw)}) from exc
    if not any(target.is_relative_to(root) for root in allowed):
        # **The path is NOT echoed.** It comes from a client, and a refusal that repeats it
        # writes an attacker-chosen string into a log line.
        raise BrowseError(
            "that folder is outside the folders this can browse",
            details={"code": "project.browse_denied", "roots": [str(root) for root in allowed]},
        )
    if not target.is_dir():
        raise BrowseError("that is not a folder", details={"code": "project.browse_denied"})
    return target


def list_folder(raw: str | None, home: Path | None = None) -> dict[str, Any]:
    """A listing of `raw`, or of the first root when it is None.

    **Directories and project folders only.** A folder holding `project.json` is a project -
    exactly the test `project.open` makes, so what the screen offers is what will open.
    """
    folder = resolve_inside(raw, home)
    allowed = roots(home)
    try:
        entries = sorted(folder.iterdir(), key=lambda path: path.name.lower())
    except OSError as exc:
        raise BrowseError(f"cannot read that folder: {exc}") from exc
    rows: list[Entry] = []
    for path in entries:
        row = _row(path)
        if row is not None:
            rows.append(row)
        if len(rows) > MAX_ENTRIES:
            break
    truncated = len(rows) > MAX_ENTRIES
    parent = _parent_of(folder, allowed)
    return {
        "path": str(folder),
        "parent": str(parent) if parent is not None else None,
        "roots": [str(root) for root in allowed],
        "entries": [row.to_json() for row in rows[:MAX_ENTRIES]],
        "truncated": truncated,
    }


def _row(path: Path) -> Entry | None:
    """One entry, or None when it must not be shown.

    **A symlink is skipped, not followed**: following one is how a listing reaches outside a root
    without ever naming a path outside it. Hidden names go too - `.ssh`, `.git` and `.config` are
    never projects, and offering them invites a click that goes nowhere.
    """
    name = path.name
    if name.startswith("."):
        return None
    try:
        if path.is_symlink() or not path.is_dir():
            return None
        is_project = (path / PROJECT_FILE).is_file()
        modified = _modified(path / PROJECT_FILE if is_project else path)
    except OSError:
        return None
    return Entry(name=name, kind="project" if is_project else "dir", modified=modified)


def _modified(path: Path) -> str:
    from datetime import UTC, datetime

    try:
        stamp = path.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(stamp, UTC).isoformat()


def _parent_of(folder: Path, allowed: list[Path]) -> Path | None:
    """The parent folder, or None when going up would leave the roots.

    **A root has no parent here**, so the screen shows no "up" at the top rather than a button that
    earns a refusal.
    """
    if folder in allowed:
        return None
    parent = folder.parent
    return parent if any(parent.is_relative_to(root) for root in allowed) else None
