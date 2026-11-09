"""The operations service (ADR-0021 decision 8): runs, outputs, tags, staleness and re-runs.

One service per session, in ``session.extensions["operations"]`` (:meth:`OperationStore.of`),
beside the geometry store it shares meshes, the shape folder and the session thread with.

A run (:meth:`OperationStore.start`) is a daemon thread (``stokes-geometry-operation``) that waits
for the parts it reads (an import still running, an operation making them), builds its job where
commands run (:class:`Plan`: SI parameters, the inputs' specs and versions, the seed of the outputs'
versions), and streams one worker job (``GeometryWorker.operation``). Each ``shape`` message adds or
updates its output part where commands run, as an import builds its group; outputs are
``output_id(op, i)``, carry ``derived`` (the op) and ``basis`` (``output_basis``), and are not
journaled or undoable: the op node is (:mod:`stokes.geometry.opcommands`). Tags follow through the
history (:mod:`stokes.geometry.carry`). Meshes go to the geometry store. When the run ends, outputs
the last run made and this one did not go, the op's ``outputs`` and run record (``meta.run``: the
signature it ran, report, warnings, unmapped faces, timings) are written, and ``geometry.op_done``
or ``geometry.op_failed`` is emitted.

Staleness. An op is up to date when its last run succeeded at the signature its parameters and
its inputs' versions give now and no part it reads is stale. The signature hashes the members of
the tags it references, kept to the members on the parts it reads: the operations after it put
their own outputs in the same tag, and those are not its input
(:mod:`stokes.geometry.opplan`), so a run's own or a downstream run's tag additions never make it
stale. Otherwise it is ``stale`` with its
outputs (``stokes.model.graph.mark_stale``, through ``meta.derives_from``), and with ``auto_run``
(default true, decisions log G12) it runs again 300 ms after the last change, cancelling a run of
the same op under way. Changes arrive through :meth:`on_event` (``node.changed`` of the op, of a
part it reads or of a tag it references; ``history.changed`` after undo and redo, which emit
nothing else); a cancelled or failed run is not retried until something changes.

Events::

    geometry.op_progress {node_id, path, stage, fraction, message}   at most ten a second
    geometry.op_done {node_id, path, outputs, seconds}
    geometry.op_failed {node_id, path, code, message, details}       code geometry.cancelled too
    node.added / node.changed / node.removed / tag.changed           outputs, tags, the op
    geometry.part_ready ...                                          the outputs' meshes (store)

Threads: the run thread waits and talks to the worker; the tree changes where commands run
(:meth:`GeometryStore.on_session_thread`), and ``_tree`` serialises them with an undo that
discards the outputs, for scripts that run without an event loop.

Import rule: the standard library, the model and the light geometry modules at module level;
numpy, the worker and the meshes inside the methods that need them.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from stokes import workspace_root
from stokes.errors import StokesError, ValidationError
from stokes.geometry.carry import Carried, Event, carry, strip
from stokes.geometry.errors import (
    GeometryError,
    GeometryWorkerError,
    ImportCancelled,
    OperationFailed,
    OperationInvalid,
)
from stokes.geometry.model import GEOMETRY_PATH, OPERATIONS_PATH, TAG_TYPE, operation_type
from stokes.geometry.opplan import Plan, build_plan, consumed, operations_in, referenced_tags
from stokes.log import fields, get_logger
from stokes.model.ids import stable_id

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.geometry.store import GeometryStore
    from stokes.geometry.worker import OperationRun, ShapeResult, SolidResult
    from stokes.model.nodes import Node
    from stokes.model.project import Project

log = get_logger(__name__)
T = TypeVar("T")

EXTENSION = "operations"
PARTS_PATH = f"{GEOMETRY_PATH}/parts"
DEBOUNCE_SECONDS = 0.3
"""How long after the last change a re-run starts (ADR-0021 decision 8.8)."""
PROGRESS_INTERVAL = 0.1
WAIT_SECONDS = 1800.0
"""The longest a waiting caller or replay waits for runs: the worker's own timeout."""
POLL_SECONDS = 0.1
UPSTREAM_ROUNDS = 32
"""Rounds of waiting for inputs (imports, upstream operations) before a run gives up."""
MAX_REPORT_BYTES = 256 * 1024
_QUIET = ("undo", "replaced", "project", "closed", "removed")
_store_lock = threading.Lock()


