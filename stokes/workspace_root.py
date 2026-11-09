"""Where Stokes keeps the data it generates: one root, and one place that decides it.

## Why this module exists

Generated data used to land wherever each writer happened to point: `$STOKES_HOME`, the agent's
own home, the repository, and **`/tmp` in five separate places** through an unscoped
`tempfile.mkdtemp`. That scattering filled the disk twice - `.coordination/DISK-FULL.md` records the
first, where a 4 M-cell mesh probe wrote hundreds of MiB per case into `/tmp`, and the second on
2026-10-16 while meshing.

A single accessor does not make the data small, but it makes it **measurable**: one `du` answers how
much a session has used, and one `rm -rf` reclaims it. It also gives a free-space check somewhere to
live, which is what the first incident actually needed.

## The layout

    <root>/projects    saved projects
    <root>/jobs        agent job folders (one per mesh or solve)
    <root>/scratch     temporary folders a job needs while it runs
    <root>/logs        server and agent logs

## Reading the environment

`STOKES_HOME` is the app's root and `STOKES_AGENT_HOME` the agent's; both default to
`~/.stokes` when unset, exactly as before, so nothing changes for an existing install. Pointing
both at one folder - `~/stokes-work` on this machine - puts every artefact under one path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

#: The environment variable that selects the app's root.
HOME_ENV = "STOKES_HOME"

#: The environment variable that selects the agent's root.
AGENT_HOME_ENV = "STOKES_AGENT_HOME"

#: The fallback when neither is set, kept as it was so an existing install is unchanged.
DEFAULT_FOLDER = ".stokes"

#: The folder under the root that holds temporary work. **A NAME, not a path**, so every caller
#: resolves it against the same root.
SCRATCH_FOLDER = "scratch"


def home() -> Path:
    """The app's root: `$STOKES_HOME`, else `~/.stokes`. **Creates nothing.**

    **This is APP DATA, not the user's projects.** It holds `recent.json`, the workspace files,
    `hosts.json`, `known_hosts`, caches that are not part of a project, logs and scratch - nothing a
    user would want to find, copy or back up. The projects live under :func:`user_root`.
    """
    configured = os.environ.get(HOME_ENV)
    return Path(configured) if configured else Path.home() / DEFAULT_FOLDER


#: The environment variable that selects the visible user-data root, for tests and servers.
USER_HOME_ENV = "STOKES_USER_HOME"

#: The visible folder's name, under the OS's Documents folder.
USER_FOLDER = "Stokes"

#: The subfolders of the visible root. **Named here so every caller agrees**, and so a Browse root
#: and the folder a project is written into cannot drift apart.
PROJECTS_SUBFOLDER = "Projects"
SAMPLES_SUBFOLDER = "Samples"


def documents_folder() -> Path | None:
    """The OS's real Documents folder, or None when it has none.

    **The platform's own answer, not a guess**, because a Documents folder can be redirected - on
    Windows to OneDrive, on Linux by `xdg-user-dirs`. A hardcoded `~/Documents` would write a second
    Documents folder beside the user's real one, which is the failure this whole change is about.

    * **Windows**: `SHGetKnownFolderPath(FOLDERID_Documents)`, which follows the redirection.
    * **macOS**: `~/Documents`.
    * **Linux**: `xdg-user-dir DOCUMENTS`, then `$XDG_DOCUMENTS_DIR`, then `~/Documents`.
    * Anything else, or a folder that does not exist: None, and the caller falls back.
    """
    # Read through a variable: mypy narrows a direct `sys.platform` test to the target it checks, so
    # on Windows the macOS branch would be "unreachable" and the gate would fail. The variable keeps
    # every branch type-checked on every host, and the runtime answer is the same.
    platform = str(sys.platform)
    if platform == "win32":
        return _windows_documents()
    if platform == "darwin":
        candidate = Path.home() / "Documents"
        return candidate if candidate.is_dir() else None
    return _linux_documents()


def _windll() -> Any:
    """`ctypes.windll`, which the stubs do not declare on non-Windows platforms.

    **One accessor rather than two `# type: ignore`s.** `ctypes.windll` exists only on Windows, so
    mypy sees no such attribute; the existing `stokes/app/icon.py` and `agent/…/probe.py` each carry
    their own error for the same reason. Going through a function that returns `Any` keeps the call
    sites typed and adds nothing to that tally, and the `noqa`-free body is the same statement the
    other two write.
    """
    import ctypes

    # `getattr` rather than `ctypes.windll`: the attribute does not exist on any platform mypy
    # checks against, so naming it directly is an error however the function is typed. This is the
    # same call, reached by the one spelling that is honest about the attribute being platform-only.
    return getattr(ctypes, "windll")  # noqa: B009 - the attribute is Windows-only, by design


def _windows_documents() -> Path | None:
    """`SHGetKnownFolderPath`, which is the only call that follows a OneDrive redirect."""
    try:
        import ctypes

        # FOLDERID_Documents = {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        guid = ctypes.create_string_buffer(
            b"\xd0\x9a\xd3\xfd\x8f\x23\xaf\x46\xad\xb4\x6c\x85\x48\x03\x69\xc7"
        )
        out = ctypes.c_wchar_p()
        library = _windll()
        shell32 = library.shell32
        ole32 = library.ole32
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        if shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            path = Path(out.value) if out.value else None
        finally:
            ole32.CoTaskMemFree(out)
        return path if path is not None and path.is_dir() else None
    except Exception:
        return None


def _linux_documents() -> Path | None:
    """`xdg-user-dir DOCUMENTS`, then the variable it reads, then `~/Documents`."""
    xdg = shutil.which("xdg-user-dir")
    if xdg is not None:
        try:
            done = subprocess.run(  # noqa: S603 - a fixed argv, no shell
                [xdg, "DOCUMENTS"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            text = done.stdout.strip()
            if text and Path(text).is_dir():
                return Path(text)
        except (OSError, subprocess.SubprocessError):
            pass
    from_env = os.environ.get("XDG_DOCUMENTS_DIR")
    if from_env and Path(from_env).is_dir():
        return Path(from_env)
    candidate = Path.home() / "Documents"
    return candidate if candidate.is_dir() else None


def user_root() -> Path:
    """The VISIBLE root: `<Documents>/Stokes`, or `~/Stokes`. **Creates nothing.**

    **The folder the owner asked to be able to find.** Projects and sample copies live under it, so
    a user can open their file manager and see them - which is the whole point, and what a hidden
    `~/.stokes` made impossible.

    `$STOKES_USER_HOME` overrides it, which is how tests and a server keep their own root.
    """
    configured = os.environ.get(USER_HOME_ENV)
    if configured:
        return Path(configured)
    documents = documents_folder()
    return (documents / USER_FOLDER) if documents is not None else (Path.home() / USER_FOLDER)


def agent_home() -> Path:
    """The agent's root: `$STOKES_AGENT_HOME`, else `~/.stokes`. **Creates nothing.**"""
    configured = os.environ.get(AGENT_HOME_ENV)
    return Path(configured) if configured else Path.home() / DEFAULT_FOLDER


