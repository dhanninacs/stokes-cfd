"""Capture an OpenFOAM install's environment once, and report what a host has (ADR-0024, T-140).

## Why the environment is captured rather than sourced per call

An OpenFOAM utility only runs after the install's ``etc/bashrc`` was sourced. Sourcing it for
every call would mean a shell string per call, and `docs/engineering/security.md` forbids that:
argv lists only, no ``shell=True``, a minimal explicit environment. So the bashrc is sourced
**once**, the resulting environment is kept as a dict, and every utility afterwards is launched as
an argv list with that dict as ``env``.

## What is filtered, and why it matters

``bashrc`` exports a great deal, including things the utilities do not need and the agent must not
keep. Only keys in `ENV_KEYS` and keys starting with a prefix in `ENV_PREFIXES` survive, so the
environment crossing into a subprocess is the one OpenFOAM needs and nothing more.

**Measured on the mesh host:** the filtered set is enough for `blockMesh -help` and
`foamRun -help` to run, both exit 0. Sourcing **only** `etc/bashrc`, without the env script's MPI
prefixes, makes `blockMesh -help` fail with exit 127 (``libmpi.so.40`` not found) — so on this
install the capture has to come from a file that exports the MPI paths, which is what
``STOKES_OPENFOAM_BASHRC`` names.

## The argv, and why the path travels as an argument

    bash --noprofile --norc -c 'source "$1" >/dev/null 2>&1; env -0' stokes-env <bashrc>

The script is a **constant**; the bashrc path arrives as ``$1``, so a path containing a space, a
quote or a semicolon cannot change what runs. ``env -0`` gives NUL-separated entries, the only
delimiter that survives a value containing a newline.

Standard library only, and nothing is written to disk: the agent writes only under
``~/.stokes/jobs/<id>``.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stokes.errors import StokesError
from stokes.log import get_logger

from .probe import install_inventory, openfoam_candidates, probe_host

log = get_logger(__name__)

# The utilities the mesh slice drives, in the order the brief lists them. A name that does not exist
# on
# the host is reported as null rather than omitted, so a caller can tell "absent" from "not asked
# about".
UTILITIES: tuple[str, ...] = (
    "blockMesh",
    "checkMesh",
    "snappyHexMesh",
    "surfaceFeatures",
    "surfaceFeatureExtract",
    "foamRun",
    "foamPostProcess",
    "foamToVTK",
    "decomposePar",
    "reconstructPar",
    "topoSet",
    "cartesianMesh",
    "tetMesh",
    "pMesh",
    "polyDualMesh",
    # The polyhedral type's symmetry planes (`stokes.mesh.drivers.cfmesh`): flatten, set back.
    "improveSymmetryPlanes",
    "foamDictionary",
    "gmsh",
    "gmshToFoam",
)

# The ordinary environment a process needs, and nothing else.
ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "LD_LIBRARY_PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
)

# The prefixes OpenFOAM and MPI use. **`PMIX_` and `PRTE_` are in the list because this install
# needs
# them**: the brief's version carried only `MPI_`/`OPAL_`/`OMPI_`, and the env script exports
# `PMIX_PREFIX`/`PRTE_PREFIX`/`PRTE_PKGDATADIR` as well.
ENV_PREFIXES: tuple[str, ...] = ("WM_", "FOAM_", "MPI_", "OPAL_", "OMPI_", "PMIX_", "PRTE_")

# A captured environment is a few tens of kilobytes; a megabyte means something went wrong.
MAX_CAPTURE_BYTES = 1024 * 1024

# The licence-free shell script. A constant: no data is ever interpolated into it.
_SOURCE_SCRIPT = 'source "$1" >/dev/null 2>&1; env -0'

_cache: dict[str, EngineEnv] = {}
_cache_lock = threading.Lock()


class EngineEnvError(StokesError):
    """A bashrc could not be sourced, or the host has no usable engine."""

    code = "agent.engine_env"


class UnknownEngine(EngineEnvError):
    """The client named an engine this agent's own probe did not find.

    `agent.bad_params`, because the request is wrong, not the host.
    """

    code = "agent.bad_params"


#: An engine id on the wire. It names an install the agent itself found; it is never a path.
ENGINE_ID_RE = re.compile(r"^e_[0-9a-f]{12}$")


def engine_id(bashrc: str | Path) -> str:
    """The wire id of an install: a short digest of its bashrc path.

    **A client sends this, never the path.** The id carries no information a client could aim at a
    file: the agent maps it back through its own probe (`resolve_engine`), so naming a path that
    the agent did not find gets nothing. Deterministic, so an id survives an agent restart.
    """
    return "e_" + hashlib.sha256(str(bashrc).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class EngineEnv:
    """One install's captured environment."""

    bashrc: str
    """The absolute path that was sourced."""
    env: dict[str, str]
    """The filtered environment: `ENV_KEYS` plus every key starting with an `ENV_PREFIXES` entry."""
    project: str
    """``$WM_PROJECT``, e.g. ``OpenFOAM``."""
    version: str
    """``$WM_PROJECT_VERSION``, e.g. ``14``."""
    flavour: str
    """``org`` (Foundation), ``com`` (ESI) or ``unknown``."""
    seconds: float
    """Wall time the capture took."""
    native_tools: dict[str, str] = field(default_factory=dict)
    mpi_executable: str | None = None
    mpi_count_flag: str = "-np"


