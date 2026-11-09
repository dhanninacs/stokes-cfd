"""The job supervisor: stage files, run steps, cancel, log, recover orphans (ADR-0005, T-144a).

## What this is, and what it is not

The agent, not the desktop, owns every process that touches the engine host. This module is the
supervisor **inside** the agent: plain Python, one daemon thread per job, no protocol and no desktop
code. T-144b puts it on the wire; T-144c starts the agent in-process.

The path and identifier rules live next door in `jobpaths`, because they are the security-relevant
half and read better without the process code around them.

## Why it is written defensively

This is the code that turns a client's request into a running program, so a bug here is a
vulnerability rather than an annoyance. Four rules shape every method:

* **argv lists only**, never a command string, and ``argv[0]`` must be a bare name from
  `ALLOWED_UTILITIES` — the desktop can never name another program, and never a path.
* **Every path is validated then contained**: see `jobpaths`.
* **The engine environment is captured once** (`engine_env.get_env`) and passed explicitly; the
  child never inherits this process's environment, so a variable the desktop set cannot reach a
  solver.
* **A job never outlives the agent that started it** (ADR-0024). `recover()` kills what a previous
  agent left behind rather than adopting it.

## The cancel path is a process GROUP

`start_new_session=True` makes each step the leader of a new process group, and cancel signals the
**group**. That matters because OpenFOAM utilities spawn children — `mpirun`, decomposition
workers — and killing only the parent leaves a solver running that holds the case directory
open. The test
asserts the *grandchild* is gone, which is the only way to tell the two implementations apart.

## Exit codes, measured rather than assumed

On this host, `blockMesh` with a broken dict exits **1**, `foamRun` with a missing field exits
**1**, and `foamRun` killed with `SIGTERM` exits **143**. None exits 0 after `FOAM FATAL ERROR`,
so the exit code is trustworthy and there is deliberately **no log-string check** on top of it —
a string check
would also fail jobs whose output merely mentions the phrase.
See `.coordination/T200-INVENTORY.md`.

## Length

Over 700 lines, which the coding standard says needs a reason: the rules above are one object's
invariants, and the supervisor is the smallest unit that can enforce them — a job and its
thread, its state file and its process group all change together. The separable part was
extracted to `jobpaths`."""

from __future__ import annotations

import contextlib
import inspect
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from stokes.errors import StokesError
from stokes.log import fields, get_logger

from .engine_env import (
    EngineEnv,
    UnknownEngine,
    engine_id,
    mpi_launcher,
    mpi_unavailable_reason,
    resolve_engine,
    resolve_utility,
)
from .flags import ALLOWED_FLAGS, FORBIDDEN_FLAGS, POSITIONAL
from .flags_external import ExternalFlagRefused, check_external_argv
from .jobpaths import (
    EXTERNAL_TOP_RE,
    JOB_ID_RE,
    STAGED_DIRS,
    STEP_NAME_RE,
    JobPathError,
    check_job_id,
    check_relative,
    check_staged_path,
    has_symlink,
    job_path,
    result_path,
    tree_bytes,
)
from .lint import POLYMESH_PREFIX, Finding, lint_file, lint_job
from .lint_external import ProbedInstall, lint_file_external, lint_job_external
from .parsers import PARSERS

log = get_logger(__name__)

#: The programs a job may run. **A bare name, never a path**: `shutil.which` resolves it against the
#: captured engine PATH, so the workspace's `bin` is the only place a utility can come from.
#:
#: `foamToVTK` is deliberately absent: VTK reads the case directory directly (T-209).
ALLOWED_UTILITIES = frozenset(
    {
        "blockMesh",
        "checkMesh",
        "snappyHexMesh",
        "surfaceFeatures",
        "foamRun",
        "decomposePar",
        "reconstructPar",
        # The 2D snappy recipe (ADR-0031 decision 5): extrude the slab's front to one layer; it
        # reads `system/extrudeMeshDict` and makes front and back `empty` itself.
        "extrudeMesh",
        # **The cfMesh family and the polyhedral dual** (ADR-0032): each reads only the case's own
        # `system/meshDict` (or a feature angle) and writes `constant/polyMesh`, exactly as
        # blockMesh and snappyHexMesh do, so they sit behind the same flag list and lint.
        "cartesianMesh",
        "tetMesh",
        "pMesh",
        "polyDualMesh",
        # **The polyhedral mesher's symmetry planes** (`stokes.mesh.drivers.cfmesh`): flatten them,
        # then set each back to `symmetry`. `foamDictionary`'s arguments are pinned to that one
        # edit of `constant/polyMesh/boundary` (`flags.py`).
        "improveSymmetryPlanes",
        "foamDictionary",
    }
)

#: The utilities that may be given OpenMP threads (`Step.threads`, ADR-0032). cfMesh is
#: OpenMP-parallel in one process; measured on this host, tetMesh on the demo pipe took 16.8 s
#: with one thread and 13.2 s with four. Without a value the runtime takes every logical core,
#: which ignores the user's `cores` setting - so the step names its count and the agent sets
#: `OMP_NUM_THREADS` for that process only.
THREADED_UTILITIES = frozenset({"cartesianMesh", "tetMesh", "pMesh"})


#: The job profiles (ADR-0029 decision 5). `None` is a case Stokes wrote: `ALLOWED_UTILITIES`,
#: `flags.py` and `lint.lint_job`. `external` is a case the user brought (an imported tutorial):
#: `flags_external`, `lint_external` and the time-directory staging rule, **each checked
#: against this agent's own probe of the install** (`probe.install_inventory`), never a list from
#: the client.
PROFILES: tuple[str | None, ...] = (None, "external")
EXTERNAL = "external"

#: The utilities an external step may run over MPI (`ranks > 1`).
EXTERNAL_MPI = frozenset({"foamRun", "foamMultiRun", "snappyHexMesh", "checkMesh"})

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_LOG_BYTES = 64 * 1024 * 1024

#: The most one job's folder may hold.
#:
#: **A quota, because there was none** (Opus F6). `MAX_FILE_BYTES` bounds ONE file and nothing
#: bounded the folder: a 500 MiB surface plus a case plus artifacts is over 1 GiB, and `job.create`
#: accepted as many jobs as a client sent. 1 GiB is the owner's number and is comfortably above a
#: real mesh case (the demo's 153 k-cell mesh is ~36 MiB).
MAX_JOB_BYTES = 1024 * 1024 * 1024

#: Free space that must remain AFTER a write, so a job cannot fill the disk it shares.
#:
#: **The check neither disk incident had.** `.coordination/DISK-FULL.md` records the first (a 4 M
#: cell probe writing hundreds of MiB per case) and 2026-10-16 the second; in both, the agent wrote
#: until the filesystem refused, taking the tooling down with it. Refusing BEFORE the write turns
#: "the disk is full and nothing works" into a message naming the request and the room left.
#: 1 GiB of reserve is enough for the OS, the log files and the editor to keep working.
DISK_RESERVE_BYTES = 1024 * 1024 * 1024


def _free_bytes(path: Path) -> int:
    """Free space on the filesystem holding ``path``.

    **Zero when it cannot be read**, which is the safe direction: a caller comparing against a
    reserve treats zero as "cannot prove there is room" and refuses. Reporting a large number on an
    unreadable path would let the write proceed on exactly the check that exists to stop it.
    """
    try:
        return shutil.disk_usage(path).free
    except OSError:  # pragma: no cover - an unreadable or absent root
        return 0


def _total_bytes(files: dict[str, bytes | str]) -> int:
    """The staged size of a job's files, counted as the agent will write them (UTF-8)."""
    total = 0
    for content in files.values():
        total += len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
    return total


#: The utilities whose output in `constant/polyMesh` is the agent's own work (FIXES-B B5). A file
#: one of them wrote is recorded in the job's record and is not scanned as a client's text would be.
MESHERS = frozenset(
    {
        "blockMesh",
        "snappyHexMesh",
        "reconstructPar",
        "extrudeMesh",
        "cartesianMesh",
        "tetMesh",
        "pMesh",
        "polyDualMesh",
        "improveSymmetryPlanes",
        "foamDictionary",
    }
)
MAX_ARGV_ITEM = 1024
MAX_LINE_CHARS = 4096
MAX_TAIL_LINES = 5000

#: The most `read` will return, whatever the caller asks for.
MAX_READ_BYTES = 1024 * 1024
#: How deep and how many files `outputs` lists under `postProcessing/`.
MAX_OUTPUT_DEPTH = 6
MAX_OUTPUT_FILES = 500
TAIL_READ_BYTES = 1024 * 1024

#: `created` is named `queued` by the ADR-0024 correction: the word says the job is waiting, where
#: "created" could be read as "ready to run".
#: The file actions a step may take, and the paths each one needs. `copy` is the only one with a
#: source; the others act on a single path. Nothing else touches a folder.
_ACTION_PATHS: dict[str, tuple[str, ...]] = {
    "mkdir": ("target",),
    "remove": ("target",),
    "copy": ("source", "target"),
}
_ACTION_KEYS = ("source", "target")
#: The mesh action (`stokes_agent.flatten`): snap a planar symmetry patch onto its plane, as far as
#: the cells around it allow. Takes a patch name and nothing else, and any job may use it.
FLATTEN_OP = "flatten"
FLATTEN_PATCH_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
STATES = ("queued", "running", "cancelling", "done", "failed", "cancelled", "interrupted")

#: The states a job never leaves.
TERMINAL_STATES = ("done", "failed", "cancelled", "interrupted")

#: Native Windows supervises every step in a kill-on-close Job Object. A module constant, not
#: `os.name`, because the supervisor's own tests stand in for POSIX process groups on Windows.
NATIVE_WINDOWS = os.name == "nt"


#: Where the job RECORDS live, under the jobs root and **outside every job folder**. A record names
#: the argv, the engine, the pids and the state; it is written only by the agent, so nothing a
#: client can put into a job folder can change what runs or whom `cancel` signals.
RECORDS_DIR = ".records"


def _posix_host() -> bool:
    """Whether this host can run jobs. A function so a test can stand in for a POSIX host."""
    return os.name != "nt"


def _no_process_groups(*_args: object) -> Any:
    """Stand-in for a POSIX-only call.

    The agent runs on Linux, and a Windows checkout only imports it.
    """
    raise RuntimeError("process groups need a POSIX host")


# Looked up by name, not written as `os.killpg`, so a Windows checkout can import this module (and
# type-check it) and a POSIX host gets the real calls. Tests that need them skip off POSIX.
_getpgid: Callable[[int], int] = getattr(os, "getpgid", _no_process_groups)
_killpg: Callable[[int, int], None] = getattr(os, "killpg", _no_process_groups)
_SIGKILL: int = int(getattr(signal, "SIGKILL", 9))
_getpgrp: Callable[[], int] = getattr(os, "getpgrp", lambda: -1)


def _own_group() -> int:
    """The agent's own process group, which no cancel may ever signal."""
    try:
        return int(_getpgrp())
    except OSError:
        return -1


def _group_exists(pgid: int) -> bool:
    """Whether a process group still has a member. Signal 0 only tests."""
    if NATIVE_WINDOWS:
        from .windows_process import owns

        return owns(pgid)
    try:
        _killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _signal_group(pgid: int, sig: int) -> None:
    """Signal a process group, ignoring one that is already gone or is not ours to signal."""
    if NATIVE_WINDOWS:
        from .windows_process import terminate

        terminate(pgid)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        _killpg(pgid, sig)


#: How long a cancel waits for the group to die before `SIGKILL`.
CANCEL_GRACE_S = 5.0

#: The file `job.stop` creates in a running case: what `#includeFunc stopAtFile(action=writeNow)`
#: watches. **The name is the call text** — `stopAtFile` watches `$FOAM_CASE/<function name>`,
#: and an `#includeFunc` with arguments is named by its call text, the same rule that names the
#: monitors' `postProcessing/` folders. `stokes.compile.of14.control.STOP_FUNC` writes exactly
#: this text, and a test pins the two together. The solver writes the current iteration and
#: ends cleanly (exit 0), so the job's later steps (reconstructPar) still run and the result can
#: be read and resumed.
STOP_FILE = "stopAtFile(action=writeNow)"

#: How long a `job.stop` waits for the solver to write and end before it cancels the job instead.
#: One time step of a large mesh can take tens of seconds, so this is generous.
STOP_GRACE_S = 60.0

#: Log streaming: at most one `log.lines` per job per this many seconds, and this many lines in one.
LOG_EVENT_INTERVAL_S = 0.1
LOG_EVENT_MAX_LINES = 500

#: The most lines held in memory waiting to be sent (FIXES-B B7). A step that prints faster than the
#: wire can carry loses the OLDEST lines from the stream, counts them, and reports the count in the
#: next event's ``dropped``; the file keeps every line (up to `MAX_LOG_BYTES`) for `tail`.
LOG_RING_LINES = 20_000

#: The most of a log read in one go, so a step that writes fast cannot make one read as big as the
#: log. The rest is read on the next pass.
LOG_READ_CHUNK_BYTES = 1024 * 1024

#: What a log ends with when it was cut at `MAX_LOG_BYTES`, so a reader knows the step wrote more.
LOG_TRUNCATED_MARKER = "[stokes: the log was truncated here; the step wrote more than the limit]"

LOG_SIZE_CHECK_S = 0.5

#: At most one parser event per NAME per job per this many seconds; the rest are coalesced.
PARSER_EVENT_INTERVAL_S = 0.1

#: How long `create` waits for the engine environment. Sourcing a bashrc is a process launch.
_ENGINE_TIMEOUT_S = 30.0


class JobError(StokesError):
    """A job operation was refused, or a job failed to run."""

    code = "agent.job"


