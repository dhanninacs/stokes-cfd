"""The SSH `Runner`: an agent on another machine, behind the same four methods (T-159a).

**`LocalRunner` and `SshRunner` are interchangeable above this file.** A mesh driver asks
`HostRegistry.runner(name)` and then calls `call`/`subscribe`; which machine answers, and how
the bytes get there, is decided here. That is the whole point of the `Runner` protocol, and it
is why the six-site fix mattered: a caller that hardcoded `local` could not have used this.

## What differs from the local runner, and why

* **The agent is DETACHED and outlives this process** (the owner's decision): a mesh on a build
  machine must survive the app closing. `close` therefore stops the *tunnel*, **not** the agent -
  killing a colleague's overnight mesh because a window closed is the opposite of the promise.
  Local jobs still die with the app, which is the asymmetry the owner chose.
* **The agent is reached through a tunnel**, so `call` goes to a loopback port that ssh forwards.
  The agent binds loopback only and is never addressable directly.
* **Starting is lazy and may fail**, so `available`/`why` answer from the host list and the first
  real call is where a bad key, a missing package or an unreachable machine becomes an error.
  That is no different from the local runner's own `_ensure_started`, and it keeps the cost off
  session start.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from typing import Any

from stokes.log import fields, get_logger
from stokes.run.errors import RunnerUnavailable
from stokes.run.ssh import (
    SshError,
    Tunnel,
    ensure_agent,
    read_secret,
    run_remote,
)
from stokes.run.ssh_hosts import SshHost

log = get_logger(__name__)

#: How long to wait for a call's reply beyond the call's own timeout. The same slack the local
#: runner allows, for the same reason: the round trip includes our own scheduling.
CALL_SLACK_S = 30.0
#: How long `close` waits for the pump to stop. A tunnel that will not die must not hang a session.
JOIN_TIMEOUT_S = 10.0


class SshRunner:
    """A `Runner` whose agent lives on another machine (T-159a).

    **Nothing is started in `__init__`.** A session that lists hosts but never meshes on one
    must not open an ssh connection, so the first `call` builds everything once - the same rule
    `LocalRunner` follows for its own thread and loop.
    """

    def __init__(self, host: SshHost, *, home: Any = None) -> None:
        self.host_record = host
        self.name = host.name
        self._home = home
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._tunnel: Tunnel | None = None
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._closed = False
        self._failure: str = ""

    # ------------------------------------------------------------------ availability

    @property
    def available(self) -> bool:
        """Whether this host can be used at all. **False is a normal answer**, so a session with a
        host it cannot reach still opens and says why."""
        return not self._closed and not self._failure

    @property
    def why(self) -> str:
        """Why the host is unavailable, for a user. `""` when it is fine.

        **The failure is reported BEFORE the closed flag.** A start that failed leaves the runner
        both failed and closed, and my first version checked `_closed` first - so a host whose
        key was untrusted answered "this runner has been closed", which tells the user nothing
        and **hides the message that says how to fix it**. Measured. The reason is the useful
        half; "closed" is only worth saying when there is no reason to give.
        """
        if self._failure:
            return self._failure
        if self._closed:
            return "this runner has been closed"
        return ""

    # ------------------------------------------------------------------ lifecycle

    def _ensure_started(self) -> None:
        """Start the tunnel, attach the agent and pump events, once. Safe from any thread."""
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

            self._thread = threading.Thread(
                target=boot, name=f"stokes-agent-ssh-{self.name}", daemon=True
            )
            self._thread.start()
            # **Wait for the agent to be listening**, so the first `call` cannot race the start.
            if not ready.wait(timeout=180):
                self._failure = f"the agent on {self.name} did not start within 180s"
            elif failure:
                # **The reason is kept on the runner**, so `why` can answer it later without
                # attempting the connection again - a host whose key is untrusted must not be
                # retried on every redraw.
                self._failure = _explain(failure[0], self.name)
            if failure or not ready.is_set():
                self._closed = True
                raise RunnerUnavailable(self._failure, details={"code": "run.unavailable"})

    async def _run(self, ready: threading.Event) -> None:
        """The loop body: attach, then pump events until stopped."""
        record = ensure_agent(self.host_record, self._home)
        secret = read_secret(self.host_record, self._home)
        tunnel = Tunnel(self.host_record, int(record["port"]), self._home)
        tunnel.open()
        client = await _connect(tunnel.local_port, secret)
        self._tunnel = tunnel
        self._client = client
        self._loop = asyncio.get_running_loop()
        self._pump = asyncio.create_task(_deliver(client, self._listeners, self._lock))
        ready.set()
        try:
            await self._stop_event.wait()
        finally:
            pump = getattr(self, "_pump", None)
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(Exception):
                    await pump

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

        **A listener must not call this**: a listener runs on the loop thread, and blocking
        that thread on a coroutine that can only run there hangs for ever. Refused rather than
        allowed to deadlock, exactly as `LocalRunner` does it.
        """
        self._ensure_started()
        loop = self._loop
        assert loop is not None
        if threading.current_thread() is self._thread:
            raise RuntimeError(
                "SshRunner.call was made from the agent's own loop thread (a listener?); "
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
        """Stop the tunnel and the pump. **The agent keeps running** (the owner's decision).

        A detached agent exists so a job outlives the client; stopping it here would kill a mesh the
        user asked to outlive their session, and would do it every time a window closed. The agent
        exits on its own after twelve idle hours.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, thread, client, tunnel = self._loop, self._thread, self._client, self._tunnel
            self._loop = self._thread = self._client = self._tunnel = None
        if client is not None:
            # The client belongs to the loop, so closing it goes through the loop too.
            with contextlib.suppress(Exception):
                _schedule(loop, client.close())
        if tunnel is not None:
            tunnel.close()
        if loop is not None:
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(self._stop_event.set)
        if thread is not None:
            thread.join(timeout=JOIN_TIMEOUT_S)


async def _connect(port: int, secret: str) -> Any:
    """`AgentClient.connect_mutual` on the loop `_run` owns."""
    from stokes_agent.client import AgentClient

    return await AgentClient.connect_mutual("127.0.0.1", port, secret)


def _schedule(loop: Any, coroutine: Any) -> None:
    """Run a coroutine on a loop from another thread, ignoring the result. Best effort."""
    if loop is None or loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(coroutine, loop)


async def _deliver(
    client: Any,
    listeners: list[Callable[[str, dict[str, Any]], None]],
    lock: threading.Lock,
) -> None:
    """Read the client's event queue and give each event to every listener.

    **On the loop thread**, which is what makes `call` from a listener a deadlock - refused
    above. A listener that raises is logged and skipped: one bad listener must not stop the
    others or kill the pump.
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
                log.warning("ssh host listener failed", extra=fields(event=name))


def _explain(exc: BaseException, name: str) -> str:
    """The reason a host could not start, in a sentence a user can act on.

    An `SshError` already carries a message written for a person - a key to trust, a package to
    install - so it is passed through; anything else is summarised with its type, because an
    unexpected exception's `str()` alone is often empty.
    """
    if isinstance(exc, SshError):
        return str(exc)
    return f"{name} could not be used: {type(exc).__name__}: {exc}"[:300]


__all__ = ["SshRunner", "run_remote"]