#: How each discovered bashrc was found, keyed by its path. **Populated as a side effect of the
#: scan**, because the source of a match is knowledge the scan has and nothing else does - a caller
#: could only re-derive it by repeating the search and guessing.
FOUND_BY: dict[Path, str] = {}


def bashrc_candidates() -> list[Path]:
    """Files that could be an OpenFOAM bashrc, in the order they should be tried.

    `STOKES_OPENFOAM_BASHRC` first when it names a regular file — it is how the dispatcher points
    the agent at an install whose environment script is not called ``bashrc`` (on the mesh host it
    is ``openfoam14-env.sh``, which exports the MPI prefixes before sourcing ``etc/bashrc``). Then
    ``<candidate>/etc/bashrc`` for every directory `openfoam_candidates` finds.
    """
    if platform.system() == "Windows":
        from .native_env import candidates

        found_native = candidates()
        configured = os.environ.get("STOKES_FOAMWIN_ENV")
        for path in found_native:
            FOUND_BY[path] = (
                "STOKES_FOAMWIN_ENV"
                if configured and path == Path(configured).resolve()
                else "standard-prefix"
            )
        return found_native
    found: list[Path] = []
    named = os.environ.get("STOKES_OPENFOAM_BASHRC")
    if named:
        path = Path(named)
        # **Absolute and a regular file only.** A relative path would resolve against whatever the
        # agent's cwd happens to be, and a directory would be sourced as the empty string.
        if path.is_absolute() and path.is_file():
            found.append(path)
            FOUND_BY[path] = "STOKES_OPENFOAM_BASHRC"
    for candidate in openfoam_candidates():
        path = Path(candidate) / "etc" / "bashrc"
        if path.is_file() and path not in found:
            found.append(path)
            FOUND_BY[path] = _scan_source(candidate)
    return found


def _scan_source(candidate: str) -> str:
    """Which scan found ``candidate``: `home-scan` or `standard-prefix`.

    **Two values rather than one**, because they mean different things to a user reading the list
    (claude-win2's 1330 answer): a system prefix was put there by an administrator, a home folder by
    the user - and the one under `~` is the one they may not remember installing. `HOME_GLOBS` in
    `probe.py` names the patterns that search the home folder, so the answer comes from the pattern
    rather than from a second guess about what a path looks like.
    """
    from .probe import HOME_GLOBS

    home = Path.home().as_posix()
    normalised = Path(candidate).as_posix()
    for pattern in HOME_GLOBS:
        # The pattern that produced this directory, compared as the expanded prefix it matched.
        prefix = Path(pattern).expanduser().as_posix().rstrip("*")
        if normalised.startswith(prefix) or normalised.startswith(home + "/opt/openfoam"):
            return "home-scan"
    return "standard-prefix"


def found_by(bashrc: str) -> str:
    """How an install was discovered, for the Settings list to show (the Settings spec §2).

    **A user adding a custom path needs to see whether it took.** `STOKES_OPENFOAM_BASHRC` is the
    override the launcher sets; `standard-prefix` is one of the globs under `/opt`, the home folder
    and the platform's own locations; `user-added` is a path the user typed in Settings, which this
    does not know about - the store marks that one itself.
    """
    return FOUND_BY.get(Path(bashrc), "standard-prefix")


