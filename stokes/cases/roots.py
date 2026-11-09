"""The tutorial folder the user chose: `$STOKES_HOME/tutorials.json` (ADR-0029, T-304).

**User settings, not the project.** The catalogue folder is a fact about this machine (where the
OpenFOAM tutorials sit), so it lives beside `hosts.json` and `recent.json`, and a project opened on
another machine does not carry a path that means nothing there.

A folder is accepted only when it is an absolute path to an existing directory with at least one
case (`*/system/controlDict`) under it; each refusal has its own code so a client can say which.

Import rules: standard library, `stokes.errors`, `stokes.log`, `stokes.workspace_root`,
`stokes.cases.catalogue`. Headless.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from stokes.cases.catalogue import has_case
from stokes.errors import StokesError, ValidationError
from stokes.log import fields, get_logger

log = get_logger(__name__)

SETTINGS_FILE = "tutorials.json"
SETTINGS_VERSION = 1
MAX_SETTINGS_BYTES = 64 * 1024
MAX_PATH_LENGTH = 4096


class TutorialRootError(ValidationError):
    """A folder cannot be used as the tutorial catalogue."""

    code = "tutorial.root"


def settings_path(home: Path | None = None) -> Path:
    """`$STOKES_HOME/tutorials.json`."""
    from stokes.workspace_root import home as stokes_home

    return (home if home is not None else stokes_home()) / SETTINGS_FILE


def validate_root(text: str) -> Path:
    """The folder as a resolved path, or a `TutorialRootError` saying why it cannot be used."""
    if not text or len(text) > MAX_PATH_LENGTH or "\x00" in text:
        raise TutorialRootError("give the tutorial folder as a path", code="tutorial.root_invalid")
    raw = Path(text).expanduser()
    if not raw.is_absolute():
        raise TutorialRootError(
            f"{text!r} is not an absolute path; give the full path to the tutorial folder",
            code="tutorial.root_relative",
            details={"path": text},
        )
    try:
        path = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TutorialRootError(
            f"{text} does not exist",
            code="tutorial.root_missing",
            details={"path": text},
        ) from exc
    if not path.is_dir():
        raise TutorialRootError(
            f"{text} is a file, not a folder",
            code="tutorial.root_not_folder",
            details={"path": text},
        )
    if not has_case(path):
        raise TutorialRootError(
            f"{text} holds no OpenFOAM case (no */system/controlDict under it)",
            code="tutorial.root_empty",
            details={"path": text},
        )
    return path


def read_root(path: Path | None = None) -> str | None:
    """The stored folder, or None. **A missing or damaged file is None**, logged, never raised."""
    found = path if path is not None else settings_path()
    try:
        if found.stat().st_size > MAX_SETTINGS_BYTES:
            raise ValueError("the settings file is too large")
        data = json.loads(found.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("the tutorial settings could not be read", extra=fields(error=str(exc)[:200]))
        return None
    if not isinstance(data, dict) or data.get("version") != SETTINGS_VERSION:
        return None
    root = data.get("root")
    return root if isinstance(root, str) and root else None


def write_root(root: str | None, path: Path | None = None) -> None:
    """Store the folder (None clears it), atomically and 0600."""
    found = path if path is not None else settings_path()
    try:
        found.parent.mkdir(parents=True, exist_ok=True)
        temp = found.with_name(f".{found.name}.tmp")
        body = {"version": SETTINGS_VERSION, "root": root}
        temp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            temp.chmod(0o600)
        temp.replace(found)
    except OSError as exc:
        raise StokesError(
            f"the tutorial folder could not be saved: {exc}",
            code="tutorial.settings_write",
            details={"path": str(found)},
        ) from exc
