"""The session's materials service (ADR-0018 decision 6; contract "Evaluation and caching" and
"Events"): background fits with progress and cancellation, the library evaluation cache, and
the listeners that keep library properties detached and stale as the model changes.

One service per session, in ``session.extensions["materials"]`` (:meth:`MaterialsService.of`),
made by the first ``material.*`` command; it imports nothing heavy (the provider libraries live
in the worker process, ``worker.py``).

Fits. A fit whose estimated cost fits the inline budget (0.2 s) runs inside the command and
lands in the command's own undo step. A slower one, or one asked for with ``wait: false``, runs
on a daemon thread (``stokes-materials-fit``) that drives the worker in chunks: the properties
are ``stale`` meanwhile, ``material.fit_progress`` reports at most ten times a second, and the
result lands as its own undo step ("fit <material> from <provider>") where commands run: on the
server's event loop when the command that started it ran there (its loop is captured then),
otherwise on the fit thread, which is what a script that waits gets. A fit whose material
changed state, or went away, meanwhile is dropped with ``material.fit_failed``.
``material.cancel_fit`` cancels between chunks.

Listeners. ``node.changed`` from ``parameter.set`` on a library property marks it detached in
the same undo step (decisions log F6 (c)): the set is undone and pushed again inside a composite
with the mark, using the undo stack's public operations only. A change of a library material's
state (provider, fluid, composition, basis, phase, T_ref, p_ref, range) marks its attached
library properties ``stale``; ``history.changed`` (undo, redo) recomputes the marks, since stale
is a status, not an undoable value. A detached property is also recognised without the
listener (``library.is_detached`` compares its parameters with the fit's signature), so an edit
made before the service existed is not missed.

Import rule: the standard library, ``stokes.model``, ``stokes.api`` types for annotations only,
and this package.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stokes.errors import StokesError, ValidationError
from stokes.log import fields, get_logger
from stokes.model.commands import Composite, SetParam
from stokes.physics.materials.authoring import SetMeta
from stokes.physics.materials.errors import Busy, Cancelled, EvaluationFailed, StaleFit
from stokes.physics.materials.library import (
    STATE_KEYS,
    FitOutcome,
    LibraryState,
    is_detached,
    is_library,
    refit_command,
    run_fit,
    stale_properties,
    state_of,
)

if TYPE_CHECKING:
    from pathlib import Path

    from stokes.api.session import Session
    from stokes.model.nodes import Node
    from stokes.model.project import Project
    from stokes.physics.materials.fit import FitResult
    from stokes.physics.materials.methods import Array
    from stokes.physics.materials.providers.base import LibraryInfo, PropertyGrid

log = get_logger(__name__)

EXTENSION = "materials"
CACHE_ENTRIES = 64
PROGRESS_INTERVAL = 0.1
"""Seconds between two ``material.fit_progress`` events of one fit (10 Hz at most)."""
INLINE_BUDGET = 0.2
"""Estimated seconds below which a fit runs inside its command."""
WAIT_TIMEOUT = 900.0
_store_lock = threading.Lock()
Dispatch = Callable[[Callable[[], None]], object]


@dataclass(eq=False)
class FitJob:
    """One background fit of one material."""

    material: str
    name: str
    provider: Any  # Any: a Provider (RemoteProvider for the library ones)
    state: LibraryState
    info: LibraryInfo
    properties: list[str]
    methods: dict[str, str]
    required: list[str]
    provider_version: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    outcome: FitOutcome | None = None
    error: StokesError | None = None
    dispatch: Dispatch | None = None


def statuses(material: Node) -> dict[str, str]:
    """The status of each property of a material, by node id."""
    return {c.id: c.status for c in material.children if c.type == "property"}


class MaterialsService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._lock = threading.RLock()
        self._jobs: dict[str, FitJob] = {}
        self._cache: OrderedDict[tuple[Any, ...], PropertyGrid] = OrderedDict()
        self._quiet = threading.local()
        self._loop_thread: int | None = None
        session.events.subscribe("node.changed", self._on_changed)
        session.events.subscribe("history.changed", self._on_history)

    @classmethod
    def of(cls, session: Session) -> MaterialsService:
        """The session's service, created on first use."""
        with _store_lock:
            found = session.extensions.get(EXTENSION)
            if not isinstance(found, MaterialsService):
                found = cls(session)
                session.extensions[EXTENSION] = found
            return found

    # ------------------------------------------------------------------ where commands run
    def _dispatch(self) -> Dispatch | None:
        """The running event loop's ``call_soon_threadsafe`` when a command runs on one (the
        server), so that a background result lands there; None for scripts and the CLI."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._loop_thread = threading.get_ident()
        return loop.call_soon_threadsafe

    def on_event_loop(self) -> bool:
        """Whether this command runs on the server's event loop (where blocking is costly)."""
        return self._dispatch() is not None

    @contextmanager
    def quiet(self) -> Iterator[None]:
        """Changes made by the materials commands themselves: the listeners stay out of them."""
        depth = getattr(self._quiet, "depth", 0)
        self._quiet.depth = depth + 1
        try:
            yield
        finally:
            self._quiet.depth = depth

    def _is_quiet(self) -> bool:
        return bool(getattr(self._quiet, "depth", 0))

    # ------------------------------------------------------------------ jobs
    def busy(self, material_id: str) -> bool:
        with self._lock:
            return material_id in self._jobs

    def require_idle(self, material_id: str, name: str) -> None:
        if self.busy(material_id):
            raise Busy(
                f"a background fit of {name} is running; wait for material.fitted or cancel it "
                "with material.cancel_fit",
                details={"material": material_id},
            )

    def start(self, job: FitJob) -> None:
        """Run a fit in the background; the material's library properties go stale now."""
        job.dispatch = self._dispatch()
        with self._lock:
            if job.material in self._jobs:
                raise Busy(f"a background fit of {job.name} is already running")
            self._jobs[job.material] = job
        self._resync_one(job.material)
        thread = threading.Thread(
            target=self._run, args=(job,), name="stokes-materials-fit", daemon=True
        )
        thread.start()

    def _run(self, job: FitJob) -> None:
        last = [0.0]

        def progress(done: int, total: int) -> None:
            now = time.monotonic()
            if done < total and now - last[0] < PROGRESS_INTERVAL:
                return
            last[0] = now
            self._session.emit(
                "material.fit_progress", material=job.material, done=done, total=total
            )

        try:
            job.outcome = run_fit(
                job.provider,
                job.state,
                job.info,
                job.properties,
                job.methods,
                required=job.required,
                provider_version=job.provider_version,
                on_progress=progress,
                cancel=job.cancel,
            )
        except StokesError as exc:
            job.error = exc
        except Exception as exc:
            log.exception("material fit failed", extra=fields(material=job.material))
            job.error = EvaluationFailed(f"the fit of {job.name} failed: {exc}")
        if job.cancel.is_set() and job.error is None:
            job.error = Cancelled(f"the fit of {job.name} was cancelled")
        dispatch = job.dispatch
        if dispatch is None:
            self._land(job)
            return
        try:
            dispatch(lambda: self._land(job))
        except RuntimeError:  # the event loop has closed: nothing else changes the model now
            self._land(job)

    def _land(self, job: FitJob) -> None:
        """Apply a finished fit where commands run, or report why it was dropped; then the
        material is no longer busy and its statuses are announced."""
        project = self._session.project
        material = project.find(job.material) if project is not None else None
        before = statuses(material) if material is not None else {}
        fitted: list[tuple[str, FitResult]] = []
        try:
            fitted = self._apply(job)
        except StokesError as exc:
            self._failed(job, exc)
        except Exception as exc:
            log.exception("material fit could not be applied", extra=fields(material=job.material))
            self._failed(
                job, EvaluationFailed(f"the fit of {job.name} could not be applied: {exc}")
            )
        finally:
            with self._lock:
                if self._jobs.get(job.material) is job:
                    del self._jobs[job.material]
            for prop, result in fitted:
                self._session.emit(
                    "material.fitted",
                    material=job.material,
                    property=prop,
                    method=result.method,
                    max_rel_err=result.max_rel_err,
                    tolerance=result.tolerance,
                    ok=result.ok,
                )
            if material is not None and material.parent is not None:
                self.resync(material, before)
            if fitted:
                with self.quiet():
                    self._session.emit("history.changed")
            job.done.set()

    def _apply(self, job: FitJob) -> list[tuple[str, FitResult]]:
        """Write the fit as its own undo step; the properties written and their results."""
        session = self._session
        project = session.project
        material = project.find(job.material) if project is not None else None
        if job.error is not None:
            raise job.error
        if project is None or material is None or material.type != "material":
            return []
        if state_of(material).signature() != job.state.signature():
            raise StaleFit(f"{material.name} changed while it was being fitted; run material.refit")
        outcome = job.outcome
        if outcome is None:
            return []
        for prop in list(outcome.results):
            node = material.child(prop)
            if node is None or is_detached(node):
                del outcome.results[prop]
        cmd = refit_command(
            material, outcome, label=f"fit {material.name} from {job.state.provider}"
        )
        with self.quiet():
            session.undo.push(cmd, project)
            for part in cmd.commands:
                if isinstance(part, SetParam) and part.node_id is not None:
                    session.emit(
                        "node.changed", node_id=part.node_id, path=part.node_path, param=part.param
                    )
        return list(outcome.results.items())

    def _failed(self, job: FitJob, exc: StokesError) -> None:
        for prop in job.properties:
            self._session.emit(
                "material.fit_failed",
                material=job.material,
                property=prop,
                code=exc.code,
                message=exc.message,
            )

    def cancel(self, material_id: str | None = None) -> list[str]:
        """Cancel the background fit of one material, or every one; the materials affected."""
        with self._lock:
            jobs = [j for m, j in self._jobs.items() if material_id is None or m == material_id]
        for job in jobs:
            job.cancel.set()
        return [job.material for job in jobs]

    def wait(self, material_id: str | None = None, timeout: float | None = WAIT_TIMEOUT) -> bool:
        """Block until the background fit(s) end; False on timeout. Refused on the event loop,
        where the result itself has to land."""
        if self._loop_thread is not None and threading.get_ident() == self._loop_thread:
            raise ValidationError(
                "waiting for a fit would block the server; follow the material.* events"
            )
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            jobs = [j for m, j in self._jobs.items() if material_id is None or m == material_id]
        for job in jobs:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not job.done.wait(left):
                return False
        return True

    # ------------------------------------------------------------------ the session's project
    def project_opened(self, project: Project, path: Path | None) -> None:
        """Session hook: another project is open; the fits of the one it replaces stop."""
        self.cancel()

    def project_closed(self) -> None:
        """Session hook: no project is open; its fits stop."""
        self.cancel()

    def pending(self, material_id: str | None = None) -> list[str]:
        """The materials with a running fit, or the properties one material's fit will write."""
        with self._lock:
            if material_id is None:
                return list(self._jobs)
            job = self._jobs.get(material_id)
            return list(job.properties) if job is not None else []

    # ------------------------------------------------------------------ the evaluation cache
    def library_grid(
        self,
        provider_name: str,
        provider: Any,  # Any: a Provider
        material: Node,
        t: Array,
        p: float,
    ) -> PropertyGrid:
        """The provider's values for a library material on a grid: an LRU of 64 entries keyed
        by provider, fluid, composition, basis, phase, pressure and the grid's hash."""
        params = material.params
        key = (
            provider_name,
            str(params.get("fluid", "")),
            str(params.get("composition", "")),
            str(params.get("basis", "")),
            str(params.get("phase", "auto")),
            float(p),
            hashlib.sha1(t.tobytes(), usedforsecurity=False).hexdigest(),
        )
        with self._lock:
            found = self._cache.get(key)
            if found is not None:
                self._cache.move_to_end(key)
                return found
        grid: PropertyGrid = provider.evaluate(key[1], key[2], key[3], key[4], t, float(p))
        with self._lock:
            self._cache[key] = grid
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
        return grid

    def cache_size(self) -> int:
        with self._lock:
            return len(self._cache)

    # ------------------------------------------------------------------ stale marks
    def _resync_one(self, material_id: str) -> None:
        project = self._session.project
        material = project.find(material_id) if project is not None else None
        if material is not None and material.type == "material":
            self.resync(material)

    def resync(self, material: Node, before: dict[str, str] | None = None) -> None:
        """Mark the material's library properties ``stale`` (waiting for a fit) or back to
        ``ok`` (a ``failed`` status is left alone), and announce every status that differs from
        ``before`` (default: the statuses now), as ``node.changed`` with ``param: "status"``."""
        project = self._session.project
        if project is None:
            return
        earlier = statuses(material) if before is None else before
        with self._lock:
            job = self._jobs.get(material.id)
        pending = set(job.properties) if job is not None else set()
        stale = {p.id for p in stale_properties(material)}
        changed: list[Node] = []
        for prop in material.children:
            if prop.type != "property" or prop.status == "failed":
                continue
            waiting = prop.name in pending and not is_detached(prop)
            want = "stale" if prop.id in stale or waiting else "ok"
            if prop.status != want:
                project.set_status(prop.id, want)
            if earlier.get(prop.id, want) != want:
                changed.append(prop)
        with self.quiet():
            for prop in changed:
                self._session.emit("node.changed", node_id=prop.id, path=prop.path, param="status")

    def resync_all(self) -> None:
        project = self._session.project
        if project is None:
            return
        for node in list(project.nodes()):
            if node.type == "material" and (is_library(node) or node.id in self._jobs):
                self.resync(node)

    # ------------------------------------------------------------------ listeners
    def _on_history(self, event: str, data: dict[str, Any]) -> None:  # Any: JSON
        if not self._is_quiet():
            self.resync_all()

    def _on_changed(self, event: str, data: dict[str, Any]) -> None:  # Any: JSON
        if self._is_quiet():
            return
        project = self._session.project
        node_id, param = data.get("node_id"), data.get("param")
        node = project.find(str(node_id)) if project is not None and node_id else None
        if node is None or not isinstance(param, str):
            return
        if node.type == "property" and node.parent is not None:
            self._detach(node, param)
        elif node.type == "material" and param in STATE_KEYS:
            self.resync(node)

    def _detach(self, prop: Node, param: str) -> None:
        """``parameter.set`` changed a property a provider or the catalogue wrote: mark it
        detached in the same undo step (the set is undone and pushed again with the mark)."""
        if param in ("kind", "dimension", "status") or prop.meta.get("detached"):
            return
        if not any(key in prop.meta for key in ("fit", "library", "signature")):
            return
        session = self._session
        project = session.project
        if project is None:
            return
        undo = session.undo
        with self.quiet():
            last = undo.undo(project)
            if not (isinstance(last, SetParam) and last.node_id == prop.id and last.param == param):
                if last is not None:
                    undo.redo(project)
                return
            parts: list[Any] = [last, SetMeta(prop.id, "detached", True)]  # Any: Command
            if "missing" in prop.meta:
                parts.append(SetMeta(prop.id, "missing", None))
            undo.push(Composite(f"set {param} (detached from the library)", parts), project)
            session.emit("node.changed", node_id=prop.id, path=prop.path)
        if prop.parent is not None:
            self.resync(prop.parent)
