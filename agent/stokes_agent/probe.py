"""Host probe: what engines and tools exist here (ADR-0011).

Fast and side-effect free: no environment scripts are sourced (that is the
engine attachment step in M2), only paths and executables are looked up.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TOOLS: tuple[str, ...] = (
    "foamRun",
    "foamMultiRun",
    "blockMesh",
    "snappyHexMesh",
    "checkMesh",
    "cartesianMesh",
    "tetMesh",
    "pMesh",
    "polyDualMesh",
    "gmsh",
    "coupledSimpleFoam",
    "paraview",
    "mpirun",
    "nvidia-smi",
    "wsl.exe",
    "ssh",
)
#: Where an OpenFOAM install may sit, as `glob` patterns. **Read-only scanning** - nothing here is
#: run; a match is a directory to look for an environment script in, and the install is only used
#: once the user (or the visible "Auto" default) has chosen it.
#:
#: **The home-folder shapes were missing, and that bit on this very machine** (claude-win2's 1330
#: answer, item 2). The install here is `/home/<user>/opt/openfoam14`, which none of the original
#: four patterns match - so with `STOKES_OPENFOAM_BASHRC` unset the probe reported **no installs at
#: all** on a host that has a perfectly good one. Measuring it rather than reasoning about it: the
#: same probe returned `installs: []` before the environment variable was set and one install after.
#:
#: A user who installs under their own home is the common case for a source build, and `~/opt` is
#: where this product's own instructions put it.
OPENFOAM_GLOBS: tuple[str, ...] = (
    "/opt/openfoam*",
    "/usr/lib/openfoam/*",
    "/opt/OpenFOAM/OpenFOAM-*",
    "~/OpenFOAM/OpenFOAM-*",
    # The home-folder shapes, added 2026-10-17.
    "~/opt/openfoam*",
    "~/openfoam*",
    "~/OpenFOAM*",
)


def memory_bytes() -> int | None:
    try:
        if hasattr(os, "sysconf"):
            return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError):
        return None
    if platform.system() == "Windows":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        # `windll` exists only in the Windows ctypes; mypy is run on Linux, so it cannot see the
        # attribute in the stub. `getattr` keeps one source that type-checks on both hosts.
        windll = getattr(ctypes, "windll", None)
        if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    return None


#: The `OPENFOAM_GLOBS` entries that search the user's HOME rather than a system prefix. Which one a
#: match came from is the difference the Settings list shows: a system-wide install was put there by
#: an administrator, a home one by the user.
HOME_GLOBS: frozenset[str] = frozenset(
    {"~/opt/openfoam*", "~/openfoam*", "~/OpenFOAM*", "~/OpenFOAM/OpenFOAM-"}
)


def _is_install(path: Path) -> bool:
    """Whether ``path`` is the root of an install (it has an ``etc/bashrc``)."""
    return (path / "etc" / "bashrc").is_file()


def _install_root_from_foamrun(executable: str | Path) -> Path | None:
    """The install root named by a ``foamRun`` on PATH.

    Two layouts are supported: the source build's
    ``<root>/platforms/<platform>/bin/foamRun`` and a prefix install's ``<root>/bin/foamRun``.
    Anything else answers None rather than guessing a parent that is not an install.
    """
    path = Path(executable)
    if path.parent.name != "bin":
        return None
    root = (
        path.parent.parent.parent.parent
        if path.parent.parent.parent.name == "platforms"
        else path.parent.parent
    )
    return root if _is_install(root) else None


def openfoam_candidates() -> list[str]:
    """Directories that may hold an OpenFOAM install, environment variables first.

    **The order is a preference, not a ranking**: `WM_PROJECT_DIR`/`FOAM_INST_DIR` name the install
    the shell is already using, so that one is tried first for the environment script while a
    globbed match is a candidate the user has not chosen.

    **`FOAM_INST_DIR` is the parent of the installs, not one install** in the common source-build
    layout (`FOAM_INST_DIR=~/opt`, installs `~/opt/OpenFOAM-14`), so a directory that is not itself
    an install is scanned one level down. **`which foamRun` is the path the sourced shell already
    resolved**, so its install comes next: that is how a user who ran `source .../etc/bashrc`
    before starting Stokes is found without setting anything else.
    """
    found: list[str] = []

    def add(path: Path) -> None:
        if path.is_dir() and str(path) not in found:
            found.append(str(path))

    for key in ("WM_PROJECT_DIR", "FOAM_INST_DIR"):
        value = os.environ.get(key)
        if not value:
            continue
        base = Path(value)
        if not base.is_dir():
            continue
        if _is_install(base):
            add(base)
            continue
        # The source-build convention: FOAM_INST_DIR holds one or more installs.
        try:
            children = sorted(child for child in base.iterdir() if child.is_dir())
        except OSError:
            children = []
        for child in children:
            if _is_install(child):
                add(child)

    executable = shutil.which("foamRun")
    if executable:
        root = _install_root_from_foamrun(executable)
        if root is not None:
            add(root)

    for pattern in OPENFOAM_GLOBS:
        for match in glob.glob(str(Path(pattern).expanduser())):  # noqa: PTH207 - glob of patterns
            if Path(match).is_dir() and match not in found:
                found.append(match)
    return found


#: How long `wsl.exe -l -q` is given. It answers in 0.2 s when it answers at all.
WSL_LIST_TIMEOUT = 5.0


def wsl_distros() -> list[str]:
    """Names of installed WSL distros (Windows only); empty elsewhere or when WSL is absent.

    **`stdin=DEVNULL` is the point of the shape** (measured 2026-11-01, the "Start screen although
    a project is open" report): started from a process whose stdin is a console, `wsl.exe -l -q`
    did not exit until the timeout killed it; with `DEVNULL` on stdin it answered in 0.2 s. The
    server runs this on its event loop from `host.list`, which the mesh panel asks for as soon as a
    client connects, so the hang held `node.tree` and `project.info` behind it and the window sat on
    the Start screen for the best part of a minute.

    `Popen` rather than `run`: on Windows `run` answers a timeout by killing the child and then
    calling `communicate()` again, which joins the pipe readers - and those block until every holder
    of the pipe has gone, which for `wsl.exe` took longer than the timeout itself. A child that has
    not answered in time is killed and left; its reader thread is a daemon.
    """
    if platform.system() != "Windows" or shutil.which("wsl.exe") is None:
        return []
    argv = ["wsl.exe", "-l", "-q"]  # resolved on PATH by design; no shell involved
    try:
        child = subprocess.Popen(  # noqa: S603
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    try:
        stdout, _ = child.communicate(timeout=WSL_LIST_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        child.kill()
        return []
    text = stdout.decode("utf-16-le", errors="ignore").replace("\x00", "")
    return [line.strip() for line in text.splitlines() if line.strip()]


#: Upper bounds on what one inventory lists, so a damaged or hostile install tree cannot make the
#: probe record (one msgpack frame) unbounded. A FoamWin 14 install has about 100 executables, 330
#: library files and 260 etc case dictionaries.
MAX_INVENTORY = 5000
#: How deep the etc walk goes below `caseDicts`. The tree is three levels deep in OpenFOAM 14.
MAX_ETC_DEPTH = 8
#: The `caseDicts` folders `#includeFunc` searches, in OpenFOAM 14 order.
ETC_FUNCTION_DIRS: tuple[str, ...] = ("functions", "postProcessing")
#: Library file suffixes, per platform. `.a` and `.def` are import or static archives on MinGW and
#: name the same library as the `.dll` beside them, so they are not counted.
LIBRARY_SUFFIXES: tuple[str, ...] = (".so", ".dll", ".dylib")


def library_name(filename: str) -> str | None:
    """`libfoo.so`, `libfoo.dll`, `foo.dll` -> `foo`; anything that is not a library -> None.

    One spelling for the probe and for the catalogue, so a case's `libs ("libfoo.so")` matches a
    Windows install's `libfoo.dll`.
    """
    stem, dot, suffix = filename.rpartition(".")
    if not dot or f".{suffix.lower()}" not in LIBRARY_SUFFIXES or not stem:
        return None
    name = stem[3:] if stem.startswith("lib") else stem
    return name or None


def _directory(env: Mapping[str, str], key: str) -> Path | None:
    """`env[key]` as an existing absolute directory, or None. Nothing is created or followed."""
    value = env.get(key, "")
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() and path.is_dir() else None


def _files(folder: Path | None) -> list[Path]:
    """The regular files directly in ``folder``, sorted, bounded. Empty when it is absent."""
    if folder is None:
        return []
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return []
    return [entry for entry in entries[: MAX_INVENTORY * 4] if entry.is_file()]


def _executables(appbin: Path | None) -> list[str]:
    """Application names in `FOAM_APPBIN`: `.exe` stems on Windows, executable files elsewhere."""
    names: set[str] = set()
    for entry in _files(appbin):
        if entry.suffix.lower() == ".exe":
            names.add(entry.stem)
        elif not entry.suffix and os.access(entry, os.X_OK):
            names.add(entry.name)
    return sorted(names)[:MAX_INVENTORY]


def _etc_files(etc: Path | None) -> list[str]:
    """Paths under `FOAM_ETC/caseDicts`, relative to `FOAM_ETC`, posix-spelt; links not followed."""
    if etc is None:
        return []
    root = etc / "caseDicts"
    out: list[str] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack and len(out) < MAX_INVENTORY:
        folder, depth = stack.pop()
        try:
            entries = sorted(folder.iterdir(), reverse=True)
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir() and depth < MAX_ETC_DEPTH:
                stack.append((entry, depth + 1))
            elif entry.is_file():
                out.append(entry.relative_to(etc).as_posix())
    return sorted(out)


def install_inventory(env: Mapping[str, str]) -> dict[str, Any]:
    """What one install can run, read from its captured `FOAM_*` environment (ADR-0029, T-304).

    **Lists directories; runs nothing.** Nothing from the install or from any case is executed:
    `FOAM_APPBIN` gives the applications, `FOAM_LIBBIN` the libraries and the `lib<name>Solver`
    solver modules, `FOAM_ETC/caseDicts` the files `#includeEtc` and `#includeFunc` may name, and
    `FOAM_TUTORIALS` (or `WM_PROJECT_DIR/tutorials`) the tutorial root. A missing variable or folder
    gives an empty list, never an error, so an older or partial install still probes.
    """
    libraries: set[str] = set()
    modules: set[str] = set()
    for entry in _files(_directory(env, "FOAM_LIBBIN")):
        name = library_name(entry.name)
        if name is None:
            continue
        libraries.add(name)
        if name.endswith("Solver") and len(name) > len("Solver"):
            modules.add(name[: -len("Solver")])
    case_dicts = _etc_files(_directory(env, "FOAM_ETC"))
    functions = sorted(
        {
            path.rsplit("/", 1)[-1]
            for path in case_dicts
            if path.split("/")[1:2] and path.split("/")[1] in ETC_FUNCTION_DIRS
        }
    )
    tutorials = _directory(env, "FOAM_TUTORIALS")
    if tutorials is None:
        project = _directory(env, "WM_PROJECT_DIR")
        candidate = project / "tutorials" if project is not None else None
        tutorials = candidate if candidate is not None and candidate.is_dir() else None
    return {
        "solver_modules": sorted(modules)[:MAX_INVENTORY],
        "utilities": _executables(_directory(env, "FOAM_APPBIN")),
        "libraries": sorted(libraries)[:MAX_INVENTORY],
        "etc_functions": functions[:MAX_INVENTORY],
        "etc_case_dicts": case_dicts,
        "tutorials_root": str(tutorials) if tutorials is not None else None,
    }


def probe_host() -> dict[str, Any]:
    return {
        "hostname": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": memory_bytes(),
        "tools": {tool: shutil.which(tool) for tool in TOOLS},
        "openfoam_candidates": openfoam_candidates(),
        "wsl_distros": wsl_distros(),
        "home": str(Path.home()),
    }
