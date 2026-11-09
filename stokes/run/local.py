"""The in-process agent: one asyncio loop on its own thread, behind a blocking facade (T-144c).

## Where this runs, and what that means for jobs

Inside the desktop/server process on Linux or Windows. Linux captures an attached
OpenFOAM environment; Windows discovers a native FoamWin install and supervises
its processes with kill-on-close Job Objects. Host probing reports whether an
eligible install and MPI runtime are actually present.

The consequence is deliberate: **closing the runner kills every running job**, because the agent
that supervised them is going away with it. `close()` calls `shutdown_jobs()`, which cancels each
job and marks it `interrupted` — a solver must not be left holding a case directory after the window
that started it is gone. `__init__` registers `atexit.register(self.close)` so that an ordinary exit
does the same thing.

**`atexit` is not enough on its own**, and the brief's review correction says why: Python's default
`SIGTERM` action ends the process immediately, so `atexit` never runs and a solver started with
`start_new_session=True` survives as an orphan. `stokes/server/__main__.py` installs handlers that
turn `SIGTERM` and `SIGHUP` into `KeyboardInterrupt`, so the existing shutdown path runs and reaches
here.

## The threading picture, in three lines

1. The **caller's thread** (the server's, a command handler) calls `call`, `subscribe` or `close`.
2. **`stokes-agent-local`** runs its own asyncio loop, where `AgentServer` and `AgentClient`
live; the
   pump task delivers events to listeners **on that thread**.
3. `call` hands a coroutine to the loop with `run_coroutine_threadsafe` and blocks the caller on its
   result — so the loop is never blocked by a caller, and a caller is never blocked by another task.

The one rule that keeps this deadlock-free: **a listener must not call `call`**, because it
would block
the loop thread it is running on. `call` refuses outright rather than hanging.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import platform
import threading
from collections.abc import Callable
from typing import Any

from stokes.log import fields, get_logger

from .errors import RunnerUnavailable

log = get_logger(__name__)

#: How long `close` waits for the loop thread to finish after being asked to stop.
JOIN_TIMEOUT_S = 5.0

#: The extra time `call` allows beyond the agent's own timeout, for the round trip itself.
CALL_SLACK_S = 5.0

#: The reason a non-Linux machine gives.
#:
#: **Says what a user can DO, not what is missing** (claude-win2's package-two review item 9). It
#: read *"…here or over SSH (coming next)"*, which the user-text gate refuses - and it was
#: wrong as well as banned, because the SSH path **exists**: `host.trust` and the Compute
#: section add a remote host and use it. The wording matches the agent's own probe
#: (`engine_env.py`), so the two answers to one question cannot differ.
NO_HOST_WHY = (
    # **Claude's own wording** (GUI review item 6): one sentence, two ways forward, and no
    # "over SSH" on the first line - the Compute section does the SSH for the user.
    "No OpenFOAM yet: add a Linux host in Settings \u2192 Compute, or install OpenFOAM on this PC"
)


class LocalRunner:
    """An agent in this process, started on first use.

    Nothing is created in `__init__`: a session that never meshes must not start a loop, open a
    port or
    read an OpenFOAM environment. The first `call` builds everything, once.
    """

    name = "local"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._client: Any = None
        self._pump: Any = None
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._closed = False
        # **A desktop that exits must not leave orphaned meshers.** `close` is idempotent, so a
        # caller
        # that also closes explicitly is fine.
        atexit.register(self.close)

    # ------------------------------------------------------------------ availability

    @property
    def available(self) -> bool:
        """Linux and native Windows can supervise jobs; probing decides engine availability."""
        return platform.system() in ("Linux", "Windows")

    @property
    def why(self) -> str:
        """The reason, or ``""``. Empty when available."""
        return "" if self.available else NO_HOST_WHY

    # ------------------------------------------------------------------ lifecycle

    def _ensure_started(self) -> None:
        """Start the loop and the agent, once. Safe to call from any thread."""
        if not self.available:
            raise RunnerUnavailable(self.why, details={"code": "run.unavailable"})
        with self._lock:
            if self._closed:
                raise RunnerUnavailable(
                    "this runner has been closed", details={"code": "run.unavailable"}
                )
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

            self._thread = threading.Thread(target=boot, name="stokes-agent-local", daemon=True)
            self._thread.start()
            # **Wait for the agent to be listening**, so the first `call` cannot race the start.
            if not ready.wait(timeout=30):
                raise RunnerUnavailable(
                    "the local agent did not start within 30s",
                    details={"code": "run.unavailable"},
                )
            if failure:
                raise RunnerUnavailable(
                    f"the local agent failed to start: {failure[0]}",
                    details={"code": "run.unavailable"},
                )

    async def _run(self, ready: threading.Event) -> None:
        """The loop body: build the server and client, pump events, and wait to be stopped."""
        from stokes_agent.client import AgentClient
        from stokes_agent.server import AgentServer

        # **A random token on a loopback port**, and it never leaves this method: not logged, not in
        # an event, not in a result. The server and client are the only two things that know it.
        self._server = AgentServer(port=0)
        host, port = await self._server.start()
        self._client = await AgentClient.connect(host, port, self._server.token)
        self._loop = asyncio.get_running_loop()
        self._pump = asyncio.create_task(self._deliver())
        ready.set()
        try:
            await self._stop_event.wait()
        finally:
            if self._pump is not None:
                self._pump.cancel()
                with contextlib.suppress(Exception):
                    await self._pump

    async def _deliver(self) -> None:
        """Read the client's event queue and give each event to every listener.

        **Listeners run on the loop thread**, which is what makes `call` from a listener a deadlock
        — it would block the very thread it is running on. `call` refuses that case rather than
        hanging, and a listener that raises is logged and skipped: one bad listener must not stop
        the others or kill the pump.
        """
        while True:
            event = await self._client.events.get()
            name = str(event.get("event", ""))
            data = event.get("data") or {}
            for listener in list(self._listeners):
                try:
                    listener(name, data)
                except Exception:
                    log.warning("host listener failed", extra=fields(event=name))

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
        """Call an agent method and wait for the result.

        Raises `RunnerUnavailable` when there is no host, and re-raises the agent's own error
        unchanged so a caller sees the agent's code rather than a wrapping one.

        **The default timeout is a round trip's worth**, 60 s. A method whose cost scales with the
        model — `job.create`, which stages every file — should pass its own; `stokes.mesh.service`
        does.
        """
        self._ensure_started()
        loop = self._loop
        assert loop is not None
        if threading.current_thread() is self._thread:
            # **Refuse rather than deadlock.** A listener runs here, and blocking this thread on a
            # coroutine that can only run on this thread would hang for ever.
            raise RuntimeError(
                "LocalRunner.call was made from the agent's own loop thread (a listener?); "
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

        The order matters: **jobs first**, while the loop is still running and can signal them,
        then the
        client and server, then the loop. Stopping the server first would leave `shutdown_jobs` with
        nothing to talk to.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, thread, client, server = self._loop, self._thread, self._client, self._server
            self._loop = self._thread = self._client = self._server = None
        if loop is None or thread is None:
            return

        async def shut_down() -> None:
            # **Kill the jobs.** ADR-0024: a job never outlives the agent that started it.
            with contextlib.suppress(Exception):
                await server.shutdown_jobs()
            with contextlib.suppress(Exception):
                await client.close()
            with contextlib.suppress(Exception):
                await server.stop()

        try:
            asyncio.run_coroutine_threadsafe(shut_down(), loop).result(JOIN_TIMEOUT_S * 2)
        except Exception:
            log.warning("the local agent did not shut down cleanly")
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(self._stop_event.set)
        thread.join(timeout=JOIN_TIMEOUT_S)
        with contextlib.suppress(Exception):
            loop.close()
