"""A host that is a WSL distribution (the Settings spec §5, ADR-0024 §3.4 step (b)).

**The distribution is a host like any other, not a command wrapper.** `wsl.exe -d <distro> --
<argv>` runs one command and exits; a mesh needs a long-lived agent with a job store,
subscriptions and a port. So this runner starts the agent **once** inside the distro, reads the
record it prints on its
stdout pipe (host, port, token), and then speaks the same protocol `LocalRunner` and `SshRunner`
speak. Above this file nothing knows which of the three it is talking to.

## Where the bytes go

The project stays on Windows; **the case crosses the socket once and the agent writes it inside the
distro**, through `job.create`/`job.put`, to its own `~/.stokes/agent` (ext4). Nothing meshes over
the `/mnt/<drive>` bridge, which is the slow path for the thousands of small files a mesh writes.
The `/mnt` path appears only in `PYTHONPATH`, where it is read once at process start.

## Why the agent is a child, and what closing means

A WSL agent belongs to this process, like the local one: a window that closes must not leave a
mesher holding a case directory. `close` sends the authenticated `agent.shutdown` method (the agent
stops its jobs and then its loop) and terminates the `wsl.exe` child if it has not exited. The
server is started by `python3 -m stokes_agent --serve`, whose own docstring says the printed token
is for a launcher reading the pipe - it is never an argument and never appears in a process list.

## What was measured on the machine this was written for

`wsl.exe -d Ubuntu-24.04 -- python3 -m stokes_agent --serve` printed `127.0.0.1:<port>`; Windows
reached it at that address through WSL's localhost forwarding, and `host.probe` returned the
distro's `/opt/openfoam14` install. Killing `wsl.exe` killed the Linux agent with it, which is why
the shutdown method exists for the graceful case and termination is the fallback.

**The agent's Python dependencies are the distro's.** The code is importable through `PYTHONPATH`
(the same checkout or installation the desktop runs from, translated to `/mnt/<drive>/...`); the
interpreter and its packages are the distro's own. A distro missing `msgpack` fails at startup with
the agent's message in `why`, not with a mesh that never starts.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from stokes.log import fields, get_logger
from stokes.run.errors import RunnerUnavailable
from stokes.run.hosts import WSL_PREFIX

log = get_logger(__name__)

#: How long the thread gets to reach a listening agent. A cold distribution has to boot.
START_TIMEOUT_S = 150.0
#: How long the agent gets to print its record once the process exists.
RECORD_TIMEOUT_S = 120.0
#: How long a call's reply gets beyond the call's own timeout, for the round trip.
CALL_SLACK_S = 30.0
#: How long `close` waits for the loop thread, after the shutdown request.
JOIN_TIMEOUT_S = 10.0

#: A distribution name from `wsl.exe -l -q`. **It may contain spaces and capitals** (`Ubuntu-24.04`
#: is mixed case), so the SSH host-name rule would reject a real distribution. It may not start with
#: `-`: this string becomes the argument after `-d`, and a leading dash would be read as a flag.
DISTRO_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,127}")

#: The reason a machine without `wsl.exe` gives. A WSL row is only listed on Windows, so this is
#: mostly a defensive answer for a runner built by hand.
NO_WSL_WHY = "WSL is not available on this computer"

#: What a spawned agent is: the argv, and the process a caller can read and stop.
Spawn = Callable[[list[str]], Awaitable[asyncio.subprocess.Process]]


def _find_wsl() -> Path | None:
    """`wsl.exe`, from PATH or the system directory. None off Windows."""
    if os.name != "nt":
        return None
    found = shutil.which("wsl.exe") or shutil.which("wsl")
    if found:
        return Path(found)
    # A GUI process launched from a shortcut can have a PATH without System32; the system directory
    # is still where WSL lives, so check it before saying WSL is missing.
    root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
    candidate = Path(root) / "System32" / "wsl.exe"
    return candidate if candidate.is_file() else None


def _to_wsl_path(path: Path) -> str:
    """A Windows path as the distribution sees it: `D:\\a\\b` -> `/mnt/d/a/b`.

    **Raises rather than inventing a path.** A UNC share has no `/mnt` spelling, and a runner that
    guessed one would fail later with an import error that names the wrong thing.
    """
    if os.name != "nt":
        return path.as_posix()
    parts = path.parts
    drive = parts[0] if parts else ""
    if len(drive) != 3 or drive[1] != ":" or drive[2] != "\\":
        raise RunnerUnavailable(
            f"cannot use {path} inside WSL: it is not on a drive WSL mounts",
            details={"code": "run.unavailable"},
        )
    return f"/mnt/{drive[0].lower()}/" + "/".join(parts[1:])


def _agent_roots() -> list[Path]:
    """The directories a Linux interpreter needs on `PYTHONPATH` to import the agent.

    `stokes_agent` lives in `agent/` (or in site-packages) and imports `stokes.errors` and
    `stokes.log` from the application, so both their parents are needed. A Windows
    `site-packages` is **not** usable by a Linux interpreter - its compiled modules are the wrong
    format - so it is left out and the distro is expected to have the agent installed.
    """
    import stokes
    import stokes_agent

    roots: list[Path] = []
    for module in (stokes_agent, stokes):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        root = Path(filename).resolve().parent.parent
        if any(part in ("site-packages", "dist-packages") for part in root.parts):
            continue
        if root not in roots:
            roots.append(root)
    return roots


class WslRunner:
    """An agent inside a WSL distribution, behind the `Runner` protocol.

    **Nothing starts in `__init__`.** Listing hosts must not boot a distribution, so the first
    `call` builds the loop, the `wsl.exe` child and the client, once.
    """

    def __init__(
        self,
        distro: str,
        *,
        wsl: Path | str | None = None,
        python: str = "python3",
        spawn: Spawn | None = None,
        pythonpath: str | None = None,
    ) -> None:
        if not DISTRO_PATTERN.fullmatch(distro):
            raise RunnerUnavailable(
                f"{distro!r} is not a WSL distribution name",
                details={"code": "run.unavailable"},
            )
        self.distro = distro
        self.name = f"{WSL_PREFIX}{distro}"
        self._wsl = Path(wsl) if wsl is not None else _find_wsl()
        self._python = python
        self._spawn = spawn or self._spawn_wsl
        self._pythonpath = pythonpath
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._proc: asyncio.subprocess.Process | None = None
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._closed = False
        self._failure = ""
        self._stderr_tail: list[str] = []
        # **A desktop that exits must not leave an agent (or its jobs) behind.** `close` is
        # idempotent, so an explicit close and this one can both run.
        atexit.register(self.close)

    # ------------------------------------------------------------------ availability

    @property
    def available(self) -> bool:
        """Whether this host can be used at all.

        **True when there is a launcher**, even before the distribution has been probed: whether
        the distro has a supported OpenFOAM is `host.probe`'s answer, exactly as it is for
        `LocalRunner`. A start that failed flips this to False, so the row keeps the reason.
        """
        return self._wsl is not None and not self._closed and not self._failure

    @property
    def why(self) -> str:
        """Why the host cannot be used, for a user. `""` when it can.

        **The failure is reported before the closed flag**, like `SshRunner`: a failed start leaves
        both set, and the reason is the half that tells a user what to fix.
        """
        if self._failure:
            return self._failure
        if self._wsl is None:
            return NO_WSL_WHY
        if self._closed:
            return "this runner has been closed"
        return ""

    # ------------------------------------------------------------------ command

    def command(self) -> list[str]:
        """The argv that starts the agent in the distribution.

        **The record is read from the pipe, so the token is not an argument.** `env` is used rather
        than a shell: the distribution's `sh` would split a Windows path with a space in it, and
        `env KEY=value` passes it as one argument.
        """
        if self._wsl is None:
            raise RunnerUnavailable(NO_WSL_WHY, details={"code": "run.unavailable"})
        prefix = [str(self._wsl), "-d", self.distro, "--"]
        pythonpath = self.pythonpath()
        if pythonpath:
            prefix += ["env", f"PYTHONPATH={pythonpath}"]
        return [*prefix, self._python, "-m", "stokes_agent", "--serve"]

    def pythonpath(self) -> str:
        """The `PYTHONPATH` the distro needs, in its own path spelling. Empty if the agent is
        installed there (a wheel install on Windows cannot supply Linux modules).
        """
        if self._pythonpath is not None:
            return self._pythonpath
        roots = [_to_wsl_path(root) for root in _agent_roots()]
        return ":".join(roots)

    async def _spawn_wsl(self, command: list[str]) -> asyncio.subprocess.Process:
        """Start `wsl.exe` with no console window.

        **`CREATE_NO_WINDOW` is not cosmetic**: the app runs under `pythonw`, so a console child
        would flash a black window on every first mesh. The agent's own output is on the pipe, not
        on a console.
        """
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            # Windows-only attribute; `getattr` keeps one source type-checking on Linux too.
            kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        proc = await asyncio.create_subprocess_exec(*command, **kwargs)
        if os.name == "nt":
            # **The child dies with the host**, even when `close` never runs (a crash, a kill from
            # Task Manager): otherwise each such exit left a `wsl.exe` and its agent running.
            from stokes_agent.windows_process import confine

            try:
                confine(proc.pid)
            except OSError as exc:
                log.warning("could not tie wsl.exe to this process", exc_info=exc)
        return proc

    # ------------------------------------------------------------------ lifecycle

    def _ensure_started(self) -> None:
        """Start the child agent and attach, once. Safe from any thread."""
        with self._lock:
            if self._closed or self._failure:
                raise RunnerUnavailable(self.why, details={"code": "run.unavailable"})
            if self._loop is not None:
                return
            ready = threading.Event()
            failure: list[BaseException] = []

            def boot() -> None:
                try:
                    asyncio.run(self._run(ready))
                except BaseException as exc:
                    failure.append(exc)
                    ready.set()

            self._thread = threading.Thread(
                target=boot, name=f"stokes-agent-wsl-{self.distro}", daemon=True
            )
            self._thread.start()
            if not ready.wait(timeout=START_TIMEOUT_S):
                self._failure = (
                    f"the agent in {self.distro} did not start within {START_TIMEOUT_S:.0f}s"
                )
            elif failure:
                self._failure = _explain(failure[0], self.distro)
            if failure or not ready.is_set():
                self._closed = True
                raise RunnerUnavailable(self._failure, details={"code": "run.unavailable"})

    async def _run(self, ready: threading.Event) -> None:
        """The loop body: spawn, read the record, attach, pump events, wait to be stopped."""
        from stokes_agent.client import AgentClient

        proc: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[None] | None = None
        try:
            proc = await self._spawn(self.command())
            self._proc = proc
            stderr_task = asyncio.create_task(self._drain_stderr(proc))
            record = await self._read_record(proc)
            client = await AgentClient.connect(record["host"], record["port"], record["token"])
            self._client = client
            self._loop = asyncio.get_running_loop()
            self._pump = asyncio.create_task(_deliver(client, self._listeners, self._lock))
            ready.set()
            await self._stop_event.wait()
        except BaseException:
            self._terminate(proc)
            raise
        finally:
            if stderr_task is not None:
                stderr_task.cancel()
                with contextlib.suppress(Exception):
                    await stderr_task

    async def _read_record(self, proc: asyncio.subprocess.Process) -> dict[str, Any]:
        """One JSON line on stdout: `{host, port, token}`. **Not the process list.**

        A failure here includes the agent's stderr tail, because the useful message (a missing
        module, a broken `/opt/openfoam*`) is printed there rather than in a status code.
        """
        assert proc.stdout is not None
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=RECORD_TIMEOUT_S)
        except TimeoutError as exc:
            raise RunnerUnavailable(
                self._start_failure(f"the agent in {self.distro} printed no address in time"),
                details={"code": "run.unavailable"},
            ) from exc
        if not line:
            # Give the concurrent stderr drain a turn, so the reason a distro failed (a missing
            # module, a broken environment) is in the message rather than just "it exited".
            await asyncio.sleep(0.05)
            raise RunnerUnavailable(
                self._start_failure(
                    f"the agent in {self.distro} exited before printing an address"
                ),
                details={"code": "run.unavailable"},
            )
        try:
            record = json.loads(line.decode("utf-8", "replace"))
            host = str(record.get("host") or "")
            port = int(record.get("port") or 0)
            token = str(record.get("token") or "")
        except (ValueError, TypeError, AttributeError) as exc:
            raise RunnerUnavailable(
                self._start_failure(f"the agent in {self.distro} printed an unreadable address"),
                details={"code": "run.unavailable"},
            ) from exc
        # **Loopback only.** The agent refuses to bind elsewhere, and the desktop reaches it through
        # WSL's localhost forwarding. A record naming any other host is not this child.
        if host not in ("127.0.0.1", "localhost", "::1") or not 1 <= port <= 65535 or not token:
            raise RunnerUnavailable(
                self._start_failure(f"the agent in {self.distro} printed an invalid address"),
                details={"code": "run.unavailable"},
            )
        return {"host": "127.0.0.1", "port": port, "token": token}

    def _start_failure(self, message: str) -> str:
        """The message plus the last thing the agent said on stderr."""
        tail = self._stderr_tail[-3:]
        return f"{message}: {' | '.join(tail)}"[:400] if tail else message

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """Keep the child's stderr moving and its last lines for a failure message.

        **An unread pipe is a hung agent**: a traceback plus a job's warnings can fill the OS
        buffer, and the agent would block writing to it with no client able to see why.
        """
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if not text:
                continue
            self._stderr_tail.append(text)
            del self._stderr_tail[:-10]
            log.debug("wsl agent said", extra=fields(distro=self.distro, line=text[:300]))

    @property
    def _stop_event(self) -> asyncio.Event:
        """The event the loop waits on. Created lazily because it must belong to the loop."""
        existing = getattr(self, "_stop", None)
        if existing is None:
            existing = asyncio.Event()
            self._stop = existing
        return existing

    # ------------------------------------------------------------------ the facade

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        """Call the agent and wait. Blocks the CALLING thread, as the protocol says.

        **A listener must not call this**: it runs on the loop thread, and blocking that thread on
        a coroutine that can only run there hangs for ever. Refused, exactly as the other runners
        do it.
        """
        self._ensure_started()
        loop = self._loop
        assert loop is not None
        if threading.current_thread() is self._thread:
            raise RuntimeError(
                "WslRunner.call was made from the agent's own loop thread (a listener?); "
                "it would deadlock"
            )
        future = asyncio.run_coroutine_threadsafe(
            self._client.call(method, timeout=timeout, **params), loop
        )
        return future.result(timeout + CALL_SLACK_S)

    def subscribe(self, listener: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        """Add a listener, returning a callable that removes it."""
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def close(self) -> None:
        """Stop the agent and every job it supervises. Idempotent.

        **Jobs first, over the wire**, while the agent can still cancel them; then the client, then
        the `wsl.exe` child. The process is terminated only if the graceful path did not finish in
        time - a `wsl.exe` that ignores its child is a bug, not a reason to hang a closing window.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, thread, client, proc = self._loop, self._thread, self._client, self._proc
            self._loop = self._thread = self._client = self._proc = None
        if loop is None or thread is None:
            self._terminate(proc)
            return

        async def shut_down() -> None:
            with contextlib.suppress(Exception):
                await client.call("agent.shutdown", timeout=15)
            with contextlib.suppress(Exception):
                await client.close()
            if proc is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)
                if proc.returncode is None:
                    self._terminate(proc)

        try:
            asyncio.run_coroutine_threadsafe(shut_down(), loop).result(JOIN_TIMEOUT_S * 2)
        except Exception:
            log.warning("the WSL agent did not shut down cleanly", extra=fields(distro=self.distro))
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(self._stop_event.set)
        thread.join(timeout=JOIN_TIMEOUT_S)
        self._terminate(proc)
        with contextlib.suppress(Exception):
            loop.close()

    @staticmethod
    def _terminate(proc: asyncio.subprocess.Process | None) -> None:
        """Kill the `wsl.exe` child, if it is still there. Best effort: close must not raise."""
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.returncode is None:
                proc.terminate()


async def _deliver(
    client: Any,
    listeners: list[Callable[[str, dict[str, Any]], None]],
    lock: threading.Lock,
) -> None:
    """Read the client's event queue and give each event to every listener.

    **On the loop thread**, which is what makes `call` from a listener a deadlock - refused above.
    A listener that raises is logged and skipped: one bad listener must not stop the others or kill
    the pump.
    """
    while True:
        event = await client.events.get()
        name = str(event.get("event", ""))
        data = event.get("data") or {}
        with lock:
            current = list(listeners)
        for listener in current:
            try:
                listener(name, data)
            except Exception:
                log.warning("wsl host listener failed", extra=fields(event=name))


def _explain(exc: BaseException, distro: str) -> str:
    """The reason a distribution could not start, in a sentence a user can act on."""
    if isinstance(exc, RunnerUnavailable):
        return str(exc)
    return f"the agent in {distro} could not be used: {type(exc).__name__}: {exc}"[:400]


__all__ = ["DISTRO_PATTERN", "NO_WSL_WHY", "WslRunner"]