def scratch_root() -> Path:
    """`<root>/scratch`, created if it is not there.

    **The one place a temporary folder for a job is made.** `stokes.geometry.store`,
    `geometry.opstore`, `geometry.step` and the demo builder each called `tempfile.mkdtemp` with
    no `dir=`, which means `/tmp` on every platform. An import of a large CAD file writes
    hundreds of MiB there. They call this instead, so the total is visible and one directory
    holds it.
    """
    folder = home() / SCRATCH_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def scratch(prefix: str) -> Path:
    """A new temporary folder under :func:`scratch_root`.

    `tempfile.mkdtemp(dir=...)` rather than a hand-built name: the standard library's uniqueness and
    atomicity are worth keeping, and the whole change is telling it WHERE.
    """
    return Path(tempfile.mkdtemp(prefix=prefix, dir=scratch_root()))


def temporary(prefix: str, suffix: str = "") -> str:
    """A new temporary FILE under :func:`scratch_root`, as its path.

    For the writers that need a file rather than a folder - a tunnel log, a staged host key. Also
    `dir=`-scoped, for the same reason.
    """
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=scratch_root())
    os.close(descriptor)
    return name


def free_bytes(path: Path | None = None) -> int:
    """Free space on the filesystem holding ``path`` (the root by default).

    **Exposed so a caller can refuse work it cannot finish.** Nothing used this before either disk
    incident: a 4 M-cell mesh needs hundreds of MiB, and the job that filled the disk had no way to
    ask whether there was room.
    """
    target = path if path is not None else home()
    try:
        return shutil.disk_usage(target).free
    except OSError:  # pragma: no cover - an unreadable or absent root
        return 0


def measure() -> dict[str, int]:
    """Bytes used under each part of the root, for a log line or a report.

    **Read-only and never raises**: it is called to explain a situation, so failing on a missing
    folder would be the least helpful thing it could do.
    """
    out: dict[str, int] = {}
    root = home()
    for name in (root, *sorted(p for p in root.iterdir() if p.is_dir())):
        total = 0
        try:
            for path in Path(name).rglob("*"):
                if path.is_file():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue
        out[str(Path(name).relative_to(root)) if Path(name) != root else "."] = total
    return out
