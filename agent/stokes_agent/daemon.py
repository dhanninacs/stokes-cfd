"""The detached agent: one per (user, host), outliving the client (T-159a).

**Why detached.** The owner's decision is that an SSH job survives the app exiting, while a
local job dies with it. A mesh on a build machine can take an hour, and a user who closes their
laptop should not lose it. So this mode is the opposite of the in-process one in every respect
that matters: it `setsid`s away from the ssh session, it writes what a later client needs to
find it, and it lives until nothing is using it.

## The secret is never an argument

`ps` shows every process's arguments to every user on the machine, so a token passed as
`--token` is a token published. The agent therefore **generates its own secret** and writes it to
a file that only its owner can read; a client reads it over ssh when it attaches. This is why
claude-win2 rejected the `--token` form I first proposed, and he was right.

## What a later client reads

`session.json` holds `{pid, port, started, protocol}` - everything needed to decide whether to
attach or to start fresh. It carries **no secret**. `secret` holds the shared key, 32 random
bytes as hex, mode 0600.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import signal
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stokes.log import fields, get_logger

log = get_logger(__name__)

#: Where the state lives when `--state-dir` is not given.
#:
#: **`$STOKES_AGENT_HOME/agent`, else `~/.stokes/agent`** - the same root the job folders use, so
#: one folder answers "what has the agent written". This was the literal `~/.stokes/agent`, which
#: put the agent's SECRET and session file outside a configured root: measured, the state folder
#: stayed behind in `~/.stokes` after `STOKES_AGENT_HOME` was pointed elsewhere, which is exactly
#: the scattering this is meant to end.
DEFAULT_STATE_DIR = "agent"
#: The two files a client reads. **`SECRET_FILE` is a file NAME, not a secret value** - the linter's
#: hardcoded-password rule fires on the word, and the value it would flag is the string `"secret"`.
SESSION_FILE = "session.json"
SECRET_FILE = "secret"  # noqa: S105
#: Bytes of entropy in the agent's own secret. 32 bytes, hex-encoded, so 64 characters.
SECRET_BYTES = 32
#: **12 hours**, the spec's number: long enough for an overnight mesh, short enough that an agent
#: nobody is coming back to does not hold its port and its case directories for ever.
IDLE_EXIT_S = 12 * 60 * 60


@dataclass(frozen=True)
class Session:
    """What `session.json` holds. **No secret** - that is a separate file."""

    pid: int
    port: int
    started: float
    protocol: int

    def to_json(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "port": self.port,
            "started": self.started,
            "protocol": self.protocol,
        }

    @classmethod
    def from_json(cls, data: Any) -> Session | None:
        """A session from parsed JSON, or None when it is not one.

        **Lenient on purpose**: a stale or damaged file means "start fresh", which is the same
        answer as "no file" and needs no special case.
        """
        if not isinstance(data, dict):
            return None
        pid, port = data.get("pid"), data.get("port")
        if not isinstance(pid, int) or not isinstance(port, int) or port <= 0:
            return None
        started = data.get("started")
        protocol = data.get("protocol")
        return cls(
            pid=pid,
            port=port,
            started=float(started) if isinstance(started, (int, float)) else 0.0,
            protocol=int(protocol) if isinstance(protocol, int) else 0,
        )


def state_dir(argument: str | None = None) -> Path:
    """The directory to keep state in.

    `~` is expanded because `--state-dir ~/.stokes/agent` arrives as that literal string: an
    `ssh ... -- cmd` argument list is not a shell, so nothing expands it, and a quoted `~` is
    the ordinary way to write it.

    **RESOLVED TO AN ABSOLUTE PATH HERE, ONCE** (Opus residue 7). A relative argument used to be
    returned as given, so the agent's state folder depended on the working directory of whatever
    started it - measured: `.stokes/agent` names a different folder before and after a `chdir`, and
    `Path` keeps the relative form. The agent detaches and then lives for hours, so anything that
    changed the process's working directory later (a test, an imported library, a future caller)
    would move its state out from under a client that had already been told where it is.

    `resolve()` also settles a symlinked home and a `..` in the path, so the folder the session
    record names is the folder that is actually used.
    """
    raw = argument or os.environ.get("STOKES_AGENT_STATE_DIR")
    if not raw:
        # **Resolved against the agent's own root, not the process's cwd.** `DEFAULT_STATE_DIR` is a
        # NAME because the root comes from `$STOKES_AGENT_HOME`; using it bare would make the state
        # folder relative, which is the very thing the paragraph above forbids.
        from stokes_agent.jobs import default_root

        return (default_root().parent / DEFAULT_STATE_DIR).resolve()
    return Path(raw).expanduser().resolve()


def read_session(folder: Path) -> Session | None:
    """The recorded session, or None when there is none or it is unreadable."""
    try:
        data = json.loads((folder / SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Session.from_json(data)


def write_session(folder: Path, session: Session) -> Path:
    """Write `session.json` atomically. **A reader never sees a half-written file.**"""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / SESSION_FILE
    temp = folder / f".{SESSION_FILE}.tmp"
    temp.write_text(json.dumps(session.to_json()) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


#: The permission bits a secret may not carry. **Group OR other, any of them** - this file is the
#: whole authentication of the channel, so a second reader is a second holder of it.
SECRET_FORBIDDEN_BITS = 0o077


def check_secret_permissions(folder: Path) -> str:
    """A problem with the secret file's permissions, or `""`. (Opus L4.)

    **A secret that is readable by anyone else has already leaked**, and the agent cannot tell
    whether that mattered. Refusing is the honest answer: the file can be created again, and a
    session that carries on with a known-compromised token is worse than one that stops.

    **Windows has no mode bits in this sense** - `os.stat` reports a rough approximation and every
    file looks group-readable - so the check applies where the bits mean something, which is where
    the detached agent actually runs.
    """
    if os.name == "nt":  # pragma: no cover - depends on the platform
        return ""
    secret = folder / SECRET_FILE
    try:
        mode = stat.S_IMODE(secret.stat().st_mode)
    except OSError:
        return ""
    if mode & SECRET_FORBIDDEN_BITS:
        return (
            f"{secret} is mode {mode:04o}: readable by users other than you. It is the whole "
            f"authentication of this channel, so it has been treated as exposed - delete it and "
            f"start a fresh agent with `rm {secret}`"
        )
    return ""


def read_secret(folder: Path) -> str:
    """The agent's secret, or `""` when there is none **or when it is exposed**.

    **A secret readable by someone else has already leaked, so it is not returned** (Opus L4): a
    session that authenticated with a token anyone on the machine could have copied would carry on
    as though nothing had happened.

    **`""` rather than an exception, because of where this is called from**: the detached agent runs
    this at start-up as `read_secret(folder) or new_secret()` and then writes it back, so an empty
    answer **rotates the secret** - which is the right response to a leak, and better than refusing
    to start. The problem is logged by `write_secret` on the way through, so it is not silent.
    """
    problem = check_secret_permissions(folder)
    if problem:
        log.warning("discarding an exposed agent secret", extra=fields(reason=problem))
        return ""
    try:
        return (folder / SECRET_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_secret(folder: Path, secret: str) -> Path:
    """Write the secret **0600 by way of a 0600 temp file**, so it is never briefly readable.

    `os.open` with the mode rather than `write_text` + `chmod`: the latter creates the file
    world-readable and closes the window afterwards, and this file is the whole
    authentication of the channel.
    """
    ensure_private_dir(folder)
    target = folder / SECRET_FILE
    temp = folder / f".{SECRET_FILE}.tmp"
    # **`O_NOFOLLOW` and `O_EXCL`** (Opus L4). Without them a SYMLINK planted at the temp path was
    # followed, and the secret was written into whatever it pointed at - measured: a file reading
    # "keep me" came back as the secret. `O_NOFOLLOW` refuses to open a symlink at all; `O_EXCL`
    # refuses to reuse an existing name, so a stale temp file is a loud failure rather than a
    # silently reused one. A stale file is removed first, because the failure it causes must be
    # about an ATTACK, not about a previous crash.
    with contextlib.suppress(OSError):
        if temp.is_symlink():
            temp.unlink()
    # **`O_NOFOLLOW` where the platform has it.** Windows has no such flag; the `is_symlink()`
    # check above is the protection there, and `O_EXCL` still refuses to reuse an existing name.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    if hasattr(os, "O_CLOEXEC"):
        # **Not inherited by a child**, so a spawned helper cannot hold the secret open.
        flags |= os.O_CLOEXEC
    descriptor = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secret + "\n")
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    temp.replace(target)
    return target


def new_secret() -> str:
    """32 random bytes, hex. **Hex and not base64**, so the value survives being copied by hand."""
    return secrets.token_hex(SECRET_BYTES)


def _process_alive_windows(pid: int) -> bool:
    """Query a Windows process handle without sending a signal."""
    # On Windows, signal 0 is CTRL_C_EVENT, not a POSIX existence check.
    # Querying a handle avoids sending a console event to the process group.
    import ctypes
    from ctypes import wintypes

    if pid > 0xFFFFFFFF:
        return False
    # These are Windows-only ctypes attributes; mypy runs on Linux, so look them up by name.
    win_dll = vars(ctypes)["WinDLL"]
    get_last_error = vars(ctypes)["get_last_error"]
    kernel = win_dll("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return bool(get_last_error() == 5)  # access denied: conservatively treat as alive
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    """Whether `pid` is a live process, without signalling it.

    A `PermissionError` means the process exists and belongs to someone else, which for our purposes
    is still "not ours to reuse" - but the caller compares against the recorded pid of a process it
    started as the same user, so it is treated as alive and the attach attempt decides.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _process_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def detach() -> None:
    """`setsid` away from the controlling terminal, so the agent outlives the ssh session.

    **Called after the state directory is prepared and before serving.** `setsid` fails when the
    caller is already a process-group leader, which is the ordinary case for a forked child - and
    that is fine, because a process that is already its own group leader has already achieved what
    this is for.
    """
    # **A no-op on Windows**, which has neither `setsid` nor `SIGHUP`: the detached agent runs on
    # the SSH host, but the process must still start where the tests (and a local run) invoke it.
    setsid = getattr(os, "setsid", None)
    if setsid is not None:
        with contextlib.suppress(OSError):
            setsid()
    # **A second fork would be the textbook daemon**, and is deliberately not done: the parent here
    # is `ssh`, which waits for this process to exit, and exiting immediately would race the caller
    # reading the state files. The caller polls for `session.json` instead.
    if sys.stdin is not None:
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.close()
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is not None:
        with contextlib.suppress(OSError, ValueError):
            signal.signal(sighup, signal.SIG_IGN)