class JobBadParams(JobError):
    """The request itself is invalid: a bad id, a path outside the job, a utility not allowed."""

    code = "agent.bad_params"


class JobUnsupported(JobError):
    """The host cannot supervise native or POSIX processes."""

    code = "agent.unsupported"


class JobUnsafeDict(JobError):
    """A staged dictionary contains a directive or a word that is refused. The message names the
    file, the line and the token."""

    code = "agent.unsafe_dict"


class JobNotFound(JobError):
    """No job with that id. A distinct code from a malformed id: one is a typo, the other a bug."""

    code = "agent.not_found"


class JobBusy(JobError):
    """The job is running, so the request cannot be honoured — a delete, or a second start."""

    code = "agent.busy"


class JobMissingUtility(JobError):
    """An allow-listed utility is not on the captured engine PATH."""

    code = "agent.missing_utility"


class JobTooLarge(JobError):
    """The request is larger than a limit allows.

    **A class, so the code is the class's** (`agent.too_large`). Four refusals in this module put
    `{"code": "agent.too_large"}` in `details`, which does nothing: `StokesError.code` is a CLASS
    attribute, so a client received `agent.job` for every one of them - and `server.py`'s mapping
    from `agent.too_large` to `AgentTooLarge` could never fire. Measured while adding the quota.
    """

    code = "agent.too_large"


class JobNoSpace(JobError):
    """The host does not have the space this job needs, with the reserve kept back.

    **Its own code so the front end can say something useful**: "this machine has 2 GiB free and the
    case needs 3" is actionable in a way a generic job failure is not.
    """

    code = "agent.no_space"


@dataclass(frozen=True)
class Step:
    """One program to run, in the job's order.

    ``parse`` names a log parser (T-202b registers them); the supervisor does not know any
    itself, so a step naming one is refused at `create` rather than failing halfway.
    """

    name: str
    argv: tuple[str, ...]
    timeout_s: int = 3600
    parse: str | None = None
    parse_params: dict[str, Any] = field(default_factory=dict)
    ranks: int = 1
    #: OpenMP threads for a `THREADED_UTILITIES` step (1..256); 1 leaves the variable unset.
    threads: int = 1
    #: A file action instead of a program: ``{"op": "mkdir"|"remove"|"copy", "source", "target"}``.
    #: Only an `external` job takes one, and then ``argv`` is empty. See `_run_action`.
    action: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "argv": list(self.argv),
            "timeout_s": self.timeout_s,
            "parse": self.parse,
            "parse_params": self.parse_params,
            "ranks": self.ranks,
            "threads": self.threads,
            "action": self.action,
        }


def _action_rel(rel: str, profile: str | None) -> str:
    """A file action's path, checked for shape: a staged folder, or a path inside the job.

    **A folder is allowed**, because an Allrun's `cp -r 0.orig 0` names one. What is refused is
    anything outside the folders a solver reads: the agent's own files sit at the job's top level.
    """
    try:
        checked = check_relative(rel)
    except JobPathError as exc:
        raise JobBadParams(str(exc), details={"path": str(rel)}) from exc
    top = checked.split("/", maxsplit=1)[0]
    external_top = profile == EXTERNAL and EXTERNAL_TOP_RE.match(top) is not None
    if top not in STAGED_DIRS and not external_top:
        raise JobBadParams(f"{checked!r} is not a staged folder", details={"path": checked})
    return checked


def _action_path(job_dir: Path, rel: str, profile: str | None) -> Path:
    """`_action_rel`, then contained in the job folder on the resolved path, like `job_path`."""
    checked = _action_rel(rel, profile)
    try:
        return job_path(job_dir, checked)
    except JobPathError as exc:
        raise JobBadParams(str(exc), details={"path": checked}) from exc


def default_root() -> Path:
    """Where jobs live: ``$STOKES_AGENT_HOME/jobs``, else ``~/.stokes/jobs``."""
    home = os.environ.get("STOKES_AGENT_HOME")
    base = Path(home) if home else Path.home() / ".stokes"  # agent-side default
    return base / "jobs"