def flavour_of(version: str) -> str:
    """``org``, ``com`` or ``unknown`` from a ``WM_PROJECT_VERSION``.

    Foundation versions are all digits or ``dev``; ESI's carry a leading ``v`` (``v2512``).
    Anything else is ``unknown`` rather than a guess, because the flavour decides whether the
    install is offered at all.
    """
    text = version.strip()
    if not text:
        return "unknown"
    if text.startswith("v") and text[1:].isdigit():
        return "com"
    if text.isdigit() or text == "dev":
        return "org"
    return "unknown"


def supports(flavour: str, version: str) -> bool:
    """Whether Stokes can drive this install. Only OpenFOAM Foundation 14 (ADR-0024)."""
    return flavour == "org" and version == "14"


def capture_env(bashrc: Path, timeout: float = 30.0) -> EngineEnv:
    """Source ``bashrc`` once and return the filtered environment it produced.

    Raises `EngineEnvError` when the path is not an absolute regular file, when the capture times
    out or exits non-zero, when ``WM_PROJECT_DIR`` is absent afterwards, or when the output is
    implausibly large.
    """
    if platform.system() == "Windows":
        from .native_env import capture

        return capture(bashrc, timeout)
    if not bashrc.is_absolute():
        raise EngineEnvError(
            f"the bashrc must be an absolute path; got {bashrc}",
            details={"bashrc": str(bashrc)},
        )
    if bashrc.is_dir():
        raise EngineEnvError(
            f"{bashrc} is a directory, not a file to source",
            details={"bashrc": str(bashrc)},
        )
    if not bashrc.is_file():
        raise EngineEnvError(f"{bashrc} is not a file", details={"bashrc": str(bashrc)})
    # A named bashrc may be called anything (the mesh host's is `openfoam14-env.sh`); the NAME rule
    # from
    # the brief applies to the globbed `etc/bashrc` entries, which `bashrc_candidates` already
    # enforces.
    argv = ["bash", "--noprofile", "--norc", "-c", _SOURCE_SCRIPT, "stokes-env", str(bashrc)]
    # A minimal environment for the sourcing shell itself: PATH to find bash, HOME for the profile
    # lookups a bashrc may do. No inherited OpenFOAM variables, so `env -0` reports what the bashrc
    # set.
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, no shell, no data in the script
            argv,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_sourcing_shell_env(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineEnvError(
            f"sourcing {bashrc} timed out after {timeout:g}s",
            details={"bashrc": str(bashrc), "timeout": timeout, "stderr": _tail(exc.stderr)},
        ) from exc
    except OSError as exc:
        raise EngineEnvError(
            f"could not run bash to source {bashrc}: {exc}",
            details={"bashrc": str(bashrc), "error": str(exc)},
        ) from exc
    seconds = time.monotonic() - started
    if completed.returncode != 0:
        raise EngineEnvError(
            f"sourcing {bashrc} exited {completed.returncode}",
            details={
                "bashrc": str(bashrc),
                "returncode": completed.returncode,
                "stderr": _tail(completed.stderr),
            },
        )
    if len(completed.stdout) > MAX_CAPTURE_BYTES:
        raise EngineEnvError(
            f"sourcing {bashrc} produced {len(completed.stdout)} bytes, over the "
            f"{MAX_CAPTURE_BYTES}-byte limit",
            details={"bashrc": str(bashrc), "bytes": len(completed.stdout)},
        )
    env = _parse(completed.stdout)
    # **`WM_PROJECT_DIR` is the test that the source worked**, not the exit code: `bashrc` can exit
    # 0
    # having found nothing, and every later call would then fail one at a time instead of here.
    if "WM_PROJECT_DIR" not in env:
        raise EngineEnvError(
            f"sourcing {bashrc} left no WM_PROJECT_DIR: the file is not an OpenFOAM bashrc",
            details={"bashrc": str(bashrc), "stderr": _tail(completed.stderr), "keys": len(env)},
        )
    project = env.get("WM_PROJECT", "")
    version = env.get("WM_PROJECT_VERSION", "")
    return EngineEnv(
        bashrc=str(bashrc),
        env=env,
        project=project,
        version=version,
        flavour=flavour_of(version),
        seconds=seconds,
    )


def _sourcing_shell_env() -> dict[str, str]:
    """The environment for the shell that sources the install's bashrc.

    **OpenFOAM's own bashrc assumes it is sourced from a shell that already has the MPI runtime
    on its library path.** Measured on this Linux host: sourcing only ``etc/bashrc`` leaves
    ``blockMesh -help`` failing with ``libmpi.so.40: cannot open shared object file`` (exit 127),
    while the same call works when the caller had sourced the install's env script first. So keep
    the caller's ``PATH`` and ``LD_LIBRARY_PATH``, plus the OpenMPI relocation prefixes a
    user-space build needs, and nothing else: no ``BASH_ENV``, no ``WM_*``/``FOAM_*`` from a
    previous install, no secrets.
    """
    user = os.environ.get("USER") or getpass.getuser()
    base = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()),
        # **OpenFOAM's bashrc builds `WM_PROJECT_USER_DIR` from `$USER`.** Measured: without it
        # the captured `FOAM_USER_APPBIN` was `~/OpenFOAM/-14/...`, so a cfMesh built into the user
        # prefix was invisible even though the launching shell had it on PATH.
        "USER": user,
        "LOGNAME": os.environ.get("LOGNAME") or user,
    }
    inherited = ("LD_LIBRARY_PATH", "OPAL_PREFIX", "PMIX_PREFIX", "PRTE_PREFIX", "PRTE_PKGDATADIR")
    for key in inherited:
        value = os.environ.get(key)
        if value:
            base[key] = value
    return base