def idle_deadline(started: float, idle_s: int = IDLE_EXIT_S) -> float:
    """When an agent that has never had a client should exit. **Pure**, so it is testable."""
    return started + idle_s


def should_exit_idle(now: float, last_active: float, idle_s: int = IDLE_EXIT_S) -> bool:
    """Whether an idle agent has outlived its welcome.

    **Idle means no connected client AND no running job** (the spec's rule). The caller decides what
    "no running job" is; this answers only the time half, so the two conditions stay separate and
    both visible at the call site.
    """
    return now - last_active >= idle_s


def ensure_private_dir(folder: Path) -> None:
    """Make the state folder, and **keep it to its owner** (Opus L4).

    The folder holds the secret and the session record, so its permissions are part of the
    secret's protection: a 0755 folder lets anyone list what is there and read anything created
    carelessly.
    A folder that already exists is **narrowed**, not recreated - the agent may be attaching to a
    directory a previous version made.

    **Best effort on the narrowing**, because a folder the user deliberately shares is their
    business; what matters is that we never CREATE a permissive one.
    """
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":  # pragma: no cover - depends on the platform
        return
    with contextlib.suppress(OSError):
        current = stat.S_IMODE(folder.stat().st_mode)
        if current & SECRET_FORBIDDEN_BITS:
            folder.chmod(current & ~SECRET_FORBIDDEN_BITS)