@dataclass(eq=False)
class RunJob:
    """One run of one operation."""

    op: str
    project: Project
    reason: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    why: str = ""
    """Who cancelled it: ``user``, or a quiet reason (undo, replaced, project, closed, removed)."""
    stage: str = "queued"
    fraction: float = 0.0
    message: str = ""
    started: float = field(default_factory=time.monotonic)
    sent: float = 0.0
    outputs: dict[int, str] = field(default_factory=dict)
    shapes: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON
    carried: Carried = field(default_factory=Carried)
    plan: Plan | None = None


class OperationStore:
    """The session's operations: runs in the background, outputs, re-runs on change."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._lock = threading.RLock()
        self._tree = threading.RLock()
        self._runs: dict[str, RunJob] = {}
        self._timers: dict[str, threading.Timer] = {}
        # Depth of `hold_debounce`: while it is non-zero a fired timer leaves its run pending, so a
        # replay walks a whole burst of changes before anything runs (T-092).
        self._hold = 0
        self._closed = False
        self._quiet = threading.local()

    @classmethod
    def of(cls, session: Session) -> OperationStore:
        """The session's service, created on first use."""
        with _store_lock:
            found = session.extensions.get(EXTENSION)
            if not isinstance(found, OperationStore):
                found = cls(session)
                session.extensions[EXTENSION] = found
            return found

    @property
    def geometry(self) -> GeometryStore:
        from stokes.geometry.store import GeometryStore

        return GeometryStore.of(self._session)

    # ------------------------------------------------------------------ plans
    def plan(self, project: Project, node: Node, *, check_cache: bool = False) -> Plan:
        """The job a run of ``node`` would send now (:func:`stokes.geometry.opplan.build_plan`)."""
        return build_plan(project, node, self.geometry, check_cache=check_cache)

    def signature(self, project: Project, node: Node) -> str | None:
        """The seed a run of ``node`` would have now; None when it cannot run."""
        try:
            return self.plan(project, node).seed
        except StokesError:
            return None

    # ------------------------------------------------------------------ runs
    def start(self, op_id: str, *, reason: str = "run") -> RunJob | None:
        """Run ``op_id`` now in the background, cancelling a run of it under way and a re-run
        waiting for its debounce. None once the service is closed."""
        project = self._session.project
        if project is None:
            return None
        with self._lock:
            if self._closed:
                return None
            timer = self._timers.pop(op_id, None)
            if timer is not None:
                timer.cancel()
            previous = self._runs.get(op_id)
            if previous is not None:
                previous.why = "replaced"
                previous.cancel.set()
            job = RunJob(op_id, project, reason)
            self._runs[op_id] = job
        thread = threading.Thread(
            target=self._run, args=(job,), name="stokes-geometry-operation", daemon=True
        )
        thread.start()
        return job

    def cancel(self, op_id: str, *, reason: str = "user") -> bool:
        """Cancel the run of ``op_id`` under way or waiting for its debounce; whether there was
        one."""
        with self._lock:
            timer = self._timers.pop(op_id, None)
            if timer is not None:
                timer.cancel()
            job = self._runs.get(op_id)
            if job is not None and not job.cancel.is_set():
                job.why = reason
                job.cancel.set()
        return timer is not None or job is not None

    def busy(self, op_id: str) -> bool:
        with self._lock:
            return op_id in self._runs or op_id in self._timers

    def state(self, op_id: str) -> dict[str, Any]:  # Any: JSON
        """``{state: idle | waiting | running, stage, fraction, message, seconds}``."""
        with self._lock:
            job = self._runs.get(op_id)
            waiting = op_id in self._timers
        if job is None:
            return {"state": "waiting" if waiting else "idle"}
        return {
            "state": "running",
            "reason": job.reason,
            "stage": job.stage,
            "fraction": job.fraction,
            "message": job.message,
            "seconds": round(time.monotonic() - job.started, 3),
        }

    def wait(
        self,
        op_id: str | None = None,
        timeout: float | None = WAIT_SECONDS,
        *,
        coalesce: bool = False,
    ) -> bool:
        """Block until the runs of ``op_id`` (or every run) have ended, starting at once a
        re-run that waits for its debounce; False on timeout. Refused on the server's event
        loop, where the runs need the thread this would block.

        ``coalesce`` leaves a run that is still inside its debounce to that debounce instead of
        starting it now, so several changes inside one window become one run. The wait is bounded by
        ``timeout`` either way, and a debounce that fires becomes a run this loop then waits for.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                timers = [o for o in self._timers if op_id is None or o == op_id]
                jobs = [j for o, j in self._runs.items() if op_id is None or o == op_id]
            if not timers and not jobs:
                return True
            if self.geometry.blocks_session():
                raise ValidationError(
                    "waiting for an operation would block the server; follow the "
                    "geometry.op_* events instead"
                )
            if coalesce:
                # Let the debounce fire on its own; its callback starts the run, which this loop
                # then waits for. Sleeping is what makes the burst coalesce rather than restart.
                if timers and not jobs:
                    time.sleep(POLL_SECONDS)
                    continue
            else:
                for pending in timers:
                    self.start(pending, reason="change")
            for job in jobs:
                left = None if deadline is None else max(0.0, deadline - time.monotonic())
                if not job.done.wait(left):
                    return False

    def settle(self, timeout: float | None, *, coalesce: bool = False) -> None:
        """Session hook: journal replay waits here between records (ADR-0022 decision 10.1).

        ``coalesce`` lets an operation that is still waiting on its debounce keep waiting for it, so
        a burst of changes recorded inside ONE window runs the operation once — which is what
        happened when the journal was written. Forcing every pending run here instead made replay
        re-run the operation once per record: measured at four runs for a burst that took two, with
        the same output (T-092). The work was duplicated, not wrong, and this is what removes it.
        """
        if not self.wait(None, WAIT_SECONDS if timeout is None else timeout, coalesce=coalesce):
            raise GeometryWorkerError("the operations did not finish in time")

    def _run(self, job: RunJob) -> None:
        import shutil

        workdir: Path | None = None
        # Making the workdir is inside the try as well: a failure here must still end the run, or
        # the operation would sit `running` for good with no job behind it.
        try:
            workdir = workspace_root.scratch("stokes-operation-")
            plan = self._prepare(job)
            if plan is None:
                raise ImportCancelled("the operation was cancelled")
            job.plan = plan
            run = self._stream(job, plan, workdir)
            self._finish(job, plan, run)
        except ImportCancelled:
            self._ended(job, None)
        except GeometryError as exc:
            self._ended(job, exc)
        except StokesError as exc:
            self._ended(job, OperationInvalid(exc.message, details=exc.details))
        except Exception as exc:
            log.exception("operation failed", extra=fields(op=job.op))
            self._ended(job, GeometryWorkerError(f"the operation failed: {exc}"))
        finally:
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)
            with self._lock:
                if self._runs.get(job.op) is job:
                    del self._runs[job.op]
            job.done.set()

    def _prepare(self, job: RunJob) -> Plan | None:
        """Wait for the parts the run reads, then the plan; None when cancelled meanwhile. An
        input whose cached B-rep is gone is rebuilt by its operation, once."""
        tried: set[str] = set()
        for _ in range(UPSTREAM_ROUNDS):
            if job.cancel.is_set():
                return None
            plan = self._here(job, lambda: self._plan_now(job))
            if plan is None:
                return None
            waiting = self._pending_inputs(job, plan)
            if not waiting and not plan.rebuild:
                return plan
            for upstream in plan.rebuild:
                if upstream in tried and not self.busy(upstream):
                    raise OperationInvalid(
                        f"an input of operation {plan.name} lost its B-rep and operation "
                        f"{upstream} could not make it again",
                        details={"op": plan.op, "upstream": upstream},
                    )
                if upstream not in tried and not self.busy(upstream):
                    self.start(upstream, reason="rebuild")
                tried.add(upstream)
            if not self._await(job, waiting + plan.rebuild):
                return None
        raise OperationInvalid(f"the inputs of operation {job.op} did not settle")

    def _plan_now(self, job: RunJob) -> Plan | None:
        project = self._session.project
        if project is not job.project:
            job.why = job.why or "project"
            return None
        node = project.find(job.op)
        if node is None:
            job.why = job.why or "removed"
            return None
        return self.plan(project, node, check_cache=True)

    def _pending_inputs(self, job: RunJob, plan: Plan) -> list[str]:
        """Imports and operations still making the parts the plan reads."""
        geo = self.geometry
        pending: list[str] = []
        for part in plan.parts:
            if geo.status(part).get("status") == "importing":
                pending.append(part)
            node = job.project.find(part)
            upstream = str(node.params.get("derived") or "") if node is not None else ""
            if upstream and upstream != job.op and self.busy(upstream):
                pending.append(upstream)
        return pending

    def _await(self, job: RunJob, ids: Iterable[str]) -> bool:
        """Wait for imports (part ids) and runs (op ids) to end; False when cancelled."""
        geo = self.geometry
        for item in ids:
            while not job.cancel.is_set():
                if self.busy(item):
                    with self._lock:
                        other = self._runs.get(item)
                    if other is not None:
                        other.done.wait(POLL_SECONDS)
                    else:
                        time.sleep(POLL_SECONDS)  # a debounce about to start the run
                    continue
                if geo.status(item).get("status") == "importing":
                    geo.wait(item, POLL_SECONDS)
                    continue
                break
        return not job.cancel.is_set()

    def _stream(self, job: RunJob, plan: Plan, workdir: Path) -> OperationRun:
        from stokes.geometry import worker

        process = worker.acquire()
        # One worker loads OCP ahead for the next run: a re-run on change is the common case,
        # and a cold worker spends seconds importing OCP before it reads anything.
        worker.prewarm(1)
        return process.operation(
            {
                "op_type": plan.type,
                "op": plan.op,
                "seed": plan.seed,
                "params": plan.params,
                "inputs": plan.specs,
                "cache_dir": str(plan.folder),
                "workdir": str(workdir),
            },
            on_progress=lambda stage, fraction, message: self._progress(
                job, stage, fraction, message
            ),
            on_shape=lambda shape: self._shape(job, plan, shape, process.ocp),
            on_solid=lambda result: self._solid(job, result),
            cancel=job.cancel,
            label=f"the operation {plan.name}",
        )

    # ------------------------------------------------------------------ messages of a run
    def _progress(self, job: RunJob, stage: str, fraction: float, message: str) -> None:
        job.stage, job.fraction, job.message = stage, fraction, message
        now = time.monotonic()
        if now - job.sent < PROGRESS_INTERVAL and 0.0 < fraction < 1.0:
            return
        job.sent = now
        self._emit(
            "geometry.op_progress",
            job.op,
            stage=stage,
            fraction=fraction,
            message=message,
        )

    def _shape(self, job: RunJob, plan: Plan, shape: ShapeResult, version: str) -> None:
        from stokes.geometry.operations import output_basis, output_id

        if shape.part != output_id(plan.op, shape.index):
            raise GeometryWorkerError("the geometry worker named an output it was not asked for")
        if shape.basis != output_basis(plan.seed, version, shape.index):
            raise GeometryWorkerError("the geometry worker gave an output the wrong version")
        events = self._here(job, lambda: self._take(job, plan, shape))
        self._announce(events or [])

    def _take(self, job: RunJob, plan: Plan, shape: ShapeResult) -> list[Event]:
        """Add or update output ``shape.index`` and carry the tags. Where commands run."""
        from stokes.geometry.files import node_name_for, unique_name

        with self._tree:
            project = self._live(job)
            if project is None:
                return []
            op = project.get(plan.op)
            events: list[Event] = []
            parts = project.find(PARTS_PATH)
            if parts is None:
                wanted = stable_id("g", PARTS_PATH)
                parts = project.add(
                    GEOMETRY_PATH,
                    "group",
                    "parts",
                    node_id=None if project.find(wanted) else wanted,
                )
                events.append(("node.added", {"node_id": parts.id, "path": parts.path}))
            params: dict[str, Any] = {
                "derived": op.id,
                "basis": shape.basis,
                "family": "brep",
                "faces": shape.faces,
                "bbox": list(shape.bbox),
                "units": "mm",
            }
            node = project.find(shape.part)
            if node is None:
                first = project.find(plan.parts[0]) if plan.parts else None
                if first is not None and isinstance(first.params.get("color"), str):
                    params["color"] = first.params["color"]
                taken = {c.name for c in parts.children}
                name = unique_name(node_name_for(shape.name, "part"), taken)
                node = project.add(parts.id, "part", name, params, node_id=shape.part)
                node.status = "new"
                events.append(("node.added", {"node_id": node.id, "path": node.path}))
            elif node.type != "part" or node.params.get("derived") != op.id:
                raise OperationFailed(
                    f"{node.path} is in the way of output {shape.index + 1} of {op.path}",
                    details={"op": op.id, "part": node.id},
                )
            else:
                for name, value in params.items():
                    if node.params.get(name) != value:
                        project.set_param(node.id, name, value)
                events.append(("node.changed", {"node_id": node.id, "path": node.path}))
            node.meta = {**node.meta, "derives_from": [op.id]}
            job.outputs[shape.index] = node.id
            job.shapes.append(
                {
                    "index": shape.index,
                    "part": node.id,
                    "name": node.name,
                    "faces": shape.faces,
                    "valid": shape.valid,
                    "closed": shape.closed,
                    "volume": shape.volume,
                    "history": shape.history_source,
                }
            )
            events.extend(carry(project, node, shape, plan.bases, job.carried))
            return events

    def _solid(self, job: RunJob, result: SolidResult) -> None:
        """One output's mesh, as the worker reports it.

        The write goes through ``on_session_thread`` like every other tree change: checking
        ``job.cancel`` here and then writing would leave a window in which a Cancel or an Undo
        lands between the two, so a mesh could be written for a part that is already gone. The
        helper takes the same event and abandons the action when it is set before it starts.
        """
        part = job.outputs.get(result.index)
        if part is None or job.cancel.is_set():
            return
        self.geometry.on_session_thread(
            lambda: self.geometry.accept(part, result.mesh, result.pass_name, result.adjacency),
            job.cancel,
        )

    def _finish(self, job: RunJob, plan: Plan, run: OperationRun) -> None:
        done = self._here(job, lambda: self._complete(job, plan, run))
        if done is None:
            raise ImportCancelled("the operation was cancelled")
        events, outputs = done
        self._announce(events)
        folder = self.geometry.shape_dir()
        if folder != plan.folder:  # saved to a new folder while it ran: its B-reps follow
            from stokes.geometry.shapes import copy_shapes

            copy_shapes(plan.folder, folder, outputs)
        seconds = round(time.monotonic() - job.started, 3)
        self._emit("geometry.op_done", plan.op, outputs=outputs, seconds=seconds)
        project = self._session.project
        if project is not None:
            ripple = {o for part in outputs for o in self._consumers(project, part)}
            self._reconsider(sorted(ripple))
        log.info("operation finished", extra=fields(op=plan.op, outputs=len(outputs)))

    def _complete(
        self, job: RunJob, plan: Plan, run: OperationRun
    ) -> tuple[list[Event], list[str]] | None:
        """The run's end where commands run: surplus outputs out, the op's outputs and run
        record in, statuses."""
        from stokes.model.graph import DERIVES_FROM

        with self._tree:
            project = self._live(job)
            if project is None:
                return None
            op = project.get(plan.op)
            new = [job.outputs[i] for i in sorted(job.outputs)]
            events = self._drop_outputs(project, op, keep=new)
            if list(op.params.get("outputs") or []) != new:
                project.set_param(op.id, "outputs", new)
                events.append(
                    ("node.changed", {"node_id": op.id, "path": op.path, "param": "outputs"})
                )
            current = self.signature(project, op)
            fresh = current == plan.seed
            report = {
                **run.report,
                "outputs": job.shapes,
                "unmapped": job.carried.unmapped(project),
            }
            meta = {k: v for k, v in op.meta.items() if k != "error"}
            meta["run"] = {
                "signature": plan.seed,
                "ok": True,
                "at": _now(),
                "seconds": round(time.monotonic() - job.started, 3),
                "worker_seconds": round(run.seconds, 3),
                "version": run.version,
                "outputs": new,
                "warnings": list(run.warnings),
                "mapped": job.carried.mapped_faces,
                "report": _bounded(report),
            }
            meta[DERIVES_FROM] = sorted(set(plan.parts) | set(plan.refs))
            meta["created_tags"] = sorted(
                set(meta.get("created_tags") or []) | set(job.carried.created)
            )
            op.meta = meta
            op.status = "ok" if fresh else "stale"
            for part in new:
                node = project.find(part)
                if node is not None:
                    node.status = op.status
                    events.append(("node.changed", {"node_id": node.id, "path": node.path}))
            events.append(("node.changed", {"node_id": op.id, "path": op.path}))
            return events, new

    def _drop_outputs(self, project: Project, op: Node, *, keep: list[str]) -> list[Event]:
        """Remove the op's outputs a run no longer made (and take them out of the tags)."""
        gone: list[str] = []
        events: list[Event] = []
        for part in list(op.params.get("outputs") or []):
            node = project.find(part)
            if part in keep or node is None or node.type != "part":
                continue
            if node.params.get("derived") != op.id:
                continue
            path = node.path
            self.geometry.take(part)
            project.remove(part)
            gone.append(part)
            events.append(("node.removed", {"node_id": part, "path": path}))
        if gone:
            events = strip(project, gone) + events
        return events

    def _ended(self, job: RunJob, exc: GeometryError | None) -> None:
        """A run that did not finish: cancelled (``exc`` None) or failed."""
        if job.why in _QUIET:
            return
        cancelled = exc is None or isinstance(exc, ImportCancelled)
        error = exc if exc is not None and not cancelled else None
        signature = job.plan.seed if job.plan is not None else None
        events = self.geometry.on_session_thread(
            lambda: self._mark_ended(job, error, signature, cancelled), threading.Event()
        )
        self._announce(events or [])
        code = "geometry.cancelled" if cancelled else (exc.code if exc else "")
        message = "the operation was cancelled" if cancelled else (exc.message if exc else "")
        details = exc.details if exc is not None and not cancelled else {}
        log.warning("operation ended early", extra=fields(op=job.op, code=code, error=message))
        self._emit("geometry.op_failed", job.op, code=code, message=message, details=details)

    def _mark_ended(
        self, job: RunJob, error: GeometryError | None, signature: str | None, cancelled: bool
    ) -> list[Event]:
        with self._tree:
            project = self._live(job, cancelled_ok=True)
            op = project.find(job.op) if project is not None else None
            if project is None or op is None:
                return []
            meta = dict(op.meta)
            record: dict[str, Any] = {"signature": signature, "ok": False, "at": _now()}
            if cancelled:
                record["cancelled"] = True
                op.status = "stale" if op.status != "failed" else op.status
            else:
                assert error is not None
                record["code"] = error.code
                meta["error"] = error.message
                op.status = "failed"
            meta["run"] = {**(meta.get("run") or {}), **record}
            made = [job.outputs[i] for i in sorted(job.outputs)]
            known = list(op.params.get("outputs") or [])
            if any(part not in known for part in made):  # outputs of the run that ended early
                project.set_param(op.id, "outputs", list(dict.fromkeys(known + made)))
            op.meta = meta
            events: list[Event] = [("node.changed", {"node_id": op.id, "path": op.path})]
            for part in dict.fromkeys(known + made):
                node = project.find(part)
                if node is not None and node.type == "part":
                    node.status = "stale"
                    events.append(("node.changed", {"node_id": node.id, "path": node.path}))
            return events

    def _live(self, job: RunJob, *, cancelled_ok: bool = False) -> Project | None:
        """The project a run may still change: its own, open, with the op in it."""
        if job.cancel.is_set() and not cancelled_ok:
            return None
        project = self._session.project
        if project is not job.project or project.find(job.op) is None:
            return None
        return project

    # ------------------------------------------------------------------ undo
    def discard(self, op_id: str) -> None:
        """Take away everything the runs of ``op_id`` made: its outputs and their meshes, their
        faces in every tag, and the tags its runs created that are now empty. The undo of an
        operation's creation, which cancels the run first."""
        from stokes.geometry.store import parts_under

        project = self._session.project
        if project is None:
            return
        from stokes.geometry.carry import tags_of
        from stokes.geometry.operations import tag_id

        with self._tree:
            outputs = [n.id for n in parts_under(project.root) if n.params.get("derived") == op_id]
            op = project.find(op_id)
            created = list(op.meta.get("created_tags") or []) if op is not None else []
            # A run cut short never wrote created_tags: its tags carry ids derived from the op's.
            created += [t.id for t in tags_of(project) if t.id == tag_id(op_id, t.name)]
            events = strip(project, outputs, created)
            for part in outputs:
                self.geometry.take(part)
                project.remove(part)
        self._announce(events)

    # ------------------------------------------------------------------ staleness and re-runs
    def on_event(self, name: str, data: dict[str, Any]) -> None:  # Any: JSON payload
        """Session hook: follow the changes that make an operation stale (ADR-0021 8.8)."""
        quiet: int = getattr(self._quiet, "depth", 0)
        if self._closed or quiet:
            return
        if name not in ("node.changed", "node.removed", "history.changed"):
            return
        project = self._session.project
        if project is None or project.find(OPERATIONS_PATH) is None:
            return
        node_id = data.get("node_id")
        if name == "history.changed":
            self._resync(project)
        elif isinstance(node_id, str):
            self._changed(project, name, node_id, data.get("param"))

    def _changed(self, project: Project, name: str, node_id: str, param: Any) -> None:
        """A node event: an op's own parameter, a part it reads, a tag it references."""
        node = project.find(node_id)
        if name == "node.removed":
            if node is None and self.busy(node_id):
                self.cancel(node_id, reason="removed")
            self._reconsider(self._consumers(project, node_id))
        elif node is not None and operation_type(node.type) is not None:
            if param != "outputs":
                self._reconsider([node.id])
        elif node is not None and node.type in ("part", TAG_TYPE):
            self._reconsider(self._consumers(project, node.id))

    def _consumers(self, project: Project, node_id: str) -> list[str]:
        """The operations that read the part, or reference the tag, ``node_id``."""
        return [
            op.id
            for op in operations_in(project)
            if node_id in consumed(op) or node_id in referenced_tags(op)
        ]

    def _resync(self, project: Project) -> None:
        """After undo or redo: runs of operations that went away stop; every op is judged."""
        ops = operations_in(project)
        present = {op.id for op in ops}
        with self._lock:
            gone = [o for o in [*self._runs, *self._timers] if o not in present]
        for op_id in gone:
            self.cancel(op_id, reason="undo")
        self._reconsider([op.id for op in ops])

    def _reconsider(self, op_ids: Iterable[str]) -> None:
        """Mark each op stale or up to date, and schedule a re-run where ``auto_run`` asks."""
        from stokes.model.graph import mark_stale

        project = self._session.project
        if project is None:
            return
        changed: list[str] = []
        with self._lock:
            for op_id in dict.fromkeys(op_ids):
                node = project.find(op_id)
                if node is None or operation_type(node.type) is None:
                    continue
                run = run_record(node)
                signature = self.signature(project, node)
                # An output of a stale operation changes when that one runs again: wait for it.
                # An imported part being re-imported does not hold the run back (the run waits
                # for the import itself).
                upstream = any(
                    (p := project.find(part)) is not None
                    and bool(p.params.get("derived"))
                    and p.status == "stale"
                    for part in consumed(node)
                )
                current = bool(run.get("ok")) and run.get("signature") == signature
                if current and not upstream:
                    if node.status == "stale":
                        node.status = "ok"
                        changed.append(node.id)
                        changed.extend(self._set_outputs(project, node, "ok"))
                    continue
                if node.status == "ok":
                    node.status = "stale"
                    changed.append(node.id)
                    changed.extend(mark_stale(project, node.id))
                again = signature is not None and signature != run.get("signature")
                job = self._runs.get(op_id)
                covered = job is not None and (job.plan is None or job.plan.seed == signature)
                if node.params.get("auto_run") and again and not upstream and not covered:
                    self._schedule(op_id)
        self._announce(
            [
                ("node.changed", {"node_id": n.id, "path": n.path})
                for n in (project.find(i) for i in dict.fromkeys(changed))
                if n is not None
            ]
        )

    @staticmethod
    def _set_outputs(project: Project, op: Node, status: str) -> list[str]:
        found: list[str] = []
        for part in op.params.get("outputs") or []:
            node = project.find(str(part))
            if node is not None and node.type == "part" and node.status != status:
                node.status = status
                found.append(node.id)
        return found

    def _schedule(self, op_id: str) -> None:
        """A re-run of ``op_id`` 300 ms after the last change (a new change restarts the wait)."""
        with self._lock:
            if self._closed:
                return
            old = self._timers.pop(op_id, None)
            if old is not None:
                old.cancel()
            # The timer is handed to its own callback, so a timer that lost its place to a newer
            # one (a change during the wait) cannot consume the replacement and start a run inside
            # the new window.
            timer = threading.Timer(DEBOUNCE_SECONDS, lambda: self._fire(op_id, timer))
            timer.daemon = True
            timer.name = "stokes-geometry-operation-debounce"
            self._timers[op_id] = timer
            timer.start()

    def _fire(self, op_id: str, timer: threading.Timer) -> None:
        with self._lock:
            if self._timers.get(op_id) is not timer:
                return  # a newer change replaced this timer: its own callback will fire
            self._timers.pop(op_id, None)
            # A replay is walking a run of change records: the operation runs once when that run
            # ends instead of once per record. The timer keeps its place, so the burst is still
            # exactly one run.
            if self._hold:
                return
        self.start(op_id, reason="change")

    @contextlib.contextmanager
    def hold_debounce(self) -> Iterator[None]:
        """Hold re-runs for the block, then start whatever the block left pending.

        A journal has no timing in it, so a replay cannot tell from the file that a burst of changes
        arrived inside one debounce window. Worse, it cannot rely on the debounce either: walking
        the burst takes longer than the window, so each timer fires during the NEXT record and
        forces a run. Holding the timers while the burst is walked — instead of forcing one run per
        record — makes a replay do the work the original session did: one run for the burst. Before
        this: four runs against two, for the same output (T-092).
        """
        with self._lock:
            self._hold += 1
        try:
            yield
        finally:
            with self._lock:
                self._hold -= 1
                pending = list(self._timers) if self._hold == 0 else []
            for op_id in pending:
                self.start(op_id, reason="change")

    # ------------------------------------------------------------------ the project
    def restored(self, derived: list[str]) -> None:
        """The geometry store restored the project's meshes: operations whose outputs have no
        mesh, or that never finished, run again; the others are judged (inputs may have changed
        while the project was closed)."""
        project = self._session.project
        if project is None:
            return
        rebuild: set[str] = set()
        for part in derived:
            node = project.find(part)
            if node is not None:
                rebuild.add(str(node.params.get("derived") or ""))
        ops = operations_in(project)
        started = set()
        for op in ops:
            if (op.id in rebuild or op.status == "new") and not self.busy(op.id):
                self.start(op.id, reason="restore")
                started.add(op.id)
        self._reconsider([op.id for op in ops if op.id not in started])

    def project_opened(self, project: Project, path: Path | None) -> None:
        """Session hook: the runs of the project that went stop."""
        self._stop_all("project")

    def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
        """Session hook: nothing to write (the geometry store copies the B-reps)."""

    def project_closed(self) -> None:
        """Session hook: the runs of the closed project stop."""
        self._stop_all("closed")

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._stop_all("closed")

    def _stop_all(self, reason: str) -> None:
        with self._lock:
            ids = [*self._runs, *self._timers]
        for op_id in ids:
            self.cancel(op_id, reason=reason)

    # ------------------------------------------------------------------ helpers
    def _here(self, job: RunJob, action: Callable[[], T]) -> T | None:
        """Run ``action`` where commands run; None when the run is cancelled before it starts."""
        return self.geometry.on_session_thread(action, job.cancel)

    def _announce(self, events: Iterable[Event]) -> None:
        """Emit what the service changed, without hearing it again (its own ``on_event``)."""
        depth = getattr(self._quiet, "depth", 0)
        self._quiet.depth = depth + 1
        try:
            for event, data in events:
                self._session.emit(event, **data)
        finally:
            self._quiet.depth = depth

    def _emit(self, event: str, op_id: str, **data: Any) -> None:  # Any: JSON payload
        project = self._session.project
        node = project.find(op_id) if project is not None else None
        self._announce([(event, {"node_id": op_id, "path": node.path if node else None, **data})])


def run_record(node: Node) -> dict[str, Any]:  # Any: JSON
    """An operation's last run, as ``meta.run`` holds it (empty before its first)."""
    found = node.meta.get("run")
    return found if isinstance(found, dict) else {}


def _bounded(report: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    """A run report small enough for the project file: as it is, or its keys with a note."""
    text = json.dumps(report, default=str)
    if len(text) <= MAX_REPORT_BYTES:
        loaded: dict[str, Any] = json.loads(text)
        return loaded
    return {"truncated": True, "bytes": len(text), "keys": sorted(report)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