def _tail(raw: bytes | None, limit: int = 2048) -> str:
    """The last ``limit`` bytes of a captured stream, decoded for a human to read."""
    if not raw:
        return ""
    return raw[-limit:].decode("utf-8", errors="replace")


def _parse(stdout: bytes) -> dict[str, str]:
    """The filtered environment from ``env -0`` output.

    Split on NUL rather than on newlines: a value containing a newline is legal in an environment
    and would otherwise become two entries, the second of which is not a key at all.
    """
    env: dict[str, str] = {}
    for chunk in stdout.split(b"\0"):
        if not chunk:
            continue
        key_raw, sep, value_raw = chunk.partition(b"=")
        if not sep:
            continue
        key = key_raw.decode("utf-8", errors="replace")
        if key in ENV_KEYS or key.startswith(ENV_PREFIXES):
            env[key] = value_raw.decode("utf-8", errors="replace")
    return env


def utilities(env: EngineEnv) -> dict[str, str | None]:
    """Every name in `UTILITIES` mapped to its absolute path on this install, or None.

    ``shutil.which`` against the **captured** PATH, not this process's: the whole point of the
    capture is that the utilities are only reachable through it.
    """
    if env.native_tools:
        return {name: env.native_tools.get(name) for name in UTILITIES}
    path = env.env.get("PATH", "")
    return {name: shutil.which(name, path=path) for name in UTILITIES}


def resolve_utility(env: EngineEnv, name: str) -> str | None:
    """Native tools come only from the captured install, never the job's directory."""
    if env.native_tools:
        path = env.native_tools.get(name)
        return path if path and Path(path).is_file() else None
    return shutil.which(name, path=env.env.get("PATH", ""))


def mpi_launcher(env: EngineEnv) -> str | None:
    """Only a captured native launcher or MPI on the captured POSIX PATH is eligible."""
    if env.native_tools:
        path = env.mpi_executable
        return path if path and Path(path).is_file() else None
    return shutil.which("mpiexec", path=env.env.get("PATH", ""))


def mpi_flags(env: EngineEnv) -> tuple[str, ...]:
    """Extra arguments the captured MPI launcher needs, from the install's own environment.

    **OpenMPI 5 / PRRTE counts physical cores, not `nproc`.** Measured on this four-thread host:
    `nproc` is 4 but `mpiexec -n 4` refuses with "not enough slots", while
    `mpiexec --oversubscribe -n 4` launches four ranks. The flag is safe when slots are
    sufficient, and the `OMPI_*` variables the install's env exports are what identifies OpenMPI
    without running `--version`; MS-MPI and MPICH get no extra argument.
    """
    if env.native_tools:
        return ()
    # The `OMPI_*` variables are one marker; OpenFOAM's own `WM_MPLIB`/`FOAM_MPI` are the other,
    # and they are present on a stock install whose bashrc sets no `OMPI_*` at all.
    mpi = (env.env.get("WM_MPLIB", "") + env.env.get("FOAM_MPI", "")).upper()
    if any(key.startswith("OMPI_") for key in env.env) or "OPENMPI" in mpi:
        return ("--oversubscribe",)
    return ()


