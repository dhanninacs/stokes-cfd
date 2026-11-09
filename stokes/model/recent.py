"""The recent-projects list: `$STOKES_HOME/recent.json` (start screen).

**What it is for.** Discovery's start screen lists recent projects and previews one on hover;
Stokes has no such list at all, so nothing can be shown before a project is open. This is the
store behind that list - **and only the store**: the screen itself is the front end's.

## What it holds, and what it deliberately does not

`{name, path, modified, opened}` per entry, most recent first, capped. **No thumbnail here**:
the image belongs beside the project it describes, because a list file in `$STOKES_HOME` would
then hold binary data for projects that live anywhere, and a project deleted without the list
noticing would leave an orphan image in a file nobody thinks to clean.

**A path, never a copy.** If the project moves, the entry stops resolving and `exists` says so -
which a start screen can show as a greyed row. Copying a project into our own storage would mean
two copies that can disagree, and the project folder is the user's.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stokes.log import get_logger

log = get_logger(__name__)

#: The file inside `$STOKES_HOME`.
RECENT_FILE = "recent.json"
RECENT_VERSION = 1
#: How many entries are kept. **Discovery shows a short list**, and a start screen is not a file
#: manager: past a few dozen a list is worse than a Browse button, and every entry costs a `stat` to
#: resolve.
MAX_RECENT = 24
#: The project file inside a project folder, used to recognise one and to read its name.
PROJECT_FILE = "project.json"


@dataclass(frozen=True)
class RecentProject:
    """One entry. **`exists` is computed on read**, never stored.

    Storing it would go stale the moment a project is moved or deleted on disk - which is exactly
    when a start screen needs to know, and a stale `true` there is a row that opens nothing.
    """

    name: str
    path: str
    modified: str
    opened: str

    @property
    def exists(self) -> bool:
        return (Path(self.path) / PROJECT_FILE).is_file()

    def to_json(self) -> dict[str, str]:
        return {
            "name": self.name,
            "path": self.path,
            "modified": self.modified,
            "opened": self.opened,
        }


def recent_path(home: Path | None = None) -> Path:
    """`$STOKES_HOME/recent.json`."""
    from stokes.workspace.merge import stokes_home

    return (home if home is not None else stokes_home()) / RECENT_FILE


def read_recent(path: Path | None = None) -> list[RecentProject]:
    """Every entry, most recent first. **A missing or unreadable file is an empty list.**

    A start screen is the first thing a user sees; it must not fail to appear because of a list
    nobody needs. A damaged file is logged and treated as empty, which is the same answer as a new
    install.
    """
    found = path if path is not None else recent_path()
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning(
            "the recent list could not be read", extra={"path": str(found), "error": str(exc)}
        )
        return []
    if not isinstance(data, dict) or data.get("version") != RECENT_VERSION:
        return []
    entries = data.get("projects")
    if not isinstance(entries, list):
        return []
    out: list[RecentProject] = []
    for item in entries:
        entry = _read_entry(item)
        if entry is not None:
            out.append(entry)
    return out[:MAX_RECENT]


def _read_entry(item: Any) -> RecentProject | None:
    """One entry, or None when it is not usable. **Lenient**: a start screen shows what it can."""
    if not isinstance(item, dict):
        return None
    path = item.get("path")
    if not isinstance(path, str) or not path:
        return None
    name = item.get("name")
    return RecentProject(
        name=name if isinstance(name, str) and name else Path(path).stem,
        path=path,
        modified=str(item.get("modified") or ""),
        opened=str(item.get("opened") or ""),
    )


def remember(
    path: str | Path,
    *,
    home: Path | None = None,
    opened: str = "",
) -> list[RecentProject]:
    """Add or move a project to the front of the list. Returns the new list.

    **Keyed by resolved path**, so the same project opened twice is one entry rather than two -
    and a path with `..` or a trailing slash in it does not become a second row.

    The name and `modified` are read from the project file **if it is there**. A project whose
    folder is gone is still recorded, because the user did open it, and a start screen that
    silently forgets is worse than one showing a row that says the folder is missing.
    """
    folder = Path(path)
    key = _key(folder)
    found = recent_path(home)
    entries = [entry for entry in read_recent(found) if _key(Path(entry.path)) != key]
    name, modified = _describe(folder)
    entries.insert(
        0,
        RecentProject(
            name=name,
            path=str(folder),
            modified=modified,
            opened=opened or _now(),
        ),
    )
    _write(entries[:MAX_RECENT], found)
    return entries[:MAX_RECENT]


def forget(path: str | Path, *, home: Path | None = None) -> list[RecentProject]:
    """Drop one project from the list. Returns the new list."""
    key = _key(Path(path))
    found = recent_path(home)
    kept = [entry for entry in read_recent(found) if _key(Path(entry.path)) != key]
    _write(kept, found)
    return kept


def _key(folder: Path) -> str:
    """The identity of an entry: the resolved path, so two spellings are one project.

    **`resolve()` on a path that does not exist still normalises it** on this platform, and a
    non-existent path must stay recordable, so no existence check is made here.
    """
    with contextlib.suppress(OSError, RuntimeError):
        return str(folder.resolve())
    return str(folder)


def _describe(folder: Path) -> tuple[str, str]:
    """The project's own name and its last write, or a fallback when it cannot be read."""
    try:
        data = json.loads((folder / PROJECT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return folder.stem, ""
    if not isinstance(data, dict):
        return folder.stem, ""
    name = data.get("name")
    modified = data.get("modified")
    return (
        name if isinstance(name, str) and name else folder.stem,
        modified if isinstance(modified, str) else "",
    )


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _write(entries: list[RecentProject], path: Path) -> None:
    """Write the list atomically and **0600**.

    Never raises: a start screen must not fail to open because a list could not be saved.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"version": RECENT_VERSION, "projects": [entry.to_json() for entry in entries]}
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            temp.chmod(0o600)
        temp.replace(path)
    except OSError as exc:
        # **A list that cannot be written is not an error the caller can act on.** The project
        # still opened; it simply will not be remembered.
        log.warning(
            "the recent list could not be written",
            extra={"path": str(path), "error": str(exc)},
        )
