"""The session's Fast run (ADR-0047 decisions 2 and 7): ``fast_of(session)``.

A session extension (``Session.extensions["fast"]``). It holds at most one ``FastRunner`` and keeps
it in step with the model: on a node event (a boundary, a material, a parameter changed) and when an
operation has re-run (``geometry.op_done``), it reads the case again and hands it to the runner,
which keeps the flow where the geometry allows. A case that no longer reads (a boundary kind Fast
mode does not model, a port that stopped being flat) leaves the run on the last good case and says
why in ``fast.warning``. It stops the run when the project closes or another one opens.

Reading the case runs two read-only commands (``material.evaluate``, ``tag.anchor``); the hook
guards against re-entering itself and ignores Fast mode's own events.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from stokes.api.session import Session
from stokes.errors import StokesError
from stokes.fast.errors import FastNotRunning
from stokes.log import fields, get_logger

log = get_logger(__name__)

#: The events after which the case is read again.
MODEL_EVENTS = frozenset(
    {"node.added", "node.changed", "node.removed", "geometry.op_done", "geometry.part_removed"}
)

_CREATE = threading.Lock()


class FastService:
    """One session's Fast run."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._lock = threading.Lock()
        self._runner: Any = None  # Any: stokes.fast.runner.FastRunner, imported on start
        self._reading = threading.local()

    # ------------------------------------------------------------------ commands

    def start(
        self, fidelity: int, axis: int | None, offset: float | None, turbulence: str | None = None
    ) -> dict[str, Any]:  # Any: JSON
        from stokes.api.fast_inputs import case_from_session
        from stokes.fast.runner import FastRunner

        case, density = case_from_session(self._session)
        self.stop()
        runner = FastRunner(self._session.emit, density=density)
        runner.configure(fidelity=fidelity, axis=axis, offset=offset, turbulence=turbulence)
        with self._lock:
            self._runner = runner
        runner.start(case)
        return {
            "started": True,
            "fluid": case.label,
            "ports": [p.name for p in case.ports],
            "zones": [{"name": z.name, "kind": z.kind} for z in case.all_zones],
            "thermal": case.thermal,
        }

    def stop(self) -> bool:
        with self._lock:
            runner, self._runner = self._runner, None
        if runner is None:
            return False
        runner.stop()
        return True

    def configure(
        self,
        fidelity: int | None,
        axis: int | None,
        offset: float | None,
        turbulence: str | None = None,
    ) -> None:
        self.require().configure(fidelity=fidelity, axis=axis, offset=offset, turbulence=turbulence)

    def require(self) -> Any:  # Any: FastRunner
        with self._lock:
            runner = self._runner
        if runner is None or not runner.alive:
            raise FastNotRunning("Fast mode is not running: start it with fast.start")
        return runner

    def runner(self) -> Any:  # Any: FastRunner or None
        with self._lock:
            return self._runner

    def status(self) -> dict[str, Any]:  # Any: JSON
        runner = self.runner()
        if runner is None:
            return {"state": "off"}
        status: dict[str, Any] = runner.status()  # Any: JSON
        return status

    # ------------------------------------------------------------------ session hooks

    def on_event(self, name: str, data: dict[str, Any]) -> None:  # Any: JSON payload
        if name not in MODEL_EVENTS or getattr(self._reading, "active", False):
            return
        runner = self.runner()
        if runner is None or not runner.alive:
            return
        from stokes.api.fast_inputs import case_from_session

        self._reading.active = True
        try:
            case, density = case_from_session(self._session)
        except StokesError as exc:
            self._session.emit("fast.warning", code=exc.code, message=exc.message)
            return
        except Exception:
            log.exception("reading the case for Fast mode failed", extra=fields(event=name))
            return
        finally:
            self._reading.active = False
        runner.density = density
        runner.update(case)

    def project_opened(self, project: Any, path: Path | None) -> None:  # Any: Project
        self.stop()

    def project_closed(self) -> None:
        self.stop()


def fast_of(session: Session) -> FastService:
    """The session's Fast service, made on first use."""
    with _CREATE:
        found = session.extensions.get("fast")
        if not isinstance(found, FastService):
            found = FastService(session)
            session.extensions["fast"] = found
        return found


def require_frame(session: Session) -> Any:  # Any: stokes.fast.runner.Frame
    """The running Fast run's last frame, or ``FastNotRunning``."""
    found = session.extensions.get("fast")
    if not isinstance(found, FastService):
        raise FastNotRunning("Fast mode is not running")
    return found.require().frame()