def mpi_unavailable_reason(env: EngineEnv) -> str:
    """An actionable platform-specific refusal, also suitable for the settings UI."""
    if env.native_tools:
        return (
            "Parallel runs require Microsoft MPI and an MPI-enabled FoamWin install. "
            "Install the Microsoft MPI runtime (msmpisetup.exe) from "
            "https://www.microsoft.com/en-us/download/details.aspx?id=105289, "
            "then restart Stokes."
        )
    return "MPI is unavailable: install an MPI runtime compatible with this OpenFOAM install."


def vtk_version() -> str | None:
    """The installed VTK version, or None. Imported here so the agent starts without VTK loaded."""
    try:
        from vtkmodules.vtkCommonCore import vtkVersion

        return str(vtkVersion.GetVTKVersion())
    except Exception:
        return None


def cached_env(bashrc: str) -> EngineEnv:
    """The captured `EngineEnv` for one bashrc, capturing it once and remembering it.

    Guarded by a lock because `host.probe` runs on a worker thread and two concurrent probes must
    not source the same file twice.
    """
    with _cache_lock:
        found = _cache.get(bashrc)
    if found is not None:
        return found
    captured = capture_env(Path(bashrc))
    with _cache_lock:
        _cache.setdefault(bashrc, captured)
        return _cache[bashrc]


def clear_cache() -> None:
    """Forget every captured environment. For tests and for `host_record(refresh=True)`."""
    with _cache_lock:
        _cache.clear()


def resolve_engine(engine: str | None = None) -> EngineEnv:
    """The environment for an ENGINE ID from the wire, or for the default install when None.

    **The only way a request reaches a bashrc.** The id is looked up among the installs this
    agent's own probe finds (`bashrc_candidates`); an id that is malformed, unknown or names an
    install Stokes cannot drive is refused with `agent.bad_params`. A path is never accepted: a
    client that could name a path could make the agent `source` any file it can read or write.
    """
    if engine is None:
        return get_env(None)
    if not isinstance(engine, str) or not ENGINE_ID_RE.match(engine):
        raise UnknownEngine(
            f"{str(engine)[:64]!r} is not an engine id; send an id from host.probe, never a path",
            details={"engine": str(engine)[:64]},
        )
    known: list[str] = []
    for path in bashrc_candidates():
        found = engine_id(path)
        known.append(found)
        if found != engine:
            continue
        env = cached_env(str(path))
        if not supports(env.flavour, env.version):
            raise UnknownEngine(
                f"engine {engine} is {_unsupported_why(env)}",
                details={"engine": engine},
            )
        return env
    raise UnknownEngine(
        f"no engine {engine} on this host; known: {', '.join(known) or 'none'}",
        details={"engine": engine, "known": known},
    )


def get_env(bashrc: str | None = None) -> EngineEnv:
    """The environment for ``bashrc``, or for the default install when None.

    **Internal: a PATH must never come from the wire.** The job supervisor calls
    `resolve_engine`, which maps a client's engine id onto the installs the agent found itself.

    T-144a calls this before launching a utility. Raises `EngineEnvError` with code
    ``agent.no_engine`` when the host has nothing to drive, so the caller can report "no engine on
    this host" rather than failing later with a missing-binary error.
    """
    if bashrc is not None:
        return cached_env(bashrc)
    default = default_bashrc()
    if default is None:
        raise EngineEnvError(
            "no supported OpenFOAM install on this host",
            details={
                "code": "agent.no_engine",
                "candidates": [str(p) for p in bashrc_candidates()],
            },
        )
    return cached_env(str(default))


def default_bashrc() -> Path | None:
    """The first supported install's bashrc, or None."""
    for path in bashrc_candidates():
        try:
            env = cached_env(str(path))
        except EngineEnvError:
            continue
        if supports(env.flavour, env.version):
            return path
    return None


