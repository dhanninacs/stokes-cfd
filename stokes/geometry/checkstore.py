"""The B-rep check service: one background job per batch of parts (ADR-0021 decisions 5, 8.3).

One service per session, in ``session.extensions["checks"]`` (:meth:`CheckStore.of`), beside the
geometry store whose shape folder, dispatcher and session thread it shares.

``geometry.check`` computes the tessellation findings at once (they are immediate) and hands every
part whose B-rep is not checked at the version it is now to :meth:`CheckStore.start`. The job
(``{kind: "check_brep", parts: [spec...]}``, the specs :func:`stokes.geometry.shapes.part_spec`
builds) runs on a daemon thread (``stokes-geometry-check``). Every part loads through the
:class:`~stokes.geometry.shapes.ShapeProvider`, so a derived part of an operation is checked
without re-reading its source file and a fresh shape cache costs no source read at all; the
findings carry the face ids a UI can highlight. Progress reaches clients as
``geometry.check_progress {node_id, index, count}``. On completion every part's
``meta.check.brep`` holds the basis it was checked at, the findings and ``at``, its status follows
the tessellation's own rule (the check's ``worst`` or a B-rep that is not ``ok`` warns), and
``node.changed`` is emitted.

Events::

    geometry.check_progress {node_id, path, index, count}    one per part, as the job reaches it
    geometry.check_failed {node_id, path, code, message}     per part the job could not check
    node.changed {node_id, path}                             the part's meta and status changed

Threads: the job thread talks to its worker process and posts its tree writes through
:meth:`stokes.geometry.store.GeometryStore.on_session_thread`, as imports and operation runs do,
so a part's ``meta`` and status change where commands run. Cancelling sets the job's event, which
kills the worker process as an import's cancel does.

Import rule: the standard library and the light geometry modules at module level; the worker, the
store and the shapes load inside the methods that need them.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from stokes.errors import StokesError, ValidationError
from stokes.geometry.errors import GeometryError, GeometryWorkerError, ImportCancelled
from stokes.log import fields, get_logger

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.geometry.store import GeometryStore
    from stokes.model.nodes import Node
    from stokes.model.project import Project

log = get_logger(__name__)

EXTENSION = "checks"
_QUIET = ("undo", "replaced", "project", "closed", "removed")
"""Why a job was cancelled when nothing should be reported: the nodes went away with the project."""
_store_lock = threading.Lock()


@dataclass(eq=False)
class CheckJob:
    """One running B-rep check: the parts it covers and the basis each was asked at."""

    project: Project
    specs: list[dict[str, Any]]  # Any: JSON provider specs (shapes.part_spec)
    bases: dict[str, str]
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    """Who cancelled it: ``user``, or a quiet reason (project, closed, ...)."""
    started: float = field(default_factory=time.monotonic)
    finished: set[str] = field(default_factory=set)

    def nodes(self) -> list[str]:
        return list(self.bases)


class CheckStore:
    """The session's B-rep checks: one background job per batch, cancellation, results."""

    def __init__(self, session: Session) -> None:
        from stokes.geometry.store import GeometryStore

        self._session = session
        self._store: GeometryStore = GeometryStore.of(session)
        self._lock = threading.RLock()
        self._jobs: dict[str, CheckJob] = {}
        self._followups: dict[str, tuple[dict[str, Any], str]] = {}
        """Parts whose check must run again: ``{part: (spec, basis)}``, queued when a request
        arrives at a newer basis than the job already checking that part."""

    @classmethod
    def of(cls, session: Session) -> CheckStore:
        """The session's check service, created on first use."""
        with _store_lock:
            store = session.extensions.get(EXTENSION)
            if not isinstance(store, CheckStore):
                store = cls(session)
                session.extensions[EXTENSION] = store
            return store

    # ------------------------------------------------------------------ jobs
    def start(
        self,
        specs: Sequence[dict[str, Any]],  # Any: JSON provider specs (shapes.part_spec)
        bases: dict[str, str],
        *,
        project: Project,
    ) -> CheckJob | None:
        """Start one job over ``specs`` covering ``bases`` (part id -> the version asked at).

        `bases` is keyed the way the command names parts (an id or a path: `bases` comes from the
        part nodes it was asked about), while a job keys its `bases` by node id. Both are resolved
        here. A part another job is already checking at the same version is left to that job; a
        request at a NEWER version is queued as a follow-up, so a part that changes while it is
        being checked still gets its new version checked. None comes back when nothing is left to
        do, so a repeated check acquires no worker."""
        by_id: dict[str, tuple[str, dict[str, Any]]] = {}
        for spec in specs:
            part = str(spec.get("part") or "")
            if not part:
                continue
            node = project.find(part)
            by_id[node.id if node is not None else part] = (part, dict(spec))
        wanted: dict[str, str] = {}
        with self._lock:
            for named, basis in bases.items():
                node = project.find(named)
                key = node.id if node is not None else named
                job = self._jobs.get(key)
                if job is None:
                    wanted[key] = basis
                elif job.bases.get(key) != basis and key in by_id:
                    self._followups[key] = (by_id[key][1], basis)
        chosen = [by_id[key][1] for key in wanted if key in by_id]
        if not wanted or not chosen:
            return None
        job = CheckJob(project=project, specs=chosen, bases=wanted)
        with self._lock:
            for part in wanted:
                self._jobs[part] = job
        log.info("check started", extra=fields(parts=len(wanted)))
        threading.Thread(
            target=self._run, args=(job,), name="stokes-geometry-check", daemon=True
        ).start()
        return job

    def running(self, node_id: str | None = None) -> bool:
        """Whether a check job covers ``node_id``, or any part when it is None."""
        with self._lock:
            if node_id is None:
                return bool(self._jobs)
            return node_id in self._jobs

    def wait(self, node_id: str | None = None, timeout: float | None = None) -> bool:
        """Block until the check job(s) covering ``node_id`` (or all) end; False on timeout."""
        if self._store.blocks_session():
            raise ValidationError(
                "waiting for a check would block the server; follow the geometry.check_progress "
                "events"
            )
        deadline = None if timeout is None else time.monotonic() + timeout
        for job in self._covering(node_id):
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not job.done.wait(left):
                return False
        return True

    def cancel(self, node_id: str | None = None, *, reason: str = "user") -> list[str]:
        """Cancel the check covering a node, or every check; returns the parts affected."""
        return self._cancel_jobs(self._covering(node_id), reason)

    def cancel_many(self, node_ids: Sequence[str], *, reason: str = "user") -> list[str]:
        """Cancel every check covering one of ``node_ids``."""
        wanted = set(node_ids)
        with self._lock:
            jobs = list({id(j): j for n, j in self._jobs.items() if n in wanted}.values())
        return self._cancel_jobs(jobs, reason)

    def _covering(self, node_id: str | None) -> list[CheckJob]:
        with self._lock:
            found = {id(j): j for n, j in self._jobs.items() if node_id is None or n == node_id}
        return list(found.values())

    def _cancel_jobs(self, jobs: list[CheckJob], reason: str) -> list[str]:
        affected: list[str] = []
        for job in jobs:
            if not job.cancel.is_set():
                job.reason = reason
                job.cancel.set()
            affected.extend(n for n in job.nodes() if n not in affected)
        return affected

    # ------------------------------------------------------------------ the job thread
    def _run(self, job: CheckJob) -> None:
        from stokes.geometry import worker

        try:
            payload = worker.acquire().check_brep(
                job.specs,
                on_progress=lambda part, index, count: self._progress(job, part, index, count),
                cancel=job.cancel,
            )
        except GeometryError as exc:
            self._failed(job, exc)
        except Exception as exc:
            log.exception("check failed", extra=fields(parts=len(job.bases)))
            self._failed(job, GeometryWorkerError(f"the check failed: {exc}"))
        else:
            try:
                self._finish(job, payload)
            except Exception as exc:
                log.exception("check could not be finished", extra=fields(parts=len(job.bases)))
                self._failed(job, GeometryWorkerError(f"the check could not be finished: {exc}"))
        finally:
            drained: dict[str, tuple[dict[str, Any], str]] = {}
            with self._lock:
                for part in job.nodes():
                    if self._jobs.get(part) is job:
                        del self._jobs[part]
                for part in job.nodes():
                    pending = self._followups.pop(part, None)
                    if pending is not None:
                        drained[part] = pending
            job.done.set()
            self._drain_followups(job, drained)

    def _drain_followups(
        self, job: CheckJob, drained: dict[str, tuple[dict[str, Any], str]]
    ) -> None:
        """Run the checks queued while ``job`` held their part: the part changed under the job, so
        its new version is checked now. A part that went away is dropped."""
        for part, (spec, basis) in drained.items():
            try:
                self.start([spec], {part: basis}, project=job.project)
            except StokesError as exc:
                log.warning(
                    "queued check not started",
                    extra=fields(part=part, code=exc.code, error=exc.message),
                )

    def _progress(self, job: CheckJob, part: str, index: int, count: int) -> None:
        if job.cancel.is_set() or part not in job.bases:
            return
        self._emit("geometry.check_progress", part, self._path(job, part), index=index, count=count)

    def _finish(self, job: CheckJob, payload: dict[str, Any]) -> None:
        raw = payload.get("parts")
        if not isinstance(raw, list):
            raise GeometryWorkerError("the geometry worker sent no check results")
        findings = {
            str(item["part"]): item
            for item in raw
            if isinstance(item, dict) and isinstance(item.get("part"), str)
        }
        missing = [part for part in job.bases if part not in findings]
        if missing:
            raise GeometryWorkerError("the geometry worker sent no result for some parts")
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        written = self._store.on_session_thread(
            lambda: self._record(job, findings, stamp), job.cancel
        )
        if written:
            job.finished.update(written)

    def _record(self, job: CheckJob, findings: dict[str, dict[str, Any]], stamp: str) -> list[str]:
        """Write each part's findings into its ``meta.check.brep`` where commands run."""
        if self._session.project is not job.project:
            return []
        written: list[str] = []
        for part, basis in job.bases.items():
            node = job.project.find(part)
            if node is None or node.type != "part":
                continue
            if self._store.basis(node) != basis:
                continue  # the part changed while the job ran: these findings are of an old one
            item = {k: v for k, v in findings[part].items() if k != "part"}
            check = self._check_of(node)
            check["brep"] = {**item, "checked": True, "basis": basis, "at": stamp}
            node.meta = {**node.meta, "check": check}
            self._status(job, node, check, item)
            self._emit("node.changed", part, node.path)
            written.append(part)
        log.info("check finished", extra=fields(parts=len(written)))
        return written

    @staticmethod
    def _check_of(node: Node) -> dict[str, Any]:  # Any: JSON
        """A copy of the part's ``meta.check`` (the last tessellation summary), so it is replaced
        whole rather than mutated in place."""
        stored = node.meta.get("check")
        return dict(stored) if isinstance(stored, dict) else {}

    @staticmethod
    def _status(
        job: CheckJob, node: Node, check: dict[str, Any], item: dict[str, Any]
    ) -> None:  # Any: JSON
        """The part's status follows the tessellation's rule (ADR-0020 decision 6): warning for a
        finding of the last check or a B-rep that is not ok, ok when neither; a failed, stale or
        new part is left alone."""
        if node.status not in ("ok", "warning"):
            return
        warns = str(check.get("worst") or "ok") in ("error", "warning") or not item.get("ok", True)
        job.project.set_status(node.id, "warning" if warns else "ok")

    def _failed(self, job: CheckJob, exc: GeometryError) -> None:
        if job.reason in _QUIET:
            return  # the nodes went away or another job took over: nothing to report
        if isinstance(exc, ImportCancelled):
            exc = ImportCancelled("the check was cancelled", details=exc.details)
        unfinished = [part for part in job.bases if part not in job.finished]
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        self._store.on_session_thread(
            lambda: self._record_failure(job, unfinished, exc, stamp), job.cancel
        )
        log.warning(
            "check failed",
            extra=fields(parts=len(unfinished), code=exc.code, error=exc.message),
        )

    def _record_failure(
        self, job: CheckJob, parts: Sequence[str], exc: GeometryError, stamp: str
    ) -> None:
        if self._session.project is not job.project:
            return
        for part in parts:
            node = job.project.find(part)
            if node is None or node.type != "part":
                continue
            check = self._check_of(node)
            check["brep"] = {
                "checked": False,
                "reason": exc.message,
                "basis": job.bases.get(part, ""),
                "at": stamp,
            }
            node.meta = {**node.meta, "check": check}
            self._emit(
                "geometry.check_failed",
                part,
                node.path,
                code=exc.code,
                message=exc.message,
            )

    # ------------------------------------------------------------------ helpers
    def _path(self, job: CheckJob, part: str) -> str | None:
        node = job.project.find(part)
        return None if node is None else node.path

    def _emit(self, event: str, node_id: str, path: str | None, **data: Any) -> None:  # Any: JSON
        self._session.emit(event, node_id=node_id, path=path, **data)
