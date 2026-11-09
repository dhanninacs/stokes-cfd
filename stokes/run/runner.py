"""The `Runner` protocol: what a host must be able to do (ADR-0005, T-144c).

A runner is a **synchronous** handle on an agent. Everything above it — the host registry, the
commands,
a mesh driver — wants to ask a question and get an answer, and the only place asyncio appears is
inside
the local runner's own thread. The SSH runner (T-159a) plugs into the same protocol, so
nothing above
this file needs to know which kind of host it is talking to.

Its four methods are the whole surface: ask something (`call`), follow what happens
(`subscribe`), find
out whether it can be used at all (`available`, `why`), and shut it down (`close`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Runner(Protocol):
    """A synchronous agent handle.

    ``subscribe`` returns an unsubscribe callable rather than taking an id, because a caller
    that has to
    remember a token to stop listening is a caller that leaks listeners.
    """

    name: str

    @property
    def available(self) -> bool:
        """Whether this host can be used at all. False is a normal answer, not an error."""
        ...

    @property
    def why(self) -> str:
        """Why the host is unavailable, or ``""``. Written for a user, not a log."""
        ...

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        """Call an agent method and wait for the result. Blocks the CALLING thread."""
        ...

    def subscribe(self, listener: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        """Register a listener for agent events; returns a callable that removes it."""
        ...

    def close(self) -> None:
        """Stop the runner. Idempotent."""
        ...