def _install_record(bashrc: Path, refresh: bool) -> dict[str, Any]:
    """One entry of ``installs``. **A failed capture is an entry, not an exception**."""
    try:
        env = capture_env(bashrc) if refresh else cached_env(str(bashrc))
    except EngineEnvError as exc:
        return {
            "id": engine_id(bashrc),
            "bashrc": str(bashrc),
            "project": "",
            "version": "",
            "flavour": "unknown",
            "supported": False,
            "why": exc.message,
            "seconds": 0.0,
            "utilities": {},
        }
    ok = supports(env.flavour, env.version)
    return {
        "id": engine_id(env.bashrc),
        "bashrc": env.bashrc,
        "project": env.project,
        "version": env.version,
        "flavour": env.flavour,
        "supported": ok,
        # **How it was found** (the Settings spec §2): "standard prefix / `STOKES_OPENFOAM_BASHRC` /
        # user-added". A user looking at a list of auto-detected installs needs to know whether one
        # came from the environment the launcher set or from a scan - the first is configuration
        # somebody chose, the second happened to be on the disk.
        "found_by": found_by(env.bashrc),
        "why": "" if ok else _unsupported_why(env),
        "seconds": round(env.seconds, 3),
        "utilities": utilities(env),
        # What the install can run (ADR-0029, T-304): solver modules, applications, libraries and
        # etc files, listed from its captured FOAM_* folders. Nested so it does not collide with
        # `utilities` above, which maps the allow-listed tools to their paths.
        "inventory": install_inventory(env.env),
        "parallel": {
            "available": mpi_launcher(env) is not None,
            "backend": "msmpi" if env.native_tools else "mpi",
            "max_ranks": 256,
        },
    }


def _unsupported_why(env: EngineEnv) -> str:
    """One sentence saying why an install cannot be driven."""
    if env.flavour == "com":
        return f"OpenFOAM {env.version} (ESI) found; Stokes supports OpenFOAM Foundation 14"
    if env.flavour == "org" and env.version != "14":
        return f"OpenFOAM Foundation {env.version or 'unknown'} found; Stokes supports version 14"
    return f"OpenFOAM {env.version or 'unknown'} found; Stokes supports OpenFOAM Foundation 14"


def host_record(refresh: bool = False) -> dict[str, Any]:
    """What this machine has: the base probe plus every OpenFOAM install it can drive (ADR-0024).

    ``installs`` is never empty-but-silent: an install whose capture failed is still listed with
    ``supported: false`` and the reason, because "found something I cannot use" and "found
    nothing" are different answers and the desktop shows the difference.
    """
    if refresh:
        clear_cache()
    record: dict[str, Any] = dict(probe_host())
    # **The VTK version, which the base probe does not report.** It is here rather than in
    # `probe_host` because that function's shape is relied on by `tests/test_agent.py` (T-140: keep
    # it
    # unchanged), and because the import is a heavy one the agent should not do at module load.
    # Measured: this host has VTK **9.6.2**, so a `None` here means the import failed, not that VTK
    # is
    # absent — which is the difference between a client hiding its field display and showing it.
    record["vtk"] = vtk_version()
    # The agent's own name and version, which the schema asks for. Taken from `server.AGENT_NAME` so
    # there is one spelling of it: a second literal here would drift from the handshake's.
    from .server import AGENT_NAME

    record["agent"] = AGENT_NAME
    installs = [_install_record(path, refresh) for path in bashrc_candidates()]
    supported = [entry for entry in installs if entry["supported"]]
    record["installs"] = installs
    record["default_install"] = supported[0]["bashrc"] if supported else None
    # **What a client sends as `install`.** An id, never the path above, which is for display.
    record["default_install_id"] = supported[0]["id"] if supported else None
    record["why"] = "" if supported else _record_why(installs)
    # **The default install's inventory at the top level** (T-304): `solver_modules`, `utilities`,
    # `libraries`, `etc_functions`, `etc_case_dicts` and `tutorials_root`. With no supported
    # install, the agent's own FOAM_* environment is listed instead (empty when it has none), so the
    # keys are always present and a client never has to tell "absent" from "empty".
    inventory = supported[0].get("inventory") if supported else None
    record.update(inventory if isinstance(inventory, dict) else install_inventory(os.environ))
    return record


def _record_why(installs: list[dict[str, Any]]) -> str:
    """One sentence for a host with no supported install, saying which case it is."""
    if not installs:
        return (
            "No OpenFOAM install found on this host; mesh on a host that has one (Settings → Hosts)"
        )
    return str(installs[0]["why"]) or "No supported OpenFOAM install on this host"
