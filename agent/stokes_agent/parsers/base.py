"""The log parser protocol (ADR-0024 §1.4, T-202b).

A parser turns a utility's output into events as it arrives. `checkMesh`'s quality
summary (T-208) and
`foamRun`'s residuals (T-213) are the two the skeleton needs; neither lives here,
because this module
must not know any particular format.

## The two-method shape, and why it is not one

``feed`` is called with each batch of new lines and returns events. ``report`` is called **once,
at the
end, whatever the exit code** — because the interesting summary is often only knowable once the
output has
stopped, and because a failed run still has a report worth keeping. A parser therefore holds
state between
calls; it is created at step start and discarded after the report.

## A parser may not fail a job

`feed` raising becomes ``{"error": …}`` in the report and nothing more. The reason is
that a parser is
reading output **we did not write**: a utility that changes its wording in a point release must
not turn a
successful mesh into a failed job. A parser that must not lose data writes its own file in the
job folder,
which T-213 does.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LogParser(Protocol):
    """Stateful, one instance per step run.

    ``feed`` receives lines in order and returns ``(event_name, data)`` pairs; ``report``
    returns the
    step's summary for `state.json`. Both may return empty — a parser is allowed to be interested in
    nothing it has seen so far.
    """

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        """Consume new log lines and return the events they imply."""
        ...

    def report(self) -> dict[str, Any]:
        """The step's summary. Called once, at the end, whatever the exit code."""
        ...