def _now() -> str:
    """An ISO-8601 UTC timestamp, which is what `state.json` records."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _time_dirs(job_dir: Path) -> list[str]:
    """A case's written time folders, oldest first: top-level numeric directories other than `0`.

    Names come from `EXTERNAL_TOP_RE` (`100`, `0.5`, `1e-05`), so `0.orig`, the agent's own
    entries and anything with a path separator never qualify; a symlinked folder is skipped.
    """
    found: list[tuple[float, str]] = []
    try:
        entries = list(job_dir.iterdir())
    except OSError:
        return []
    for entry in entries:
        name = entry.name
        if name == "0.orig" or EXTERNAL_TOP_RE.match(name) is None:
            continue
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            value = float(name)
        except ValueError:
            continue
        if value > 0:
            found.append((value, name))
    return [name for _, name in sorted(found)]


def _latest_time_dir(job_dir: Path) -> str | None:
    """The newest written time folder of a case, or None when it wrote none."""
    times = _time_dirs(job_dir)
    return times[-1] if times else None


def _proc_start_ticks(pid: int) -> int | None:
    """Field 22 of ``/proc/<pid>/stat``: when the process started, in clock ticks.

    **The reason a pid alone is not enough.** Pids are reused, so "a process with this pid is
    alive" can be true of a different program entirely. Recording the start time with the pid
    makes the pair
    unique, and `recover` refuses to kill on a mismatch rather than killing an innocent process.
    """
    if NATIVE_WINDOWS:
        from .windows_process import process_start

        return process_start(pid)
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # The comm field is parenthesised and may contain spaces or a `)`, so split after the LAST `)`.
    close = raw.rfind(")")
    if close == -1:
        return None
    parts = raw[close + 2 :].split()
    # After comm, field 3 is state, so field 22 is index 19 of the remainder.
    if len(parts) < 20:
        return None
    try:
        return int(parts[19])
    except ValueError:
        return None


#: Utilities that read a `featureEdgeMesh` (`constant/geometry/<surface>.eMesh`).
#:
#: **Measured: OpenFOAM 14 on native Windows cannot open one through an absolute path.**
#: `edgeMeshFormat::read` builds an `IOobject` from the file name and its `headerOk` check fails
#: when that name starts with a drive letter, so `snappyHexMesh` aborts with "Cannot read file
#: .../constant/geometry/walls.eMesh" on a file that exists and that the same build reads as soon
#: as the case is named relatively. Every real Stokes part stages one surface per boundary tag and
#: therefore has a non-empty `features` list, so this affects every snappy job, serial or parallel.
FEATURE_READERS = ("snappyHexMesh",)


#: The utilities that read every field of a time folder without the solver's libraries loaded.
SPLITTERS = frozenset({"decomposePar", "reconstructPar"})
#: The generic patch fields: OpenFOAM reads a patch type it does not know through them.
GENERIC_FIELDS_LIBS = '("libgenericFvFields.dll")'


def _launch_libs(utility: str, job_dir: Path) -> list[str]:
    """The `-libs` the agent adds itself at launch: `_split_libs`, for the serial utilities only.

    **Never for an MPI step.** Measured on FoamWin (2026-11-08): `-libs '("libfvConstraints.dll")'`
    on a parallel foamRun lost its quotes through mpiexec's own command-line parsing and the
    solver stopped at "wrong token type - expected string". It was not needed either: the
    `fluid` solver read the energy case's `limitTemperature` constraint without it. ``job_dir``
    is kept for a rule that needs the case's files.
    """
    del job_dir
    return _split_libs(utility)


def _split_libs(utility: str) -> list[str]:
    """`-libs` for decomposePar and reconstructPar on native Windows; nothing anywhere else.

    **Measured on FoamWin (2026-11-08):** a parallel energy run of the mixing elbow failed at
    decomposePar with "Unknown patchField type compressible::alphatJayatillekeWallFunction", and a
    tutorial at pitzDaily's unused `0/v2` (`v2WallFunction`). Linux OpenFOAM reads a type it has no
    library for through the generic patch fields, which the MinGW build does not load on its own.
    **The agent adds the flag itself, with this fixed name**, as it adds `-parallel` and `-case`:
    `-libs` stays refused from every client (`flags.FORBIDDEN_FLAGS`), so no caller can choose a
    library. The library only changes how an unknown patch type is read; a known one is unchanged.
    """
    if os.name != "nt" or utility not in SPLITTERS:
        return []
    return ["-libs", GENERIC_FIELDS_LIBS]


def _native_case(name: str, argv: list[str], cwd: Path) -> tuple[list[str], Path]:
    """Name a feature reader's case relatively on native Windows, and run from its parent.

    The job id is validated and contains no spaces, and the agent root is required to have none
    either (the native handshake refuses a job directory with whitespace), so `-case <job id>` is
    a safe relative path. Everything else keeps the job directory as its cwd, exactly as before,
    and on POSIX nothing changes at all.
    """
    if not NATIVE_WINDOWS or name not in FEATURE_READERS:
        return argv, cwd
    return [*argv, "-case", cwd.name], cwd.parent


def _launch(
    argv: list[str], cwd: Path, env: dict[str, str], output: IO[bytes]
) -> subprocess.Popen[bytes]:
    """Start one step, confined before it executes on a native Windows host.

    A POSIX host keeps the process group `cancel` signals. Windows has no process
    group equivalent that survives a launcher exiting, so the child is assigned to
    a kill-on-close Job Object by `windows_process`.
    """
    if NATIVE_WINDOWS:
        from .windows_process import launch

        return launch(argv, cwd, env, output)
    return subprocess.Popen(  # noqa: S603 - validated argv, captured environment
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _step_env(env: Mapping[str, str], step: Step) -> dict[str, str]:
    """The captured engine environment for one step, plus its OpenMP thread count.

    **`OMP_NUM_THREADS` is set only for a threaded step and always explicitly.** The captured
    environment carries no OpenMP variable, and an unset one lets the runtime take every logical
    core - a cfMesh step on a 24-core host would ignore the `cores` the user chose. Setting `1`
    for a one-thread step keeps that true as well.
    """
    out = dict(env)
    if step.argv and step.argv[0] in THREADED_UTILITIES:
        out["OMP_NUM_THREADS"] = str(step.threads)
    return out


def _alive(pid: int) -> bool:
    """Whether a pid exists. `os.kill(pid, 0)` is the only portable test."""
    if NATIVE_WINDOWS:
        return _proc_start_ticks(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class JobStore:
    """The jobs on one agent, and the threads running them.

    One instance per agent process. ``on_event`` is called from the job threads, so a caller that
    touches shared state must be thread-safe; the callback must not raise, and a raise is caught and
    logged rather than being allowed to kill a job.
    """

    def __init__(
        self,
        root: Path | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else default_root()
        self._on_event = on_event
        #: Guards `_jobs` and `_job_locks` only; never held while a record is read or written.
        self._lock = threading.RLock()
        self._jobs: dict[str, _Running] = {}
        #: One lock per job. **Every read-modify-write of a job's record takes it** — the job
        #: thread, `cancel`, `put`, `start`, `recover` — so no writer can lose another's change.
        self._job_locks: dict[str, threading.RLock] = {}
        self._stopping = False
        #: The external profile's view of each install, by engine id: read once per agent.
        self._installs: dict[str, ProbedInstall] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        # 0o700: job folders hold case files, and a shared machine must not let others read them.
        with contextlib.suppress(OSError):
            self.root.chmod(0o700)

    # ------------------------------------------------------------------ paths

    def job_dir(self, job: str) -> Path:
        """The folder for a job id, validated before it is used to build a path."""
        self._check_id(job)
        return self.root / job

    def record_path(self, job: str) -> Path:
        """Where the job's record lives: ``<root>/.records/<job>.json``, outside the job folder."""
        self._check_id(job)
        return record_file(self.root / job)

    def _job_lock(self, job: str) -> threading.RLock:
        """The lock for one job's record. Created on first use, dropped by `delete`."""
        with self._lock:
            return self._job_locks.setdefault(job, threading.RLock())

    def _check_id(self, job: object) -> None:
        """Validate a job id, translating the pure rule's error into the API's code."""
        try:
            check_job_id(job)
        except JobPathError as exc:
            raise JobBadParams(str(exc), details={"job": str(job)}) from exc

    def _inside(self, job_dir: Path, rel: object) -> Path:
        """The absolute path of ``rel`` inside ``job_dir``, or a refusal.

        **Validated, then contained, then checked again.** The rules live in `jobpaths` because
        they are the security-relevant half of this module and are worth reading without the
        process code around them; this wraps them in the API's error type.
        """
        try:
            return job_path(job_dir, rel)
        except JobPathError as exc:
            raise JobBadParams(str(exc), details={"path": str(rel)}) from exc

    def _inside_staged(self, job_dir: Path, rel: object, profile: str | None = None) -> Path:
        """`_inside` for a path a CLIENT writes: it must also be in a staged folder.

        Used by `create`, `put` and `copy_from`, which is every way a client's path becomes a file
        in a job. See `jobpaths.check_staged_path` for what is refused; an `external` job may also
        stage time directories and `0.orig`.
        """
        try:
            return job_path(job_dir, check_staged_path(rel, external=profile == EXTERNAL))
        except JobPathError as exc:
            raise JobBadParams(str(exc), details={"path": str(rel)}) from exc

    def _load(self, job: str) -> dict[str, Any]:
        """The job's record, or a refusal if the job does not exist."""
        path = self.record_path(job)
        if not path.is_file():
            # **`agent.not_found`, not `agent.bad_params`.** The id is well-formed — `_check_id`
            # already
            # passed — so this is a job that is not there rather than a request that is wrong. A
            # client
            # deleting a job that finished and was already removed needs to tell those apart.
            raise JobNotFound(f"no job {job}", details={"code": "agent.not_found", "job": job})
        try:
            data = read_record(path)
        except (OSError, ValueError) as exc:
            raise JobError(f"job {job} has an unreadable record: {exc}") from exc
        # **Narrowed, not cast.** `json.loads` returns `Any`, and a record holding a list or a
        # string would otherwise be returned as if it were a job: every later `.get` would fail
        # far from the file that caused it.
        if not isinstance(data, dict):
            raise JobError(f"job {job} has a record that is not an object")
        return data

    def _save(self, job_dir: Path, state: dict[str, Any]) -> None:
        """Write the job's record atomically, so a reader never sees half a document."""
        write_state(job_dir, state)

    def _emit(self, name: str, data: dict[str, Any]) -> None:
        """Call the event callback, never letting it raise into a job thread."""
        if self._on_event is None:
            return
        try:
            self._on_event(name, data)
        except Exception:
            log.warning("job event callback failed", extra=fields(event=name))

    # ------------------------------------------------------------------ create

    def create(
        self,
        files: dict[str, bytes | str],
        steps: list[Step],
        install: str | None = None,
        label: str = "",
        copy_from: dict[str, Any] | None = None,
        profile: str | None = None,
    ) -> str:
        """Stage a job: validate everything, write the files, record the steps.

        A refusal here leaves **nothing** on disk: the job folder is removed on any error, so a
        client that retries does not accumulate half-written jobs. ``profile`` is None or
        `external` (`PROFILES`); an external job may not `copy_from` another job.
        """
        if profile not in PROFILES:
            raise JobBadParams(f"profile must be null or {EXTERNAL!r}")
        if profile == EXTERNAL and copy_from is not None:
            raise JobBadParams("an external job stages its whole case; copy_from is not allowed")
        if not _posix_host() and not NATIVE_WINDOWS:
            raise JobUnsupported(
                "jobs run on a Linux host; this Windows agent only probes",
                details={"code": "agent.unsupported"},
            )
        if NATIVE_WINDOWS and any(char.isspace() for char in str(self.root.resolve())):
            raise JobUnsupported(
                "Native OpenFOAM requires a job directory without whitespace. "
                "Set STOKES_AGENT_HOME to a directory without whitespace, then restart Stokes."
            )
        # **Refused BEFORE the folder exists** (Opus F6). Every check that can be made from the
        # request alone happens first, so a refusal leaves nothing to clean up and costs no disk.
        wanted = _total_bytes(files)
        if wanted > MAX_JOB_BYTES:
            raise JobTooLarge(
                f"the case is {wanted} bytes, over the {MAX_JOB_BYTES}-byte limit for one job",
                details={"bytes": wanted, "limit": MAX_JOB_BYTES},
            )
        free = _free_bytes(self.root)
        if free - wanted < DISK_RESERVE_BYTES:
            raise JobNoSpace(
                f"staging {wanted} bytes would leave {free - wanted} free, under the "
                f"{DISK_RESERVE_BYTES}-byte reserve; free some space and try again",
                details={
                    "bytes": wanted,
                    "free": free,
                    "reserve": DISK_RESERVE_BYTES,
                },
            )
        job = "j_" + secrets.token_hex(6)
        job_dir = self.root / job
        job_dir.mkdir(parents=True, exist_ok=False)
        try:
            # **`install` is an ENGINE ID, never a path.** The agent maps it onto an install its
            # own probe found; anything else is refused before any file is written.
            engine = self._engine_for(install)
            checked = self._check_steps(steps, engine, profile)
            copied: list[str] = []
            trusted: dict[str, int] = {}
            if copy_from is not None:
                copied, trusted = self._copy_from(job_dir, copy_from)
            for rel, content in files.items():
                self._write_file(job_dir, rel, content, profile)
                # A file the client staged over a copied mesh file is theirs, not the mesher's.
                trusted.pop(rel, None)
            # **At create, before a job can start.** The whole staged case is scanned once here: the
            # individual writes cannot check a file's meaning in context, and a refusal now leaves
            # nothing on disk because `create` removes the folder on any error.
            self._lint_or_refuse(self._lint_job(job_dir, engine, profile, trusted))
            state: dict[str, Any] = {
                "id": job,
                "label": label,
                "state": "queued",
                "created": _now(),
                "ended": None,
                # The engine ID, re-resolved at `start`. Never a path: a record that named one
                # would be a record whose bashrc gets sourced.
                "engine": engine_id(engine.bashrc) if install is None else install,
                "agent_pid": os.getpid(),
                "agent_start_ticks": _proc_start_ticks(os.getpid()),
                "steps": [
                    {
                        **step.to_json(),
                        "state": "queued",
                        "rc": None,
                        "started": None,
                        "ended": None,
                        "pid": None,
                        "pgid": None,
                        "start_ticks": None,
                    }
                    for step in checked
                ],
                "error": None,
            }
            if profile is not None:
                state["profile"] = profile
            if copy_from is not None:
                state["copy_from"] = {"job": copy_from.get("job"), "paths": copied}
            if trusted:
                # **Provenance** (FIXES-B B5): the polyMesh files this job holds that a mesher of
                # THIS agent wrote, with the size each had. `start` skips the scan for these.
                state["polymesh_trusted"] = trusted
            self._save(job_dir, state)
        except BaseException:
            shutil.rmtree(job_dir, ignore_errors=True)
            with contextlib.suppress(OSError):
                record_file(job_dir).unlink(missing_ok=True)
            raise
        self._emit(
            "job.state",
            {"job": job, "state": "queued", "step": None, "index": 0, "total": len(steps)},
        )
        return job

    def _engine_for(self, engine: object) -> EngineEnv:
        """The captured environment for an engine id (or None for the default), or a refusal.

        The only place an engine id from the wire becomes a bashrc: `engine_env.resolve_engine`
        looks it up among the installs the agent itself found, and refuses a path, a malformed id
        and an unknown one with `agent.bad_params`.
        """
        if engine is not None and not isinstance(engine, str):
            raise JobBadParams("install must be an engine id (a string) or null")
        try:
            return resolve_engine(engine)
        except UnknownEngine as exc:
            raise JobBadParams(exc.message, details=exc.details) from exc

    def _installed(self, engine: EngineEnv) -> ProbedInstall:
        """What ``engine``'s install has, from **this agent's own** read of its `FOAM_*` folders.

        Cached per engine for the agent's life: the lists are directory listings of the install,
        which does not change under a running agent, and a job's checks must not differ between
        `create` and `start` because a folder was mid-copy.
        """
        from .probe import install_inventory

        key = engine_id(engine.bashrc)
        with self._lock:
            found = self._installs.get(key)
        if found is not None:
            return found
        record = install_inventory(dict(engine.env))
        install = ProbedInstall.from_lists(
            solver_modules=record.get("solver_modules") or [],
            utilities=record.get("utilities") or [],
            libraries=record.get("libraries") or [],
            etc_functions=record.get("etc_functions") or [],
            etc_files=record.get("etc_case_dicts") or [],
        )
        with self._lock:
            self._installs[key] = install
        return install

    def _lint_job(
        self,
        job_dir: Path,
        engine: EngineEnv,
        profile: str | None,
        trusted: dict[str, int],
    ) -> list[Finding]:
        """The whole-job lint of the job's profile."""
        if profile == EXTERNAL:
            return lint_job_external(job_dir, self._installed(engine), trusted_polymesh=trusted)
        return lint_job(job_dir, trusted_polymesh=trusted)

    @staticmethod
    def _profile_of(job: str, state: dict[str, Any]) -> str | None:
        """The profile a record holds. The record is the agent's own, but an unknown value is
        refused rather than run under the default profile's rules."""
        profile = state.get("profile")
        if profile is not None and profile not in PROFILES:
            raise JobError(f"job {job} has a record with an unknown profile")
        return str(profile) if profile is not None else None

    def _check_steps(
        self, steps: list[Step], engine: EngineEnv, profile: str | None = None
    ) -> list[Step]:
        """Validate the step list: names unique and well-formed, argv allowed, utilities present."""
        if not steps:
            raise JobBadParams("a job needs at least one step")
        if len(steps) > 32:
            raise JobBadParams(f"a job has at most 32 steps; got {len(steps)}")
        seen: set[str] = set()
        for step in steps:
            if not isinstance(step, Step):
                raise JobBadParams(f"not a Step: {step!r}")
            if not STEP_NAME_RE.match(step.name):
                raise JobBadParams(
                    f"{step.name!r} is not a step name (a letter then up to 31 "
                    "letters, digits or underscores)"
                )
            if step.name in seen:
                raise JobBadParams(f"two steps are called {step.name!r}")
            seen.add(step.name)
            if not 1 <= step.timeout_s <= 86400:
                raise JobBadParams(f"timeout_s must be 1..86400; got {step.timeout_s}")
            if step.parse is not None and step.parse not in PARSERS:
                raise JobBadParams(
                    f"no log parser called {step.parse!r}; known: "
                    f"{', '.join(sorted(PARSERS)) or 'none'}"
                )
            if step.action is not None:
                self._check_action(step, profile)
            else:
                self._check_argv(step, engine, profile)
        return steps

    def _check_action(self, step: Step, profile: str | None) -> None:
        """A file action: an `external` job only, no program, and paths staged like any upload.

        **The paths are checked here for shape and again at run time for containment**, because
        what matters is what the folder holds when the action runs, not what it held at create.

        **`flatten` is the one action a mesh job may take**: it names a patch, not a path, and works
        on the mesh the job just made (`_run_flatten`). The file actions stay external-only.
        """
        if step.argv or step.parse is not None or step.ranks != 1 or step.threads != 1:
            raise JobBadParams(f"step {step.name!r} is an action and runs no program")
        action = step.action or {}
        op = action.get("op")
        if op == FLATTEN_OP:
            patch = action.get("patch")
            if set(action) != {"op", "patch"} or not isinstance(patch, str):
                raise JobBadParams(f"step {step.name!r}: flatten takes exactly one patch name")
            if not FLATTEN_PATCH_RE.match(patch):
                raise JobBadParams(f"step {step.name!r}: {patch!r} is not a patch name")
            return
        if profile != EXTERNAL:
            raise JobBadParams(
                f"step {step.name!r} is a file action, which only an external job takes"
            )
        needed = _ACTION_PATHS.get(str(op))
        if needed is None or set(action) - {"op", *_ACTION_KEYS}:
            raise JobBadParams(
                f"step {step.name!r} has a file action that is not mkdir, remove or copy"
            )
        for key in _ACTION_KEYS:
            if key not in needed:
                if key in action:
                    raise JobBadParams(f"step {step.name!r}: {op} takes no {key}")
                continue
            if not isinstance(action.get(key), str):
                raise JobBadParams(f"step {step.name!r}: {op} needs a {key} path")
            _action_rel(action[key], profile)

    def _check_argv(self, step: Step, engine: EngineEnv, profile: str | None = None) -> None:
        """**The security boundary.** ``argv[0]`` is a bare allow-listed name, resolved against the
        CAPTURED path; every item is a bounded string with no NUL. An `external` step is checked
        against `flags_external` and the install's own lists instead (`_check_external_argv`)."""
        argv = step.argv
        if not argv:
            raise JobBadParams(f"step {step.name!r} has an empty argv")
        if profile == EXTERNAL:
            self._check_external_argv(step, engine)
            return
        if argv[0] not in ALLOWED_UTILITIES:
            raise JobBadParams(
                f"{argv[0]!r} is not an allowed utility; allowed: "
                f"{', '.join(sorted(ALLOWED_UTILITIES))}",
                details={"argv0": str(argv[0])},
            )
        for item in argv:
            if not isinstance(item, str):
                raise JobBadParams(f"every argv item must be a string; got {item!r}")
            if len(item) > MAX_ARGV_ITEM:
                raise JobBadParams(
                    f"an argv item is {len(item)} characters, over the {MAX_ARGV_ITEM} limit"
                )
            if "\0" in item:
                raise JobBadParams("an argv item contains a NUL byte")
        self._check_flags(step)
        if (
            isinstance(step.ranks, bool)
            or not isinstance(step.ranks, int)
            or not 1 <= step.ranks <= 256
        ):
            raise JobBadParams("ranks must be an integer from 1 to 256")
        if (
            isinstance(step.threads, bool)
            or not isinstance(step.threads, int)
            or not 1 <= step.threads <= 256
        ):
            raise JobBadParams("threads must be an integer from 1 to 256")
        if step.threads > 1 and argv[0] not in THREADED_UTILITIES:
            raise JobBadParams(f"{argv[0]} does not take OpenMP threads")
        if step.ranks > 1:
            if argv[0] not in {"snappyHexMesh", "foamRun", "checkMesh"}:
                raise JobBadParams(f"{argv[0]} does not support an MPI step")
            if mpi_launcher(engine) is None:
                raise JobMissingUtility(mpi_unavailable_reason(engine))
        if resolve_utility(engine, argv[0]) is None:
            raise JobMissingUtility(
                f"{argv[0]} is not on the engine's PATH",
                details={"code": "agent.missing_utility", "utility": argv[0]},
            )

    def _check_external_argv(self, step: Step, engine: EngineEnv) -> None:
        """`_check_argv` for an external case: the same item, rank and PATH rules, with the
        program and its arguments judged by `flags_external.check_external_argv`."""
        argv = step.argv
        for item in argv:
            if not isinstance(item, str):
                raise JobBadParams(f"every argv item must be a string; got {item!r}")
            if len(item) > MAX_ARGV_ITEM:
                raise JobBadParams(
                    f"an argv item is {len(item)} characters, over the {MAX_ARGV_ITEM} limit"
                )
            if "\0" in item:
                raise JobBadParams("an argv item contains a NUL byte")
        try:
            check_external_argv(argv, self._installed(engine))
        except ExternalFlagRefused as exc:
            raise JobBadParams(exc.message, details=exc.details) from exc
        if step.threads != 1:
            raise JobBadParams("an external step takes no OpenMP threads")
        if (
            isinstance(step.ranks, bool)
            or not isinstance(step.ranks, int)
            or not 1 <= step.ranks <= 256
        ):
            raise JobBadParams("ranks must be an integer from 1 to 256")
        if step.ranks > 1:
            if argv[0] not in EXTERNAL_MPI:
                raise JobBadParams(f"{argv[0]} does not support an MPI step")
            if mpi_launcher(engine) is None:
                raise JobMissingUtility(mpi_unavailable_reason(engine))
        if resolve_utility(engine, argv[0]) is None:
            raise JobMissingUtility(
                f"{argv[0]} is not on the engine's PATH",
                details={"code": "agent.missing_utility", "utility": argv[0]},
            )

    def _check_flags(self, step: Step) -> None:
        """**The second half of the boundary.** Which arguments the utility may be given.

        An allow-listed program with an unchecked argument list is not a sandbox: `foamRun -case
        /etc` would read a directory the job does not own, and `-libs` loads arbitrary code into
        the process. So every item after the program name must be a listed flag, or the value of
        a one-argument flag immediately before it, or it is refused.

        **The list is closed.** A new flag is added to `flags.py` deliberately, with its reason,
        rather than passed through because it looked harmless.
        """
        argv = step.argv
        allowed = ALLOWED_FLAGS.get(argv[0], {})
        positional = POSITIONAL.get(argv[0])
        index = 1
        while index < len(argv):
            item = argv[index]
            # **One positional value, matched in full** (`flags.POSITIONAL`): polyDualMesh's
            # feature angle. Taken once, so a second bare word is still refused below.
            if positional is not None and re.fullmatch(positional, item):
                positional = None
                index += 1
                continue
            if item in FORBIDDEN_FLAGS:
                raise JobBadParams(
                    f"{item} is not allowed for any utility",
                    details={"flag": item, "utility": argv[0]},
                )
            spec = allowed.get(item)
            if spec is None:
                raise JobBadParams(
                    f"{item!r} is not an allowed argument for {argv[0]}; allowed: "
                    f"{', '.join(sorted(allowed)) or 'none'}",
                    details={"flag": item, "utility": argv[0]},
                )
            if spec.arity == 1:
                if index + 1 >= len(argv):
                    raise JobBadParams(
                        f"{item} needs a value",
                        details={"flag": item, "utility": argv[0]},
                    )
                value = argv[index + 1]
                if spec.pattern is not None and not re.fullmatch(spec.pattern, value):
                    raise JobBadParams(
                        f"{item} {value!r} is not allowed: "
                        f"{spec.why or 'the value is not permitted'}",
                        details={"flag": item, "value": value, "utility": argv[0]},
                    )
                index += 2
            else:
                index += 1

    def _copy_from(self, job_dir: Path, spec: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
        """Copy artifacts out of a finished job — the snappy case, where `constant/polyMesh` comes
        from
        an earlier meshing run.

        Four refusals matter here, and each is a way this could be used to read or write
        something it should not: the source must be a **done** job; every path must be relative
        and non-climbing; the resolved path must be inside the source folder; and a path that
        **is or contains a symlink** is refused, because `copytree(symlinks=False)` would
        otherwise follow it out of the job tree.

        Returns the paths copied and **the `constant/polyMesh` files among them that the source
        job's own mesher wrote** (name -> size), from the source's record. Those are the only
        files whose scan is skipped at `start`; a file the client staged in the source is not one.
        """
        if not isinstance(spec, dict):
            raise JobBadParams("copy_from must be an object")
        source_id = spec.get("job")
        self._check_id(source_id)
        paths = spec.get("paths")
        if not isinstance(paths, list) or not paths:
            raise JobBadParams("copy_from needs a non-empty paths list")
        source_dir = self.job_dir(str(source_id))
        source_state = self._load(str(source_id))
        if source_state.get("state") != "done":
            raise JobBadParams(
                f"job {source_id} is {source_state.get('state')}, not done; "
                "only a finished job's artifacts can be copied"
            )
        total = 0
        copied: list[str] = []
        trusted: dict[str, int] = {}
        for rel in paths:
            source = self._inside(source_dir, rel)
            if not source.exists():
                raise JobBadParams(f"job {source_id} has no {rel}", details={"path": rel})
            self._check_copy_root(rel)
            if has_symlink(source_dir, rel):
                raise JobBadParams(
                    f"{rel} is or contains a symlink; refusing to copy it",
                    details={"path": rel},
                )
            self._check_copy_tree(source_dir, rel, source)
            size = tree_bytes(source)
            total += size
            if total > MAX_FILE_BYTES * 4:
                raise JobTooLarge(
                    f"copy_from would copy {total} bytes, over the 2 GiB limit",
                    details={"bytes": total},
                )
            target = self._inside(job_dir, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                # `symlinks=True` copies a link as a link instead of following it; none is left
                # by now, because `_check_copy_tree` refused any link in the tree.
                shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target, follow_symlinks=False)
            copied.append(rel)
            trusted.update(self._mesher_written(source_state, source_dir, job_dir, target))
        time_job = spec.get("latest_time_from")
        if time_job is not None:
            copied.append(self._copy_latest_time(job_dir, time_job, total))
        return copied, trusted

    def _copy_latest_time(self, job_dir: Path, time_job: object, total: int) -> str:
        """Copy a finished run's newest written time folder into ``job_dir``; returns its name.

        **Resume** (`run.resume`): the folder is chosen HERE from that job's own tree, never named
        by the client, so this reaches nothing but a solver-written time directory of a finished
        job of this agent. It is a second source because the mesh stays the mesher's own (trusted,
        never rescanned) while the fields come from the run; every copied file is still linted at
        `start`, as all of a job's files are.
        """
        self._check_id(time_job)
        time_dir = self.job_dir(str(time_job))
        time_state = self._load(str(time_job))
        if time_state.get("state") != "done":
            raise JobBadParams(
                f"job {time_job} is {time_state.get('state')}, not done; "
                "only a finished run can be resumed",
            )
        name = _latest_time_dir(time_dir)
        if name is None:
            raise JobBadParams(
                f"job {time_job} has no written time to resume from",
                details={"code": "agent.no_time"},
            )
        source = time_dir / name
        for root, dirnames, filenames in os.walk(source, followlinks=False):
            for entry in [*dirnames, *filenames]:
                if (Path(root) / entry).is_symlink():
                    raise JobBadParams(
                        f"{name} contains a symlink; refusing to copy it",
                        details={"path": name},
                    )
        if total + tree_bytes(source) > MAX_FILE_BYTES * 4:
            raise JobTooLarge(
                "copy_from would copy more than the 2 GiB limit",
                details={"bytes": total + tree_bytes(source)},
            )
        shutil.copytree(source, job_dir / name, symlinks=True, dirs_exist_ok=True)
        return name

    def _mesher_written(
        self, source_state: dict[str, Any], source_dir: Path, job_dir: Path, target: Path
    ) -> dict[str, int]:
        """The copied files under `constant/polyMesh` that the source job's mesher wrote.

        A file counts when the source's RECORD lists it (`_record_polymesh`, written by the agent
        when a `blockMesh` or `snappyHexMesh` step of that job finished) and its size, in the
        source and in the copy, is the recorded one. A file the source merely held — staged by a
        client — is not listed, so it is scanned like any other.
        """
        manifest = source_state.get("polymesh")
        if not isinstance(manifest, dict):
            return {}
        if target.is_file():
            files = [target]
        else:
            files = [item for item in target.rglob("*") if item.is_file() and not item.is_symlink()]
        found: dict[str, int] = {}
        for item in files:
            name = item.relative_to(job_dir).as_posix()
            recorded = manifest.get(name)
            if not name.startswith(POLYMESH_PREFIX) or not _is_size(recorded):
                continue
            original = source_dir / name
            if item.stat().st_size == recorded and original.stat().st_size == recorded:
                found[name] = int(recorded)
        return found

    def _check_copy_root(self, rel: object) -> None:
        """A `copy_from` path must start in a staged folder (it may be the folder itself)."""
        try:
            checked = check_relative(rel)
        except JobPathError as exc:
            raise JobBadParams(str(exc), details={"path": str(rel)}) from exc
        if checked.split("/")[0] not in STAGED_DIRS:
            raise JobBadParams(
                f"{checked!r} cannot be copied: only {', '.join(STAGED_DIRS)} are staged folders",
                details={"path": checked},
            )

    def _check_copy_tree(self, source_dir: Path, rel: str, source: Path) -> None:
        """Every file a copy would bring in is a path a client could have staged, and no link.

        **The copy is checked file by file, not just at its root.** A `state.json`, a log, an
        `artifacts/` folder or a dot-file inside the copied folder is refused rather than skipped:
        silently dropping files would hand the user a case that is not the one they named. A
        symbolic link anywhere in the tree is refused, which `copytree` would otherwise follow.
        """
        if source.is_file():
            self._inside_staged(source_dir, rel)
            return
        for root, dirnames, filenames in os.walk(source, followlinks=False):
            for name in [*dirnames, *filenames]:
                found = Path(root) / name
                if found.is_symlink():
                    raise JobBadParams(
                        f"{rel} contains a symlink ({found.relative_to(source_dir).as_posix()}); "
                        "refusing to copy it",
                        details={"path": rel},
                    )
            for name in filenames:
                inner = (Path(root) / name).relative_to(source_dir).as_posix()
                try:
                    check_staged_path(inner)
                except JobPathError as exc:
                    raise JobBadParams(
                        f"{rel} contains {inner}, which cannot be copied: {exc}",
                        details={"path": inner},
                    ) from exc

    def _lint_or_refuse(self, findings: list[Finding]) -> None:
        """Raise `agent.unsafe_dict` for the first finding, naming the file, line and token.

        One builder for the three call sites, so a client sees one error shape whether the file
        arrived through `create`, through a chunked `put`, or through `copy_from` from an older
        job.
        """
        if not findings:
            return
        first = findings[0]
        where = f"{first.file}:{first.line}" if first.line else first.file
        raise JobUnsafeDict(
            f"{where}: {first.token} {first.why}",
            details={
                "code": "agent.unsafe_dict",
                "file": first.file,
                "line": first.line,
                "token": first.token,
                "findings": [item.to_json() for item in findings[:20]],
            },
        )

    def _write_file(
        self, job_dir: Path, rel: str, content: bytes | str, profile: str | None = None
    ) -> None:
        """Write one staged file atomically."""
        target = self._inside_staged(job_dir, rel, profile)
        data = content.encode("utf-8") if isinstance(content, str) else content
        if not isinstance(data, bytes):
            raise JobBadParams(f"file {rel} content must be bytes or str")
        if len(data) > MAX_FILE_BYTES:
            raise JobTooLarge(
                f"file {rel} is {len(data)} bytes, over the {MAX_FILE_BYTES} limit",
                details={"path": rel, "bytes": len(data)},
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
        temp.write_bytes(data)
        temp.replace(target)

    def put(self, job: str, path: str, data: bytes, offset: int, final: bool) -> int:
        """Append a chunk to ``path``, which is held as ``<path>.part`` until ``final``.

        Chunked because a case can be hundreds of megabytes and the protocol frames are bounded; the
        offset check makes a retry after a dropped connection safe.
        """
        self._check_id(job)
        job_dir = self.job_dir(job)
        # **The job's lock, the same one `start` takes.** Without it a final `put` could land
        # after `start` had linted the case and before the process read it: the lint would have
        # passed a file that was not yet there.
        with self._job_lock(job):
            state = self._load(job)
            if state.get("state") != "queued":
                raise JobBadParams(
                    f"job {job} is {state.get('state')}; files are accepted only while it is queued"
                )
            trusted = _trusted_of(state)
            if path in trusted:
                # **A client write over a mesher's file ends the trust in it** (FIXES-B B5), before
                # a byte is written: whatever lands there is scanned in full at `start`, even when
                # `put` refuses it and leaves it on disk.
                del trusted[path]
                state["polymesh_trusted"] = trusted
                self._save(job_dir, state)
            profile = self._profile_of(job, state)
            engine = self._engine_for(state.get("engine")) if profile == EXTERNAL else None
            return self._put_locked(job_dir, path, data, offset, final, profile, engine)

    def _put_locked(
        self,
        job_dir: Path,
        path: str,
        data: bytes,
        offset: int,
        final: bool,
        profile: str | None = None,
        engine: EngineEnv | None = None,
    ) -> int:
        """The body of `put`, with the job's lock held and its state checked."""
        target = self._inside_staged(job_dir, path, profile)
        part = target.with_name(target.name + ".part")
        current = part.stat().st_size if part.exists() else 0
        if offset != current:
            raise JobBadParams(
                f"offset {offset} does not match the {current} bytes already written",
                details={"offset": offset, "size": current},
            )
        if current + len(data) > MAX_FILE_BYTES:
            raise JobTooLarge(
                f"{path} would exceed {MAX_FILE_BYTES} bytes",
                details={"path": path},
            )
        part.parent.mkdir(parents=True, exist_ok=True)
        with part.open("ab") as handle:
            handle.write(data)
        written = current + len(data)
        if final:
            part.replace(target)
            # **The file only becomes a dictionary when its last chunk lands.** Checking each chunk
            # would refuse a file split mid-directive, which is a false refusal. A file that is
            # binary, unreadable or too large is refused here too (`lint_file`).
            if profile == EXTERNAL and engine is not None:
                self._lint_or_refuse(lint_file_external(path, target, self._installed(engine)))
            else:
                self._lint_or_refuse(lint_file(path, target))
        return written

    # ------------------------------------------------------------------ run

    def start(self, job: str) -> str:
        """Begin the job on a daemon thread and return its state.

        **Everything that runs is rebuilt from the job's RECORD and checked again here.** The record
        is written only by the agent (`.records/`, outside the job folder), but `start` is the
        point of use, so the argv list is validated again — program on the allow-list, every flag
        in `flags.py`, the utility on the engine's PATH — exactly as `create` validated it.
        """
        self._check_id(job)
        job_dir = self.job_dir(job)
        with self._job_lock(job):
            with self._lock:
                running = self._jobs.get(job)
                if running is not None and running.thread.is_alive():
                    raise JobError(f"job {job} is already running", details={"code": "agent.busy"})
            state = self._load(job)
            if state.get("state") != "queued":
                raise JobError(f"job {job} is {state.get('state')}; only a queued job can start")
            # **At start too, and this is not redundant.** `copy_from` brings files in from
            # an earlier job, which may have been staged by an older agent under rules that
            # have since changed; and `put` writes files one at a time, so no single call
            # saw the whole case.
            profile = self._profile_of(job, state)
            engine = self._engine_for(state.get("engine"))
            self._lint_or_refuse(self._lint_job(job_dir, engine, profile, _trusted_of(state)))
            steps = self._check_steps(self._steps_of(job, state), engine, profile)
            state["state"] = "running"
            state["steps"][0]["state"] = "running"
            state["steps"][0]["started"] = _now()
            self._save(job_dir, state)
            with self._lock:
                self._jobs[job] = _Running(thread=threading.Thread(target=lambda: None))
        self._emit(
            "job.state",
            {
                "job": job,
                "state": "running",
                "step": steps[0].name,
                "index": 0,
                "total": len(steps),
            },
        )

        thread = threading.Thread(
            target=self._run,
            args=(job, job_dir, steps, engine, profile),
            name=f"stokes-job-{job}",
            daemon=True,
        )
        with self._lock:
            self._jobs[job].thread = thread
        thread.start()
        return "running"

    def _steps_of(self, job: str, state: dict[str, Any]) -> list[Step]:
        """The steps a record holds, as `Step` objects. A malformed record is refused, not run."""
        try:
            return [
                Step(
                    name=item["name"],
                    argv=tuple(item["argv"]),
                    timeout_s=int(item.get("timeout_s", 3600)),
                    parse=item.get("parse"),
                    parse_params=dict(item.get("parse_params") or {}),
                    ranks=item.get("ranks", 1),
                    threads=item.get("threads", 1),
                    action=item.get("action"),
                )
                for item in state["steps"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise JobError(f"job {job} has a record with malformed steps: {exc}") from exc

    def _run(
        self,
        job: str,
        job_dir: Path,
        steps: list[Step],
        engine: EngineEnv,
        profile: str | None = None,
    ) -> None:
        """The job thread: run each step in order, then write the final state."""
        total = len(steps)
        failure: dict[str, str] | None = None
        try:
            for index, step in enumerate(steps):
                # **A cancel asked for between two steps stops the next one from launching**: the
                # signal wave `cancel` sends reaches only the processes that exist when it runs.
                if self._stopping or self._cancelling(job_dir):
                    self._set_step(job_dir, index, "skipped")
                    continue
                if step.action is not None:
                    outcome, error = self._run_action(job_dir, step, profile)
                    rc = None
                else:
                    outcome, rc, error = self._run_step(
                        job, job_dir, index, step, engine, total, profile
                    )
                self._set_step(job_dir, index, outcome, rc)
                if error is not None:
                    failure = error
                if outcome == "failed" and failure is None:
                    failure = {"code": "agent.step_failed", "message": f"{step.name} failed"}
                if outcome == "failed" and not self._cancelling(job_dir):
                    # The first failure ends the job; the rest are skipped.
                    for later in range(index + 1, total):
                        self._set_step(job_dir, later, "skipped")
                    break
                if outcome == "cancelled":
                    for later in range(index + 1, total):
                        self._set_step(job_dir, later, "skipped")
                    break
        except Exception as exc:
            log.exception("job thread failed", extra=fields(job=job))
            failure = {"code": "agent.job", "message": f"the supervisor failed: {exc}"}
        finally:
            self._finish(job, job_dir, failure)

    def _cancelling(self, job_dir: Path) -> bool:
        """Whether a cancel has been asked for. Read from the record so `cancel` need not share
        state."""
        data = self._load_state_file(job_dir)
        return data is not None and data.get("state") == "cancelling"

    def _run_action(
        self, job_dir: Path, step: Step, profile: str | None
    ) -> tuple[str, dict[str, str] | None]:
        """One file action, inside the job folder only: ``mkdir -p``, ``rm -rf`` or ``cp -r``.

        **This is what lets a recipe's `potentialFoam` then `rm -f 0/phi` then `foamRun` run in
        order.** Staging cannot apply a file action that follows a program, because the program
        writes the file first. The action is validated, contained on the resolved path, and refused
        on any symbolic link, so a copy cannot read or remove anything outside the job folder.
        """
        action = step.action or {}
        op = str(action.get("op"))
        if op == FLATTEN_OP:
            return self._run_flatten(job_dir, step, str(action.get("patch")))
        source_rel = str(action.get("source") or "")
        target_rel = str(action.get("target") or "")
        try:
            for rel in (source_rel, target_rel):
                if rel and has_symlink(job_dir, rel):
                    raise JobBadParams(f"{rel!r} passes through a symbolic link")
            target = _action_path(job_dir, target_rel, profile) if target_rel else None
            if op == "mkdir" and target is not None:
                target.mkdir(parents=True, exist_ok=True)
            elif op == "remove" and target is not None:
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            elif op == "copy" and target is not None:
                source = _action_path(job_dir, source_rel, profile)
                if not source.exists():
                    raise JobBadParams(f"copy: {source_rel!r} does not exist")
                if source.is_dir():
                    if any(part.is_symlink() for part in source.rglob("*")):
                        raise JobBadParams(f"copy: {source_rel!r} holds a symbolic link")
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            else:
                raise JobBadParams(f"file action {op!r} has no target")
        except (JobError, OSError) as exc:
            return "failed", {"code": "agent.file_action", "message": f"{op}: {exc}"}
        return "done", None

    def _run_flatten(
        self, job_dir: Path, step: Step, patch: str
    ) -> tuple[str, dict[str, str] | None]:
        """Snap a symmetry patch of the mesh in this job onto its plane (`stokes_agent.flatten`).

        **Never fails the job**: a mesh that cannot be flattened (binary, too large, a curved
        patch, broken cells beside the plane) is a mesh that stays as it is, and the mesh service
        measures and reports the flatness of what is there. The outcome goes to the step's log,
        which is where a user reading why the plane is still bent will look.
        """
        from .flatten import FlattenSkipped, flatten_patch

        log_file = job_dir / f"log.{step.name}"
        try:
            message = flatten_patch(job_dir, patch).text()
        except FlattenSkipped as exc:
            message = f"flatten {patch}: left as it is: {exc}"
        except Exception as exc:  # a failed flatten must not end the mesh job
            log.warning("flatten failed", extra=fields(patch=patch, error=str(exc)[:200]))
            message = f"flatten {patch}: left as it is: {type(exc).__name__}: {exc}"
        try:
            log_file.write_text(message + "\n", encoding="utf-8")
        except OSError:
            log.debug("flatten log not written")
        return "done", None

    def _run_step(
        self,
        job: str,
        job_dir: Path,
        index: int,
        step: Step,
        engine: EngineEnv,
        total: int,
        profile: str | None = None,
    ) -> tuple[str, int | None, dict[str, str] | None]:
        """One step: launch it, stream its log, enforce the timeout and the log cap.

        Returns the step's final state and an error record when there is one.
        """
        # **Validated at the point of use**, though `create` and `start` already did it: what is
        # launched is checked against the allow-lists immediately before it is launched.
        try:
            self._check_argv(step, engine, profile)
        except JobError as exc:
            return "failed", None, {"code": exc.code, "message": exc.message}
        resolved = resolve_utility(engine, step.argv[0])
        if resolved is None:
            return (
                "failed",
                None,
                {
                    "code": "agent.missing_utility",
                    "message": f"{step.argv[0]} is not on the engine's PATH",
                },
            )
        libs = _launch_libs(step.argv[0], job_dir)
        argv = [resolved, *step.argv[1:], *libs]
        if step.ranks > 1:
            launcher = mpi_launcher(engine)
            if launcher is None:
                return (
                    "failed",
                    None,
                    {"code": "agent.missing_utility", "message": mpi_unavailable_reason(engine)},
                )
            # `libs` is empty for every utility the agent launches over MPI (`_launch_libs`).
            argv = [
                launcher,
                engine.mpi_count_flag,
                str(step.ranks),
                resolved,
                "-parallel",
                *step.argv[1:],
                *libs,
            ]
        log_path = job_dir / f"log.{step.name}"
        started = time.monotonic()
        # **The Windows feature-mesh workaround** (see `FEATURE_READERS`): the recorded argv is
        # unchanged; only what is launched moves to the parent and gains a relative `-case`.
        argv, launch_dir = _native_case(step.argv[0], argv, job_dir)
        # **What a mesher writes is the agent's own work** (FIXES-B B5), so the polyMesh files as
        # they stand BEFORE the launch are remembered: only what the step added or rewrote is
        # recorded as the mesher's, never a file the client staged there.
        meshes = step.argv[0] in MESHERS
        polymesh_before = self._polymesh_state(job_dir) if meshes else {}
        # **Built at step start**, so a parser sees the whole run and is discarded with it. The job
        # folder goes in too, because a parser that must not lose data writes its own file there
        # (T-213's residual history) — the brief asked for this and T-202 did not have it.
        parser = self._build_parser(step, job_dir)
        try:
            handle = log_path.open("wb")
        except OSError as exc:
            return "failed", None, {"code": "agent.job", "message": f"cannot write the log: {exc}"}
        with handle:
            try:
                process = _launch(argv, launch_dir, _step_env(engine.env, step), handle)
            except OSError as exc:
                return "failed", None, {"code": "agent.job", "message": f"cannot start: {exc}"}
            try:
                self._record_pid(job_dir, index, process.pid)
                outcome, rc, error = self._wait(
                    job, job_dir, index, step, process, log_path, started, parser
                )
            finally:
                if NATIVE_WINDOWS:
                    from .windows_process import release

                    release(process.pid)
        # **Wait for the process to be fully gone before reading its log.** `poll()` says the
        # child is
        # reaped, but a utility's last writes can reach the file after that — and for a solve those
        # lines are `SIMPLE solution converged in N iterations` and `End`, which live AFTER
        # the final
        # block. `wait()` is bounded so a wedged child cannot hang the job.
        #
        # **This reduced the failure rate but did NOT remove it** (measured: 3 in 12 before, 2 in 12
        # after). The race is still open; see `.coordination/FOAMRUN-CONVERGENCE-RACE.md` for
        # exactly
        # what is and is not known. It is kept because waiting for a process you are about to
        # read the
        # output of is correct regardless.
        # **A best-effort wait, and it must tolerate a process object that cannot wait.**
        # `Popen` has
        # `wait`; a test's fake need not, and requiring it turned six passing limits tests into
        # `AttributeError: 'FakePopen' object has no attribute 'wait'`. The wait is an
        # optimisation —
        # without it the tail read may miss the last lines — so a process that cannot be
        # waited on is
        # simply read as before.
        try:
            wait = getattr(process, "wait", None)
            if callable(wait):
                wait(timeout=max(1.0, min(10.0, float(step.timeout_s))))
        except subprocess.TimeoutExpired:
            log.warning("the process did not exit before its log was read", extra=fields(job=job))
        except Exception as exc:
            log.debug("could not wait for the process", extra=fields(job=job, error=str(exc)[:120]))
        if meshes and outcome == "done":
            self._record_polymesh(job_dir, polymesh_before)
        # **The report is written whatever the outcome** — a failed run's summary is worth keeping.
        self._store_report(job_dir, step, parser)
        return outcome, rc, error

    def _polymesh_state(self, job_dir: Path) -> dict[str, tuple[int, int]]:
        """Every regular file under `constant/polyMesh` as ``name -> (size, mtime_ns)``."""
        found: dict[str, tuple[int, int]] = {}
        base = job_dir / "constant" / "polyMesh"
        if base.is_symlink() or not base.is_dir():
            return found
        for root, _dirs, names in os.walk(base, followlinks=False):
            for name in names:
                item = Path(root) / name
                if item.is_symlink():
                    continue
                try:
                    info = item.stat()
                except OSError:
                    continue
                found[item.relative_to(job_dir).as_posix()] = (info.st_size, info.st_mtime_ns)
        return found

    def _record_polymesh(self, job_dir: Path, before: dict[str, tuple[int, int]]) -> None:
        """Record in the job's record the polyMesh files a mesher step wrote: new or rewritten.

        The record is agent-only, so this is provenance a client cannot forge. `copy_from` reads it
        (`_mesher_written`) to decide which files of a finished job may skip the scan.
        """
        after = self._polymesh_state(job_dir)
        wrote = {
            name: size for name, (size, mtime) in after.items() if before.get(name) != (size, mtime)
        }
        if not wrote:
            return
        with self._job_lock(job_dir.name):
            state = self._load_state_file(job_dir)
            if state is None:
                return
            manifest = state.get("polymesh")
            if not isinstance(manifest, dict):
                manifest = {}
            manifest.update(wrote)
            state["polymesh"] = manifest
            self._save(job_dir, state)

    def _record_pid(self, job_dir: Path, index: int, pid: int) -> None:
        """Mark the step **running** and record its pid, group and start time.

        **Before the process can exit**, so `recover` after a crash has something to test.
        Writing it after `wait` would leave a window in which an orphan is untraceable.

        **And the `running` transition belongs here.** `start` writes `running` for step 0 only, so
        for every later step the state file said `queued` from the moment it launched until the
        moment it ended — measured on a real pitzDaily run, where the solve read `queued` for its
        whole 40 seconds. A client watching the job could not tell a step that had begun from one
        that had not.

        **The transition is announced too, for every step after the first** (`start` announces step
        0). Only a step's END was sent, so a client kept naming the step that had just finished:
        measured on the elbow, the Mesh card read "decomposePar" for the whole of a parallel
        snappyHexMesh run.
        """
        name = ""
        total = 0
        with self._job_lock(job_dir.name):
            state = self._load_state_file(job_dir)
            if state is None:
                return
            with contextlib.suppress(IndexError):
                entry = state["steps"][index]
                entry["state"] = "running"
                entry["started"] = _now()
                entry["pid"] = pid
                entry["pgid"] = pid if NATIVE_WINDOWS else _getpgid(pid)
                entry["start_ticks"] = _proc_start_ticks(pid)
                name = str(entry.get("name", ""))
            self._save(job_dir, state)
            job = str(state.get("id", ""))
            total = len(state.get("steps") or [])
        if index > 0 and name:
            self._emit(
                "job.state",
                {"job": job, "state": "running", "step": name, "index": index, "total": total},
            )

    def _load_state_file(self, job_dir: Path) -> dict[str, Any] | None:
        """The job's record, or None when it is missing or not an object. Never raises."""
        try:
            data = read_record(record_file(job_dir))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _wait(
        self,
        job: str,
        job_dir: Path,
        index: int,
        step: Step,
        process: subprocess.Popen[bytes],
        log_path: Path,
        started: float,
        parser: _StepParser | None = None,
    ) -> tuple[str, int | None, dict[str, str] | None]:
        """Wait for the process, streaming its log and enforcing the two limits.

        **The stream is bounded** (FIXES-B B7). At most `LOG_READ_CHUNK_BYTES` of the log are read
        per pass, at most `LOG_RING_LINES` lines wait to be sent, and an event carries at most
        `LOG_EVENT_MAX_LINES`; what the ring pushes out is counted and reported in ``dropped``.
        **The parser still sees every line**, in order and exactly once (`_take` feeds it before
        the ring can drop anything).
        """
        offset = 0
        last_event = 0.0
        pending: list[str] = []
        dropped = 0
        while True:
            # Shutdown may race launch before its PID reaches the record. The worker
            # owns the process handle and closes that gap without trusting saved PIDs.
            if NATIVE_WINDOWS and (self._stopping or self._cancelling(job_dir)):
                self._kill_group(process)
                self._flush(job, step.name, pending, dropped, log_path, offset, parser)
                return "cancelled", process.returncode, None
            rc = process.poll()
            offset, lines, _ = self._read_new_lines(log_path, offset)
            dropped += self._take(job, step.name, pending, lines, parser)
            now = time.monotonic()
            if (pending or dropped) and (
                now - last_event >= LOG_EVENT_INTERVAL_S or rc is not None
            ):
                self._emit_lines(job, step.name, pending, dropped)
                pending = []
                dropped = 0
                last_event = now
            if rc is not None:
                # **One more read after the process is reaped.** `poll()` says the process is gone,
                # but the last of its output may not be on disk yet: a C++ utility buffers, and the
                # kernel has the write before the file does. Breaking here lost the tail — for a
                # solve, the `SIMPLE solution converged in N iterations` line and `End`, which live
                # AFTER the final block. Measured: one run in four came out `converged: False` with
                # every residual correct, because exactly that line was missed.
                offset, tail, _ = self._read_new_lines(log_path, offset)
                if tail:
                    dropped += self._take(job, step.name, pending, tail, parser)
                break
            if time.monotonic() - started > step.timeout_s:
                self._kill_group(process)
                self._flush(job, step.name, pending, dropped, log_path, offset, parser)
                return (
                    "failed",
                    None,
                    {
                        "code": "agent.timeout",
                        "message": f"{step.name} ran longer than {step.timeout_s}s",
                    },
                )
            if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
                self._kill_group(process)
                self._truncate_log(log_path)
                offset = min(offset, MAX_LOG_BYTES)
                self._flush(job, step.name, pending, dropped, log_path, offset, parser)
                return (
                    "failed",
                    None,
                    {
                        "code": "agent.log_limit",
                        "message": f"{step.name} wrote more than {MAX_LOG_BYTES} bytes of log",
                    },
                )
            time.sleep(LOG_SIZE_CHECK_S / 10 if rc is None else 0)
        self._flush(job, step.name, pending, dropped, log_path, offset, parser)
        # **143 is SIGTERM**, so a step that ended 143 while a cancel was asked for is
        # CANCELLED, not failed. Measured: a group killed during `foamRun` reports 143.
        if self._cancelling(job_dir):
            return "cancelled", rc, None
        if rc == 0:
            return "done", rc, None
        return (
            "failed",
            rc,
            {
                "code": "agent.step_failed",
                "message": f"{step.name} exited {rc}",
            },
        )

    def _take(
        self,
        job: str,
        step: str,
        pending: list[str],
        lines: list[str],
        parser: _StepParser | None,
    ) -> int:
        """Take freshly read lines: feed the parser ALL of them, queue them for the wire, and trim
        the queue to `LOG_RING_LINES`. Returns how many queued lines were pushed out.

        **The parser is fed here, before the ring can drop anything**, so it sees every line the
        step wrote, in order and exactly once — not a second read of the file, which could race the
        writer, and not the part of the stream that survived the cap.
        """
        if not lines:
            return 0
        if parser is not None:
            self._emit_parser_events(job, step, parser.feed(lines))
        pending.extend(lines)
        excess = len(pending) - LOG_RING_LINES
        if excess <= 0:
            return 0
        del pending[:excess]
        return excess

    def _truncate_log(self, log_path: Path) -> None:
        """Cut a log that went over `MAX_LOG_BYTES` back to the limit and end it with a marker.

        The step was killed for writing too much; what it wrote in the meantime must not stay on
        disk, and a reader of the file must be able to tell it was cut (`LOG_TRUNCATED_MARKER`).
        """
        with contextlib.suppress(OSError), log_path.open("r+b") as handle:
            handle.truncate(MAX_LOG_BYTES)
            handle.seek(0, os.SEEK_END)
            handle.write(f"\n{LOG_TRUNCATED_MARKER}\n".encode())

    def _flush(
        self,
        job: str,
        step: str,
        pending: list[str],
        dropped: int,
        log_path: Path,
        offset: int,
        parser: _StepParser | None = None,
    ) -> None:
        """Send whatever lines are still unsent, ignoring the rate limit.

        Reads what is left of the log in bounded chunks, so the end of a very large log is not one
        giant read, and sends once at the end.
        """
        while True:
            offset, lines, _ = self._read_new_lines(log_path, offset)
            if not lines:
                break
            dropped += self._take(job, step, pending, lines, parser)
        self._emit_lines(job, step, pending, dropped)
        if parser is not None:
            # **Held events go out now.** The limiter protects the wire; it is not a reason to lose
            # the last samples of a run, which are the ones a report is built from.
            self._emit_parser_events(job, step, parser.flush())

    def _emit_lines(self, job: str, step: str, lines: list[str], dropped: int = 0) -> None:
        """Stream log lines, at most `LOG_EVENT_MAX_LINES` in one event.

        ``dropped`` is the number of lines the in-memory ring pushed out before these. **It is put
        on the FIRST event only**: those lines precede the ones kept, so a client reads "this many
        are missing, then these". Putting it on every event would make a client add the same number
        repeatedly. With nothing left to send, a non-zero ``dropped`` goes out alone.
        """
        batches = [
            lines[start : start + LOG_EVENT_MAX_LINES]
            for start in range(0, len(lines), LOG_EVENT_MAX_LINES)
        ]
        if not batches and dropped:
            batches = [[]]
        for index, batch in enumerate(batches):
            self._emit(
                "log.lines",
                {
                    "job": job,
                    "step": step,
                    "lines": batch,
                    "dropped": dropped if index == 0 else 0,
                },
            )

    def _emit_parser_events(
        self, job: str, step: str, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """Emit a parser's events with the job and step added.

        The parser names the event and supplies its data; the supervisor adds ``job`` and
        ``step``, so a parser never needs to know which job it is running inside.
        """
        for name, data in events:
            self._emit(name, {"job": job, "step": step, **data})

    def _store_report(self, job_dir: Path, step: Step, parser: _StepParser | None) -> None:
        """Put the parser's report in the state file under ``reports[step.name]``.

        **It is written whatever the exit code**, because a failed run's summary is
        exactly what a user
        wants to see, and a report that appears only on success is a report nobody can rely on.
        """
        if parser is None:
            return
        with self._job_lock(job_dir.name):
            state = self._load_state_file(job_dir)
            if state is None:
                return
            reports = state.setdefault("reports", {})
            reports[step.name] = parser.report()
            self._save(job_dir, state)

    def _read_new_lines(self, path: Path, offset: int) -> tuple[int, list[str], int]:
        """Lines appended since ``offset``, each cut at `MAX_LINE_CHARS`, and the new offset.

        **At most `LOG_READ_CHUNK_BYTES` per call**, so one read is never as big as a log that grew
        fast; the caller loops. A partial last line is **not** consumed: its offset is left before
        it, so a line split across two reads is delivered once and whole. The cut is made on the
        BYTES, before decoding, so a replaced character cannot shift the offset. A single line
        longer than a whole chunk is the one exception: it is taken in pieces (each cut), because
        waiting for its end would never make progress.
        """
        try:
            size = path.stat().st_size
        except OSError:
            return offset, [], offset
        if size <= offset:
            return offset, [], offset
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(LOG_READ_CHUNK_BYTES)
        if not chunk.endswith(b"\n"):
            cut = chunk.rfind(b"\n")
            if cut == -1:
                if len(chunk) < LOG_READ_CHUNK_BYTES:
                    return offset, [], offset
                cut = len(chunk) - 1
            chunk = chunk[: cut + 1]
        new_offset = offset + len(chunk)
        text = chunk.decode("utf-8", errors="replace")
        lines = [line[:MAX_LINE_CHARS] for line in text.splitlines()]
        return new_offset, lines, new_offset

    def _kill_group(self, process: subprocess.Popen[bytes]) -> None:
        """Kill the process group: `SIGTERM`, then `SIGKILL` after the grace period.

        **The group, not the process.** OpenFOAM utilities spawn children — `mpirun`, decomposition
        workers — and killing only the parent leaves a solver holding the case directory.
        """
        if NATIVE_WINDOWS:
            from .windows_process import terminate

            terminate(process.pid)
            process.wait(timeout=CANCEL_GRACE_S)
            return
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            pgid = _getpgid(process.pid)
            _killpg(pgid, signal.SIGTERM)
        deadline = time.monotonic() + CANCEL_GRACE_S
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.05)
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                _killpg(_getpgid(process.pid), _SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=CANCEL_GRACE_S)

    def _parser_params(
        self, factory: Callable[..., Any], step: Step, job_dir: Path
    ) -> dict[str, Any]:
        """The kwargs to build a parser with: the client's `parse_params`, plus `job_dir` when
        wanted.

        **`job_dir` is offered only if the factory accepts it**, because a parser need not:
        `checkMesh`
        summarises what it read and writes nothing, and the test fakes take named options
        only. Passing
        it unconditionally made both of those raise `TypeError`, which the supervisor swallows
        as "the
        parser failed" — so the job ran and produced no events at all, silently.

        **A client cannot override it**: it is set after the client's own params, so a
        `parse_params`
        naming `job_dir` is replaced rather than honoured. The job folder is not a client's to name.
        """
        params = dict(step.parse_params)
        try:
            signature = inspect.signature(factory)
        except (TypeError, ValueError):  # a builtin or a C callable: assume it takes none
            return params
        accepts = "job_dir" in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts and "job_dir" not in params:
            params["job_dir"] = job_dir
            # **And the step's own log name**, because the supervisor writes `log.<step>` and
            # a parser
            # that re-reads its log at `report()` time has to know which file that is. Passing
            # `job_dir`
            # without it would work only for a step called `solve`.
            if "log_name" in signature.parameters:
                params["log_name"] = f"log.{step.name}"
        return params

    def _build_parser(self, step: Step, job_dir: Path) -> _StepParser | None:
        """The parser for a step, or None. **A failure here is the parser's, not the job's.**

        A parser is built from `parse_params`, which a client sends **plus `job_dir`**, which the
        caller supplies: the job folder is the one thing a parser needs that a client must not name
        (it would be a path from outside). A bad parameter must not stop a mesh from running, so the
        failure is recorded in the report and the step proceeds unparsed.
        """
        if step.parse is None:
            return None
        factory = PARSERS.get(step.parse)
        if factory is None:
            # `create` already refused an unknown name, so reaching here means the registry changed
            # **between `create` and the step running** — a redeploy, or a test that swapped it.
            # Recorded rather than raised, for the same reason as above: a parser problem must not
            # fail the job.
            return _StepParser(step.parse, None, error=f"no parser called {step.parse!r}")
        try:
            parser = factory(**self._parser_params(factory, step, job_dir))
        except Exception as exc:
            return _StepParser(step.parse, None, error=f"{type(exc).__name__}: {exc}")
        return _StepParser(step.parse, parser)

    def _set_step(self, job_dir: Path, index: int, outcome: str, rc: int | None = None) -> None:
        """Record one step's final state — **and its exit code** — then emit it.

        ``rc`` is a parameter because the exit code is only known where the process was waited on.
        My first version did not pass it and instead preserved whatever was already in the file,
        which was always ``None``: every finished step recorded `rc: null`, so a caller could not
        tell a clean exit from a crash without reading the log. Measured on the smoke run before the
        fix.
        """
        step_name = ""
        with self._job_lock(job_dir.name):
            state = self._load_state_file(job_dir)
            if state is None:
                return
            with contextlib.suppress(IndexError):
                entry = state["steps"][index]
                entry["state"] = outcome
                entry["ended"] = _now()
                entry["rc"] = rc
            self._save(job_dir, state)
            job = state.get("id", "")
            total = len(state.get("steps", []))
            steps = state.get("steps") or []
            if index < len(steps):
                step_name = steps[index].get("name", "")
        self._emit(
            "job.state",
            {"job": job, "state": outcome, "step": step_name, "index": index, "total": total},
        )

    def _finish(self, job: str, job_dir: Path, failure: dict[str, str] | None) -> None:
        """Write the job's final state and announce it.

        Under the job's lock, reading the record fresh: a `cancel` or a `shutdown` that wrote
        while the last step ended is seen here, not overwritten.
        """
        with self._job_lock(job):
            state = self._load_state_file(job_dir)
            if state is None:
                with self._lock:
                    self._jobs.pop(job, None)
                return
            states = [step.get("state") for step in state.get("steps", [])]
            if state.get("state") == "interrupted":
                # `shutdown` marked it while this thread was ending; that mark is the truth.
                final = "interrupted"
            elif "cancelled" in states or state.get("state") == "cancelling":
                # **WHO asked decides the word** (FIXES-C item 6). `shutdown` writes
                # `cancelling` for
                # the jobs IT is stopping, so the same state means two different things and
                # the reader
                # deserves to be told which: the agent is exiting with this job running
                # (`interrupted`), or the user pressed Cancel and then the app closed (`cancelled`).
                # `_stopping` is set for the whole of `shutdown` and is the only marker of the
                # first.
                #
                # Before this, `shutdown` relabelled a job the USER had cancelled as `interrupted` -
                # telling them the agent stopped their work when they had.
                final = "interrupted" if self._stopping else "cancelled"
            elif failure is not None or "failed" in states:
                final = "failed"
            else:
                final = "done"
            if final != "interrupted":
                state["state"] = final
                state["ended"] = _now()
                state["error"] = failure
                self._save(job_dir, state)
            with self._lock:
                self._jobs.pop(job, None)
        self._emit(
            "job.state",
            {
                "job": job,
                "state": final,
                "step": None,
                "index": 0,
                "total": len(state.get("steps", [])),
            },
        )

    # ------------------------------------------------------------------ control

    def cancel(self, job: str) -> bool:
        """Stop a job. False when it was already finished or is already being cancelled.

        **A queued job goes straight to `cancelled`**: nothing is running, so there is nothing to
        wait for, and it must not be left `cancelling` (which `start` and `delete` both refuse).

        **The record is read, edited and written under the job's lock**, so a final state the job
        thread wrote a moment earlier is seen here (and the call returns False) instead of being
        overwritten with `cancelling`.

        **A group is signalled only when its pid still has the start time that was recorded**
        (`_live_groups`), the same check `recover` makes: a recycled pid must not take a signal.
        """
        self._check_id(job)
        job_dir = self.job_dir(job)
        with self._job_lock(job):
            state = self._load(job)
            current = state.get("state")
            if NATIVE_WINDOWS and current in ("running", "cancelling") and job not in self._jobs:
                raise JobBusy("this job belongs to another agent; cancel it there")
            if current == "queued":
                for entry in state.get("steps", []):
                    entry["state"] = "skipped"
                state["state"] = "cancelled"
                state["ended"] = _now()
                state["error"] = None
                self._save(job_dir, state)
                groups: list[int] = []
            elif current == "running":
                # **`cancelling` is written before any signal**, so a step that ends 143 can tell a
                # cancel from a crash — the record is the shared fact, and `_wait` reads it rather
                # than shared memory.
                state["state"] = "cancelling"
                self._save(job_dir, state)
                groups = self._live_groups(job, state)
            else:
                return False
        self._emit(
            "job.state",
            {
                "job": job,
                "state": state["state"],
                "step": None,
                "index": 0,
                "total": len(state.get("steps", [])),
            },
        )
        if current == "queued":
            return True
        for pgid in groups:
            _signal_group(pgid, signal.SIGTERM)
        # **The record is re-read on every pass, and the groups are signalled as they appear.**
        # A cancel that lands BEFORE `_record_pid` sees no pid at all - that was the
        # documented flaky
        # race: the list was captured once, before this loop, so a pid recorded a moment later was
        # never signalled and the step ran to its timeout. Re-reading is also what makes the SIGTERM
        # reach a step that starts during the grace period.
        deadline = time.monotonic() + CANCEL_GRACE_S
        signalled: set[int] = set(groups)
        while time.monotonic() < deadline:
            state_now = self._load_state_file(job_dir) or {}
            if state_now.get("state") in TERMINAL_STATES:
                return True
            for pgid in self._live_groups(job, state_now):
                if pgid not in signalled:
                    signalled.add(pgid)
                    _signal_group(pgid, signal.SIGTERM)
            time.sleep(0.05)
        # Survivors: the record is read again, because the job thread may have moved on.
        with self._job_lock(job):
            survivors = self._live_groups(job, self._load_state_file(job_dir) or {})
        for pgid in survivors:
            _signal_group(pgid, _SIGKILL)
        return True

    def stop(self, job: str) -> bool:
        """Ask a running job's solver to write the current iteration and end (graceful Stop).

        Creates `STOP_FILE` in the case; the case's `stopAtFile` function sees it at the end of
        the time step, writes and ends with exit 0, so the job finishes `done` and its later steps
        still run. False when the job is not running - a queued or finished job has no solver
        to ask, and the caller falls back to `cancel` for those.
        """
        self._check_id(job)
        job_dir = self.job_dir(job)
        with self._job_lock(job):
            state = self._load(job)
            if state.get("state") != "running":
                return False
            (job_dir / STOP_FILE).touch(exist_ok=True)
            state["stop_requested"] = _now()
            self._save(job_dir, state)
        # **A stop the solver never sees must still stop.** Measured on FoamWin 14 (native
        # Windows): `stopAtFile` is constructed but never fires, and a live controlDict edit is not
        # re-read either, so without this a Stop pressed there would leave the run going for
        # ever. After the grace period the job is cancelled exactly as Stop did before.
        watchdog = threading.Thread(
            target=self._stop_or_cancel, args=(job,), name=f"stop-{job}", daemon=True
        )
        watchdog.start()
        return True

    def _stop_or_cancel(self, job: str) -> None:
        """Cancel ``job`` if it is still running `STOP_GRACE_S` after a `stop`."""
        deadline = time.monotonic() + STOP_GRACE_S
        while time.monotonic() < deadline:
            state = self._load_state_file(self.job_dir(job)) or {}
            if state.get("state") != "running":
                return
            time.sleep(0.25)
        with contextlib.suppress(JobError):
            if self.cancel(job):
                log.warning(
                    "a stop was not honoured in time; the job was cancelled", extra=fields(job=job)
                )

    def times(self, job: str) -> list[str]:
        """The job's written time folders (`0` excluded), oldest first: where a resume starts."""
        self._check_id(job)
        return _time_dirs(self.job_dir(job))

    def _live_groups(self, job: str, state: dict[str, Any]) -> list[int]:
        """The process groups of a job's recorded steps that may safely be signalled.

        A step counts only when its pid is alive **and its start time equals the one recorded**:
        a pid is reused, a pid with its start time is not, and signalling somebody else's group is
        far worse than leaving an orphan. A pid whose start time cannot be read or was never
        recorded is not signalled either. The agent's own group, and group 0 and 1, never are.
        """
        if NATIVE_WINDOWS:
            from .windows_process import owns

            return [
                entry["pid"]
                for entry in state.get("steps", [])
                if isinstance(entry.get("pid"), int) and owns(entry["pid"])
            ]
        groups: list[int] = []
        own = _own_group()
        for entry in state.get("steps", []):
            raw = entry.get("pid")
            if not raw:
                continue
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                continue
            if pid <= 1:
                continue
            # **A group is signalled even when its leader has died**, which is the whole point:
            # a mesher or `mpirun` exits and leaves its children running in the same group, and the
            # old test `not _alive(pid)` skipped exactly that case - so the survivors were never
            # killed and the case directory stayed locked. The leader's death says nothing about
            # whether the group still has members.
            #
            # **The start-time guard still applies WHEN THE LEADER IS ALIVE**, and it is what makes
            # signalling safe: a pid is reused, a pid with its start time is not. With the leader
            # gone there is no start time to compare, so the guard moves to the GROUP: the group is
            # signalled only if it still has a member (`_group_alive`).
            leader_alive = _alive(pid)
            if leader_alive:
                recorded = entry.get("start_ticks")
                actual = _proc_start_ticks(pid)
                if recorded is None or actual is None or recorded != actual:
                    log.warning(
                        "not signalling a pid whose start time does not match",
                        extra=fields(job=job, pid=pid, recorded=recorded, actual=actual),
                    )
                    continue
            try:
                pgid = int(entry.get("pgid") or _getpgid(pid))
            except (ProcessLookupError, PermissionError, OSError, TypeError, ValueError):
                if not leader_alive:
                    continue
                pgid = pid
            if pgid <= 1 or pgid == own or pgid in groups:
                continue
            if not leader_alive and not _group_alive(pgid):
                continue
            groups.append(pgid)
        return groups

    def delete(self, job: str) -> bool:
        """Remove a finished job's folder. **A job that is still running is refused.**

        `rmtree` is called on a path built from a validated id and then contained, which is the
        same rule
        every other path here follows — the difference is that this one deletes a whole tree, so the
        containment matters more, not less.
        """
        self._check_id(job)
        job_dir = self.job_dir(job)
        with self._job_lock(job):
            state = self._load(job)
            if state.get("state") in ("queued", "running", "cancelling"):
                raise JobBusy(
                    f"job {job} is {state.get('state')}; cancel it before deleting",
                    details={"code": "agent.busy", "job": job},
                )
            resolved = job_dir.resolve()
            if not resolved.is_relative_to(self.root.resolve()) or resolved == self.root.resolve():
                raise JobBadParams(f"refusing to delete {job_dir}")
            shutil.rmtree(resolved, ignore_errors=False)
            # The record goes with the folder. **Last**, so a failed `rmtree` leaves a job that can
            # still be listed and deleted again.
            record_file(job_dir).unlink(missing_ok=True)
        with self._lock:
            self._job_locks.pop(job, None)
        return True

    def status(self, job: str) -> dict[str, Any]:
        """The job's state file."""
        return self._load(job)

    def jobs(self) -> list[dict[str, Any]]:
        """Every job, newest first.

        A job folder whose record is unreadable is **listed with an error** rather than
        skipped: a job that vanished from the list while its folder is on disk is worse than one
        that says why.
        """
        found: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return found
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or not JOB_ID_RE.match(entry.name):
                continue
            try:
                data = read_record(record_file(entry))
                if not isinstance(data, dict):
                    raise ValueError("the record is not an object")
            except (OSError, ValueError) as exc:
                found.append({"id": entry.name, "state": "unknown", "error": str(exc)})
                continue
            found.append(data)
        found.sort(key=lambda item: str(item.get("created", "")), reverse=True)
        return found

    def read(self, job: str, path: str, max_bytes: int = 1_048_576) -> dict[str, Any]:
        """A text file inside a job folder, from its start, up to ``max_bytes``.

        **Used for ``postProcessing/`` files**, which is why the path rules are the relaxed ones: an
        OpenFOAM function object names its output directory after its own invocation, and that name
        contains parentheses, ``=`` and commas. Containment and the no-symlink rule are unchanged.

        The cap is the point of the call rather than a courtesy: a `postProcessing` file can be
        gigabytes, and a reader that returns all of it is a reader that hangs a client.
        """
        self._check_id(job)
        if not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_READ_BYTES:
            raise JobBadParams(f"max_bytes must be 1..{MAX_READ_BYTES}; got {max_bytes!r}")
        job_dir = self.job_dir(job)
        self._load(job)
        try:
            target = result_path(job_dir, path)
        except JobPathError as exc:
            raise JobBadParams(str(exc), details={"path": str(path)}) from exc
        if not target.is_file():
            raise JobBadParams(f"job {job} has no file {path}", details={"path": path})
        size = target.stat().st_size
        with target.open("rb") as handle:
            raw = handle.read(max_bytes)
        return {
            "text": raw.decode("utf-8", errors="replace"),
            "size": size,
            "truncated": size > max_bytes,
        }

    def outputs(self, job: str) -> dict[str, Any]:
        """The files under a job's `postProcessing/`: `{files: [{path, size}], truncated}`.

        **What a function object wrote**, so a client can offer a tutorial's own monitors
        (forces, probes, flow rates) without knowing their names in advance (T-311). Read-only and
        bounded: no link is followed or listed, at most `MAX_OUTPUT_DEPTH` folders deep and
        `MAX_OUTPUT_FILES` files, and every path listed is one `read` accepts.
        """
        self._check_id(job)
        job_dir = self.job_dir(job)
        self._load(job)
        root = job_dir / "postProcessing"
        found: list[dict[str, Any]] = []
        truncated = False
        if root.is_dir() and not root.is_symlink():
            stack: list[tuple[Path, int]] = [(root, 0)]
            while stack and not truncated:
                folder, depth = stack.pop()
                for entry in sorted(folder.iterdir(), reverse=True):
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        if depth < MAX_OUTPUT_DEPTH:
                            stack.append((entry, depth + 1))
                        continue
                    rel = entry.relative_to(job_dir).as_posix()
                    try:
                        result_path(job_dir, rel)
                    except JobPathError:
                        continue
                    found.append({"path": rel, "size": entry.stat().st_size})
                    if len(found) >= MAX_OUTPUT_FILES:
                        truncated = True
                        break
        return {"files": sorted(found, key=lambda item: item["path"]), "truncated": truncated}

    def tail(self, job: str, step: str, lines: int = 200) -> dict[str, Any]:
        """The last ``lines`` of a step's log, reading at most the last 1 MiB."""
        self._check_id(job)
        if not isinstance(lines, int) or not 1 <= lines <= MAX_TAIL_LINES:
            raise JobBadParams(f"lines must be 1..{MAX_TAIL_LINES}; got {lines!r}")
        job_dir = self.job_dir(job)
        self._load(job)
        log_path = self._inside(job_dir, f"log.{step}")
        if not log_path.is_file():
            raise JobBadParams(f"job {job} has no log for step {step!r}")
        size = log_path.stat().st_size
        with log_path.open("rb") as handle:
            if size > TAIL_READ_BYTES:
                handle.seek(size - TAIL_READ_BYTES)
                text = handle.read().decode("utf-8", errors="replace")
                # The first line is probably cut; drop it.
                text = text.split("\n", 1)[-1]
            else:
                text = handle.read().decode("utf-8", errors="replace")
        # **Cut here too.** The streamed lines are capped, but `tail` reads the FILE — and my
        # first version returned its lines whole, so a 100,000-character line reached the caller
        # through this path while the same line was cut on the wire. Two readers of one log must
        # apply one rule, and the reason for the rule (a dumped array must not cross a socket
        # whole) is the same for both.
        all_lines = [line[:MAX_LINE_CHARS] for line in text.splitlines()]
        return {
            "lines": all_lines[-lines:],
            "truncated": size > TAIL_READ_BYTES or len(all_lines) > lines,
        }

    # ------------------------------------------------------------------ lifecycle

    def recover(self) -> list[str]:
        """Deal with jobs a previous agent left `running` or `cancelling`. Returns their ids.

        **A job never outlives the agent that started it** (ADR-0024), so this kills rather than
        re-adopts; re-adopting is the SSH task (T-159b). The pid AND its start time must match
        (`_live_groups`): a pid alone can belong to a different program by now, and killing that
        would be far worse than leaving an orphan. Reads only the record.
        """
        recovered: list[str] = []
        for listed in self.jobs():
            if listed.get("state") not in ("running", "cancelling"):
                continue
            job = str(listed.get("id", ""))
            job_dir = self.job_dir(job)
            with self._job_lock(job):
                data = self._load_state_file(job_dir)
                if data is None or data.get("state") not in ("running", "cancelling"):
                    continue
                if NATIVE_WINDOWS and data.get("agent_start_ticks") is not None:
                    owner = data.get("agent_pid")
                    if (
                        isinstance(owner, int)
                        and _proc_start_ticks(owner) == data["agent_start_ticks"]
                    ):
                        continue
                for pgid in self._live_groups(job, data):
                    _signal_group(pgid, signal.SIGTERM)
                data["state"] = "interrupted"
                data["ended"] = _now()
                data["error"] = {
                    "code": "agent.interrupted",
                    "message": "the agent that started this job is gone",
                }
                self._save(job_dir, data)
            recovered.append(job)
            self._emit(
                "job.state",
                {
                    "job": job,
                    "state": "interrupted",
                    "step": None,
                    "index": 0,
                    "total": len(data.get("steps", [])),
                },
            )
        return recovered

    def shutdown(self) -> None:
        """Stop every job and mark it `interrupted`. T-144c calls this at desktop exit.

        **One deadline for all of them.** `SIGTERM` goes to every group first, the wait happens
        once (`CANCEL_GRACE_S`), and every group still there is then `SIGKILL`led. Cancelling job
        after job would give each its own grace period, so a second job that ignored `SIGTERM`
        was reached only after the first had used up the time the caller allowed.
        """
        self._stopping = True
        marked: list[str] = []
        groups: list[int] = []
        for listed in self.jobs():
            job = str(listed.get("id", ""))
            if NATIVE_WINDOWS and job not in self._jobs:
                continue
            if listed.get("state") not in ("running", "cancelling", "queued"):
                continue
            job_dir = self.job_dir(job)
            with self._job_lock(job):
                data = self._load_state_file(job_dir)
                if data is None or data.get("state") not in ("running", "cancelling", "queued"):
                    continue
                if data.get("state") == "running":
                    # `cancelling` before any signal, so a step that ends 143 reads as a cancel.
                    data["state"] = "cancelling"
                    self._save(job_dir, data)
                groups.extend(g for g in self._live_groups(job, data) if g not in groups)
            marked.append(job)
        for pgid in groups:
            _signal_group(pgid, signal.SIGTERM)
        deadline = time.monotonic() + CANCEL_GRACE_S
        while time.monotonic() < deadline and any(_group_exists(g) for g in groups):
            time.sleep(0.05)
        for pgid in groups:
            if _group_exists(pgid):
                _signal_group(pgid, _SIGKILL)
        with self._lock:
            threads = [running.thread for running in self._jobs.values()]
        join_by = time.monotonic() + 2.0
        for thread in threads:
            thread.join(timeout=max(0.0, join_by - time.monotonic()))
        for job in marked:
            self._mark_interrupted(job)

    def _mark_interrupted(self, job: str) -> None:
        """Write `interrupted` for a job `shutdown` stopped, unless it had already finished.

        **`cancelled` is a finished state** (FIXES-C item 6). It was missing from the guard,
        so a job
        the user had deliberately cancelled was relabelled `interrupted` at desktop exit -
        telling them
        the agent stopped their work when in fact they had. The guard is the terminal set
        rather than a
        hand-written list, so a state added later cannot be forgotten the same way.
        """
        job_dir = self.job_dir(job)
        with self._job_lock(job):
            current = self._load_state_file(job_dir)
            if current is None or current.get("state") in TERMINAL_STATES:
                return
            current["state"] = "interrupted"
            current["ended"] = _now()
            current["error"] = {
                "code": "agent.shutdown",
                "message": "the agent is shutting down",
            }
            self._save(job_dir, current)

    # ------------------------------------------------------------------ artifacts

    def recorded_artifact(self, job: str, name: str) -> dict[str, Any] | None:
        """What the agent's own record says it wrote as artifact ``name``, or None.

        **The only thing that makes a cached artifact trustworthy.** The file and
        `artifacts/index.json` are on disk where a bug or a hostile client could plant them; the
        record is written only by `record_artifact`, from the extraction the agent ran itself.
        """
        entry = (self._load(job).get("artifacts") or {}).get(name)
        return dict(entry) if isinstance(entry, dict) else None

    def record_artifact(self, job: str, name: str, size: int, sha256: str) -> None:
        """Record that the agent wrote artifact ``name`` with this size and digest."""
        job_dir = self.job_dir(job)
        with self._job_lock(job):
            state = self._load(job)
            artifacts = state.setdefault("artifacts", {})
            artifacts[name] = {"size": int(size), "sha256": str(sha256)}
            self._save(job_dir, state)

    def forget_artifacts(self, job: str, names: list[str]) -> None:
        """Drop the records for artifacts whose FILES are gone (Opus recheck item 4c).

        **The disk was bounded and the bookkeeping was not.** Pruning slice files left their entries
        in `state.json`, so a long drag grew the index without limit and left it listing artifacts
        that no longer existed - an index of lies, which is worse than no index.

        **One save for the whole batch**, because the caller has just deleted several files and a
        write per name would turn a cleanup into the expensive part of the request.
        """
        if not names:
            return
        job_dir = self.job_dir(job)
        with self._job_lock(job):
            state = self._load(job)
            artifacts = state.get("artifacts")
            if not isinstance(artifacts, dict):
                return
            for name in names:
                artifacts.pop(name, None)
            self._save(job_dir, state)


class _StepParser:
    """One step's parser, plus the rate limit and the coalescing the supervisor applies to it.

    **The limit is per event NAME, not per parser.** A parser that emits `monitor.residual`
    twenty times a second would drown the wire; one that emits five different event names once
    each is fine. So the clock is kept per name.

    **Coalescing keeps data.** When two events of the same name arrive inside the interval, the
    one that
    is dropped must not take its contents with it: a `blocks` list (T-213's residuals)
    is concatenated
    rather than replaced, because keeping the last would silently lose the samples in between.
    Every other
    payload keeps the newest, which is what a status event wants.
    """

    def __init__(self, name: str, parser: Any, error: str = "") -> None:
        self.name = name
        self.parser: Any = parser
        #: Set when the parser could not be built; reported instead of a summary.
        self.error = error
        self.report_data: dict[str, Any] = {}
        self._last_sent: dict[str, float] = {}
        self._held: dict[str, tuple[str, dict[str, Any]]] = {}

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        """Run the parser over new lines and return what may be sent now.

        A parser exception becomes an error report and stops further parsing, rather than
        propagating: a parser is reading output we did not write, and a wording change in a
        point release must not turn a successful mesh into a failed job.
        """
        if self.parser is None or self.error:
            return []
        try:
            events = self.parser.feed(lines)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("log parser failed", extra=fields(parser=self.name, error=str(exc)))
            return []
        out: list[tuple[str, dict[str, Any]]] = []
        for event, data in events or []:
            if self._due(event):
                out.append((event, data))
            else:
                self._hold(event, data)
        return out

    def _due(self, event: str) -> bool:
        """Whether an event of this name may be emitted now."""
        now = time.monotonic()
        last = self._last_sent.get(event, 0.0)
        if now - last >= PARSER_EVENT_INTERVAL_S:
            self._last_sent[event] = now
            return True
        return False

    def _hold(self, event: str, data: dict[str, Any]) -> None:
        """Keep a coalesced event, concatenating a `blocks` list if there is one."""
        held = self._held.get(event)
        if held is None:
            self._held[event] = (event, dict(data))
            return
        merged = dict(held[1])
        for key, value in data.items():
            if key == "blocks" and isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key] = [*merged[key], *value]
            else:
                merged[key] = value
        self._held[event] = (event, merged)

    def flush(self) -> list[tuple[str, dict[str, Any]]]:
        """Everything held back, sent at the end of the step.

        **Held events are not dropped.** The limiter exists to protect the wire, not to lose
        samples; a step that ends sends what it was holding.
        """
        held = list(self._held.values())
        self._held.clear()
        for event, _ in held:
            self._last_sent[event] = time.monotonic()
        return held

    def report(self) -> dict[str, Any]:
        """The step's summary for `state.json`."""
        if self.error:
            return {"error": self.error}
        if self.parser is None:
            return {}
        try:
            data = self.parser.report()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return dict(data) if isinstance(data, dict) else {}


@dataclass
class _Running:
    """A job's thread, kept so `start` can refuse a second one."""

    thread: threading.Thread


def _is_size(value: object) -> bool:
    """Whether a value read from a record is a plain non-negative integer size."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _trusted_of(state: dict[str, Any]) -> dict[str, int]:
    """The ``polymesh_trusted`` map of a record, keeping only well-formed polyMesh entries.

    A record is agent-written, but it is read back at the point of use and checked there: a
    malformed entry is dropped (so its file is scanned), never believed.
    """
    raw = state.get("polymesh_trusted")
    if not isinstance(raw, dict):
        return {}
    return {
        name: size
        for name, size in raw.items()
        if isinstance(name, str) and name.startswith(POLYMESH_PREFIX) and _is_size(size)
    }


def record_file(job_dir: Path) -> Path:
    """The record of the job whose folder is ``job_dir``: ``<root>/.records/<job>.json``.

    **Outside the job folder, on purpose.** The record decides what `start` runs and whom
    `cancel` signals, so it must not be a path a client can write (`job.put`, `copy_from`).
    """
    return job_dir.parent / RECORDS_DIR / f"{job_dir.name}.json"


def read_record(path: Path) -> Any:
    """The JSON in a record file. A reader that races the atomic replace retries.

    On POSIX a rename never disturbs an open reader; Windows refuses the rename while a reader has
    the file open, so the writer retries (`write_state`) and so does this.
    """
    import json

    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01)
    raise AssertionError("unreachable")


def write_state(job_dir: Path, state: dict[str, Any]) -> None:
    """Write a job's record atomically, mode 0600, into ``<root>/.records/``.

    A module-level function as well as the method, because the tests build a record by hand
    to put a job in a state a test cannot reach any other way — `running` with a live orphan, or
    a pid whose start time does not match. Testing `recover` otherwise means crashing an agent
    on purpose.
    """
    import json

    target = record_file(job_dir)
    if not target.parent.is_dir():
        # Created on the first record, not with the store: a refused `create` leaves the jobs root
        # exactly as it found it. Mode 0700, like the root.
        target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            target.parent.chmod(0o700)
    temp = target.parent / f".{target.stem}.{secrets.token_hex(4)}.tmp"
    temp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    with contextlib.suppress(OSError):
        temp.chmod(0o600)
    for attempt in range(5):
        try:
            temp.replace(target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01)


def iter_states() -> Iterable[str]:
    """The job states, for a caller that wants to validate one."""
    return STATES


def _group_alive(pgid: int) -> bool:
    """Whether a process group still has a member. `killpg(pgid, 0)` is the test.

    **The leader's death does not empty a group.** A job whose `mpirun` or mesher exits leaves its
    children running in the same group, and those children are what hold the case directory. This is
    how `_live_groups` decides a leaderless group is still worth signalling.
    """
    try:
        _killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
