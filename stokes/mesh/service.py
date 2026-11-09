"""The mesh service: build a snapshot, run a job, keep one result (T-145b, T-207).

This is where the pieces meet. `mesh.generate` builds a snapshot (T-206), writes a case through the
driver, sends it to the agent as a job (T-203/T-204), follows the job's events, and keeps **one
result
node** at `/mesh/results/<mesher>` up to date.

## The threading picture, which is the part to get right

Three threads are involved and each is only allowed to do one thing:

1. **The session thread** — where commands run. `generate` builds the snapshot, writes the case and
   starts the job here. Every change to the tree happens here, through
   `GeometryStore.on_session_thread`, because that is where the model is safe to touch.
2. **The runner's event thread** — `LocalRunner`'s asyncio loop, where the agent delivers
events. The
   listener runs here and **must return at once**: it maps an event to a service event and hands any
   real work to a worker. It must never call `runner.call`, which would block the loop it is running
   on — `LocalRunner.call` refuses that outright.
3. **The finish worker** — a short-lived thread that reads `job.status` (a blocking call, so not
on the
   loop) and then updates the tree on the session thread.

## Two things this deliberately does not do

**Results are not undo steps.** A result is written by a job, like an operation's output;
undoing past a
completed mesh would leave the settings that produced it. `mesh.generate` is journaled with the
result id it chose, so a replay recreates the same node.

**Nothing runs by itself.** An operation may `auto_run` on a change; a mesh does not. A change makes
the result **stale**, which is reported and costs nothing, because meshing is expensive and a user
who changed a size has not necessarily asked for a new mesh.
"""

from __future__ import annotations

import contextlib
import json
import math
import shutil
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.log import fields, get_logger
from stokes.mesh.basis import changed_tasks, mesh_basis, mesh_basis_parts
from stokes.mesh.drivers import driver_for
from stokes.mesh.errors import NO_INSTALL_ADVICE, MeshBusy, MeshInvalid
from stokes.mesh.model import MESHERS, SETTINGS_PATH

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session
    from stokes.model.nodes import Node
    from stokes.model.project import Project

log = get_logger(__name__)

#: The session extension key.
EXTENSION = "mesh"

#: Where results live, and how long a generate waits.
RESULTS_PATH = "/mesh/results"
WAIT_SECONDS = 1800.0

#: How long `job.create` may take. It **stages every file of the case**, so it is the one agent call
#: here whose cost scales with the model rather than being a fixed round trip; `LocalRunner.call`
#: defaults to 60 s, which a large case on a slow disk could reach.
CREATE_TIMEOUT_S = 300.0

#: How long a change may keep arriving before the staleness check runs. One timer per burst, so a
#: parameter typed into a box does not rebuild the snapshot on every keystroke.
STALE_DEBOUNCE_S = 0.3

#: At most this many `mesh.progress` and `mesh.log` events a second.
EVENT_INTERVAL_S = 0.1

#: The final job states, after which nothing more arrives.
_FINAL = ("done", "failed", "cancelled", "interrupted")

#: Where the desktop keeps a result's surfaces: `<cache>/mesh/<result id>.stkmsh` (ADR-0024 5.3).
SURFACE_FOLDER = "mesh"
SURFACE_SUFFIX = ".stkmsh"

#: How long `extract.surfaces` may take. It reads the whole mesh on the host (seconds for a million
#: cells), which is more than a round trip's worth, like `job.create`.
EXTRACT_TIMEOUT_S = 600.0

#: How long the fidelity measurement may take.
#:
#: **Its own timeout, and a long one**: the agent streams every byte of a staged surface and takes
#: distances from up to `MAX_SAMPLES` boundary points - seconds on a real part, minutes on a large
#: one. It runs after the mesh is already `done`, so a slow measurement delays a REPORT rather than
#: the mesh, and a timeout leaves the block absent rather than failing a mesh that exists.
FIDELITY_TIMEOUT_S = 900.0

#: How long `extract.quality` may take (brief M-506, R14c): the distributions are computed on
#: the host AFTER the result is finished, so a slow one delays a REPORT, never the mesh — the
#: same reasoning as the fidelity timeout, with its own budget.
QUALITY_TIMEOUT_S = 300.0

#: The layer coverage under which a wall earns a warning (brief M-504): layers grew on fewer than
#: 80 % of the wall's wanted thickness. A warning, not a failed mesh - collapsed layers are an
#: answer about the geometry, and the user decides.
LAYER_COVERAGE_WARN_PCT = 80.0

#: How many results' surface files are kept in memory, as `memoryview`s (ADR-0024 5.6).
SURFACE_LRU = 2

#: The colour of a patch no tag colours.
DEFAULT_PATCH_COLOR = "#9aa3ad"


@dataclass(frozen=True)
class _FidelityCase:
    """What the fidelity measurement needs about the case, kept from the snapshot.

    **Three values, not the snapshot.** `generate` builds a snapshot, uses it and lets it go; by the
    time the job finishes there is nothing left to read the surfaces from, and holding a whole
    snapshot alive per run to answer one optional question would be the wrong trade.
    """

    surfaces: tuple[str, ...]
    characteristic_length: float
    cell_size: float | None

    @classmethod
    def of(cls, snap: Any) -> _FidelityCase | None:
        """From a snapshot, or None when it has nothing to measure against.

        A `MeshSnapshot` (blockMesh) has no `stl_files` at all, and a box-domain snappy case has an
        empty set: both mean "no CAD was staged", and the block is absent rather than empty.
        """
        files = getattr(snap, "stl_files", None)
        if not files:
            return None
        lo = getattr(snap, "background_min", None)
        hi = getattr(snap, "background_max", None)
        length = 0.0
        if lo and hi:
            length = float(sum((b - a) ** 2 for a, b in zip(lo, hi, strict=True)) ** 0.5)
        size = getattr(snap, "base_size", None)
        return cls(
            surfaces=tuple(sorted(files)),
            characteristic_length=length,
            cell_size=float(size) if size else None,
        )


@dataclass(frozen=True)
class _Surfaces:
    """What fetching a finished job's surfaces came to: its patches, or why there are none."""

    patches: tuple[dict[str, Any], ...] = ()
    error: tuple[str, str] | None = None
    #: `"no_vtk"` when the host cannot extract; shown by `mesh.status`.
    reason: str = ""
    #: Each planar symmetry patch's flatness (`stokes.mesh.flatness`), measured from its points.
    flatness: tuple[dict[str, Any], ...] = ()


#: The most `meta.quality_dist` may hold (the second review's R9): the project's own load
#: limit is 64 MiB, and the distributions are the page's reading material, not a dump — 1 MiB
#: is far above the 40-bin histograms and the 50 worst faces the reply should carry.
QUALITY_DIST_MAX_BYTES = 1024 * 1024

#: The most bins any one histogram may carry, and the most worst-faces rows (R9's cap).
QUALITY_DIST_MAX_BINS = 256
QUALITY_DIST_MAX_WORST = 512


def _symmetry_flatness(data: memoryview, header: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Each planar symmetry patch's flatness, from the surface artifact. **A worker thread.**

    Never raises: the measurement is a report about a mesh that exists, so a patch it cannot read
    is left out rather than failing the fetch.
    """
    from stokes.geometry.errors import MeshFormatError
    from stokes.mesh.flatness import SYMMETRY_TYPES, measure
    from stokes.mesh.surfaces import patch_arrays

    found: list[dict[str, Any]] = []
    for index, patch in enumerate(header.get("patches") or ()):
        if str(patch.get("patch_type") or "") not in SYMMETRY_TYPES:
            continue
        try:
            positions, _triangles, _edges = patch_arrays(data, header, index)
        except MeshFormatError:
            continue
        flat = measure(str(patch.get("name") or f"patch_{index}"), positions)
        if flat is not None:
            found.append(flat.to_dict())
    return tuple(found)


def _check_dist_reply(reply: Any) -> dict[str, Any]:
    """The agent's reply, validated for the meta: size, shape, finite numbers.

    **Not a trusted document.** The reply travels from a process that reads a mesh's files, and
    the meta is persisted into `project.json` — whose own load limit is 64 MiB, so an
    unvalidated reply could push the project past it or fill the page with NaN. A reply that
    fails becomes `{"error": ...}` with the reason; nothing is partially stored.
    """

    def refuse(reason: str) -> dict[str, Any]:
        return {"error": reason}

    if not isinstance(reply, dict):
        return refuse("the quality reply is not an object")
    if len(json.dumps(reply, default=str)) > QUALITY_DIST_MAX_BYTES:
        return refuse("the quality reply is too large to store")
    if "error" in reply:
        return refuse(str(reply["error"])[:200])
    problem = _metric_problem(reply)
    return refuse(problem) if problem else reply


def _metric_problem(reply: dict[str, Any]) -> str:
    """The first structural problem in the reply's metrics and worst faces, or "" for none."""
    metrics = reply.get("metrics")
    if metrics is not None:
        if not isinstance(metrics, dict):
            return "the quality reply's metrics are not an object"
        for name, metric in metrics.items():
            if not isinstance(metric, dict):
                return f"the metric {name!r} is not an object"
            problem = _one_metric_problem(name, metric)
            if problem:
                return problem
    worst = reply.get("worst")
    if worst is not None and (not isinstance(worst, list) or len(worst) > QUALITY_DIST_MAX_WORST):
        return "the quality reply carries too many worst faces"
    return ""


def _one_metric_problem(name: str, metric: dict[str, Any]) -> str:
    """The first problem in one metric's arrays and numbers, or "" for none."""
    for key in ("edges", "counts"):
        values = metric.get(key)
        if values is None:
            continue
        if not isinstance(values, list) or len(values) > QUALITY_DIST_MAX_BINS:
            return f"the metric {name!r} carries too many {key}"
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            return f"the metric {name!r} carries a non-finite {key}"
    for key in ("max", "mean"):
        value = metric.get(key)
        if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
            return f"the metric {name!r} carries a non-finite {key}"
    return ""


class MeshService:
    """One project's meshing: the running job, the result node and the staleness check."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._lock = threading.RLock()
        self._job: str | None = None
        self._result_id: str | None = None
        self._step: str | None = None
        self._total = 0
        self._done_steps = 0
        self._engine = ""
        self._warnings: tuple[str, ...] = ()
        #: What the fidelity measurement needs about the case, from the snapshot. **`None` until a
        #: generate sets it**, which is correct: a service that has not generated anything has no
        #: case to measure, and reading it must not be an AttributeError.
        self._fidelity_case: _FidelityCase | None = None
        self._unsubscribe: Callable[[], None] | None = None
        # **Per JOB, not per service.** It was created once here and only ever cleared, which left a
        # window on every second generate: `cancel()` does not clear it, so between `cancel()` and
        # `_reset` it was still set from the PREVIOUS job and `wait()` returned True while
        # nothing was
        # running. Journal replay reached it through `settle`. `_new_job_event` replaces it
        # under the
        # lock; `wait` reads the same event it is waiting on for the job it asked about.
        self._finished: threading.Event | None = None
        self._cancel = threading.Event()
        self._stale_timer: threading.Timer | None = None
        self._last_event: dict[str, float] = {}
        #: (step, stage) of the last `mesh.progress` sent, so a change is never throttled away.
        self._last_progress: tuple[str, str] = ("", "")
        self._closed = False
        #: The surface files in memory: path -> ((size, mtime_ns), the bytes, the validated header).
        self._surfaces: OrderedDict[str, tuple[tuple[int, int], memoryview, dict[str, Any]]] = (
            OrderedDict()
        )
        #: The scratch folder the cache used while the project had no folder, for `project_saved`.
        self._scratch: Path | None = None
        #: The prepared basis parts and dry-run precheck, keyed by the project revision (brief
        #: M-501's 200 ms cache). **Filled by the debounced stale pass**, which already builds one
        #: snapshot per burst of edits, and read by `mesh.tasks` and `mesh.precheck`, so neither
        #: builds a snapshot per call. One slot: a revision supersedes the one before it.
        self._prepared: dict[str, Any] | None = None
        self._prepared_revision: int | None = None

    @classmethod
    def of(cls, session: Session) -> MeshService:
        """The session's service, created on first use, like `GeometryStore.of`."""
        found = session.extensions.get(EXTENSION)
        if not isinstance(found, cls):
            found = cls(session)
            session.extensions[EXTENSION] = found
        return found

    # ------------------------------------------------------------------ generate

    def generate(
        self,
        project: Project,
        host: str | None,
        force: bool,
        result_id: str | None,
    ) -> dict[str, Any]:
        """Build, write and start a mesh job. Runs on the session thread, inside the command.

        Raises `MeshInvalid` for settings that cannot be a case — those are raised on replay too,
        and a replay that cannot build the same snapshot should stop rather than write a different
        result. **A missing host does not raise**: it writes a `skipped` result, so a journal
        recorded on a machine with OpenFOAM replays on one without.
        """
        from stokes.mesh.snapshot import snapshot_for
        from stokes.run import staging
        from stokes.run.hosts import HostRegistry

        settings = project.find(SETTINGS_PATH)
        if settings is None:
            raise MeshInvalid("there is no mesh settings node; run mesh.configure first")
        host = host or str(settings.params.get("host") or "local")
        snap = snapshot_for(self._session, settings)
        if not force:
            reused = self._reuse(snap, host)
            if reused is not None:
                return reused

        runner = HostRegistry.of(self._session).runner(host)
        install: str | None = None
        engine = ""
        if runner.available:
            try:
                record = HostRegistry.of(self._session).probe(host)
                # **An engine ID, never the bashrc path** (FIXES-A A2): the agent resolves it
                # among the installs it found itself, and refuses a path.
                install = (record.get("record") or {}).get("default_install_id")
                engine = _engine_of(record.get("record") or {})
            except Exception:
                log.warning("the host probe failed", extra=fields(host=host))
                install = None
        if not runner.available or not install:
            # **`skipped`, never `failed`** (ADR-0024): a machine without OpenFOAM is a fact about
            # the machine, and a replay that stops there is a replay that cannot be replayed.
            why = runner.why or NO_INSTALL_ADVICE
            return self._no_host(project, snap, host, why, result_id)

        engine = engine or "org-14"
        # **The case is built BEFORE anything is replaced**: a snapshot the driver cannot express
        # raises here, and a refusal must not leave a `running` result behind with no job.
        basis, files, job_steps = self._case_for(snap, engine)
        # **One result, so the previous run must stop first.** A job that kept going would write its
        # state into the node that replaced it, and the user would see the old mesh's outcome on the
        # new node. `_replace` removes the node; this stops the process behind it.
        # **The job's own event, created BEFORE `cancel()`.** `cancel()` does not clear
        # `_finished`, so
        # creating it here is what closes the first window: from this instant a `wait()` waits
        # on THIS
        # job, however far the generate has got.
        self._new_job_event()
        self.cancel()
        # **And the old job is forgotten at once** (FIXES-B B8). Its final event can still arrive
        # after `_replace` has pointed `_result_id` at the NEW result; with `_job` still naming the
        # old job it would pass the listener's job check and mark the new result cancelled. With
        # `_job` cleared, every event of the old job is ignored, and `_finish` re-checks the job.
        with self._lock:
            self._job = None
        result = self._replace(project, snap, host, basis, engine, result_id)
        self._reset(result, snap, engine, basis)

        try:
            # **An explicit timeout, because this call WRITES the case.** `job.create` stages every
            # file, and the default is 60 s (`LocalRunner.call`) — a large case on a slow disk could
            # approach that, and every other call in this service names its own timeout. `job.start`
            # below keeps the default on purpose: it only spawns a process.
            #
            # **`staging.stage`, not a bare `job.create`**: a case whose surfaces exceed one payload
            # goes up in chunks through `job.put`, which is the agent's own mechanism and was
            # unreachable from any client until now. A finned part's ASCII STL is tens of MiB, so
            # the inline-only path could not stage the parts this exists to mesh.
            created = staging.stage(
                runner.call,
                files,
                steps=job_steps,
                install=install,
                label=result.id,
                timeout=CREATE_TIMEOUT_S,
            )
        except Exception as exc:
            self._fail(project, result, "mesh.no_host", f"the host refused the job: {exc}")
            return self._info()
        job = str(created["job"])
        with self._lock:
            self._job = job
            self._total = len(job_steps)
            self._done_steps = 0
        try:
            self._subscribe(runner)
            project.set_param(result.id, "job", job)
            runner.call("job.start", job=job)
        except Exception as exc:
            # **A job that cannot start must end the result** (FIXES-B B3). Outside the `try`, the
            # result stayed `running` for ever and `_job` stayed set, so `wait` never returned and
            # the next generate cancelled a job that never ran. `_fail` releases the runner state.
            self._discard_job(runner, job)
            self._fail(project, result, "mesh.no_host", f"the host could not start the job: {exc}")
        return self._info()

    def _discard_job(self, runner: Any, job: str) -> None:
        """Best effort: remove a job that was created and never started. Never raises."""
        try:
            runner.call("job.delete", job=job, timeout=30.0)
        except Exception:
            log.warning("could not remove the job that failed to start", extra=fields(job=job))

    def _reuse(self, snap: Any, host: str) -> dict[str, Any] | None:
        """The current result when nothing that decides the mesh has changed.

        **A mesh costs minutes**, so a second generate with the same inputs returns what is
        already there
        rather than starting an identical job. `force=True` is how a user says "run it again
        anyway".
        """
        project = self._session.require_project()
        # **This mesher's kept result**, not the one in use: with one result per mesher (ADR-0034),
        # going back to a mesher whose mesh is still current takes that mesh instead of re-meshing.
        result = self._result_of(project, str(snap.mesher))
        if result is None or result.params.get("state") != "done":
            return None
        basis = str(result.params.get("basis") or "")
        if not basis or str(result.params.get("host") or "") != host:
            return None
        # The basis depends on the engine string, which the result recorded when it was made.
        engine = str(result.meta.get("engine") or "")
        if not engine:
            return None
        # **The RESULT's own driver**, not the settings' current one: a result made by blockMesh
        # before the user switched to snappy must be compared against blockMesh's basis, or the
        # recomputed value could never match and every generate would re-run a mesh that is
        # already there.
        driver = driver_for(str(result.params.get("mesher") or snap.mesher))
        if mesh_basis(snap, driver.DRIVER_VERSION, engine) != basis:
            return None
        self._make_active(project, result)
        with self._lock:
            self._result_id = str(result.id)
            self._warnings = tuple(result.meta.get("warnings") or ())
        info = self._info()
        info["reused"] = True
        return info

    def _no_host(
        self,
        project: Project,
        snap: Any,
        host: str,
        why: str,
        result_id: str | None,
    ) -> dict[str, Any]:
        """Write a `skipped` result and **return** rather than raising."""
        result = self._replace(project, snap, host, "", "", result_id)
        self._reset(result, snap, "", "")
        with self._lock:
            self._job = None
        self._finish_with(project, result, "skipped", error=("mesh.no_host", why))
        return self._info()

    def _replace(
        self,
        project: Project,
        snap: Any,
        host: str,
        basis: str,
        engine: str,
        result_id: str | None,
    ) -> Node:
        """Swap in a fresh result node for this mesher, **the one in use from now on**.

        **One result per mesher** (ADR-0034, amending ADR-0024 §4.3): the previous result of the
        SAME mesher goes, and the other meshers' results stay, so the user can go back to them
        (`mesh.use`). Built with `project.add`/`remove` rather than undo commands: a result is not
        something a user did, and `history.undo` must not be able to bring back a mesh that the
        job's output no longer matches.
        """
        from stokes.model.ids import ID_RE, new_id, stable_id

        if result_id is not None and not ID_RE.match(result_id):
            raise MeshInvalid(f"{result_id!r} is not a node id")
        group = project.find(RESULTS_PATH)
        if group is None:
            group = project.add("/mesh", "group", "results", node_id=stable_id("g", RESULTS_PATH))
            self._emit("node.added", node_id=group.id, path=RESULTS_PATH)
        wanted = str(snap.mesher)
        self._drop_result(project, self._result_of(project, wanted))
        self._end_orphans(project)
        name = wanted
        taken = {str(child.name) for child in group.children}
        if name in taken:
            name = f"{wanted}-{new_id('n')[-4:]}"
        result = project.add(
            RESULTS_PATH,
            "mesh_result",
            name,
            {
                "mesher": wanted,
                "host": host,
                "basis": basis,
                "state": "running",
                "job": "",
                "cells": self._cells(snap),
            },
            node_id=result_id or new_id("n"),
        )
        # **`engine`, `warnings`, `patch_tags` and `derives_from` live in `node.meta`.** A
        # `mesh_result`
        # has no parameters for them, and inventing parameters would mean a schema change for
        # something
        # only this service and its readers use. `meta` is free-form and persisted, and it is
        # already how
        # an operation keeps its own run record.
        derives = [str(snap.settings_id)]
        if snap.domain_part:
            derives.append(str(snap.domain_part))
        derives.extend(str(item) for item in snap.side_tags.values() if item)
        result.meta["engine"] = engine
        # **The six basis parts the mesh was made from** (brief M-501): the stepper names the task
        # whose inputs changed by diffing THIS against the current parts, and a result made before
        # the parts existed is told apart from a part-diff by their absence.
        result.meta["basis_parts"] = dict(
            mesh_basis_parts(snap, driver_for(str(snap.mesher)).DRIVER_VERSION, engine)
        )
        result.meta["derives_from"] = derives
        result.meta["warnings"] = [
            f"tag {tag} lies inside the domain; blockMesh cannot mesh it" for tag in snap.unmapped
        ]
        # **The patch names, tag ids and types the driver will write** (T-207 amendment 3):
        # T-210 serves
        # them to the viewport and T-214 reads them for the solve snapshot.
        # **Keyed by patch name**, as the schema documents (`mesh-schema.md`): `side_tags` is keyed
        # by SIDE, and the viewport and the solve snapshot look a patch up by its name, which is
        # the name of the tag that holds the side (`snap.sides`).
        result.meta["patch_tags"] = {
            str(snap.sides[side]): str(tag_id)
            for side, tag_id in sorted(snap.side_tags.items())
            if tag_id and side in snap.sides
        }
        # **Every patch the driver writes, `unassigned` included** (FIXES-B B4): the same function
        # the dictionary comes from, so this list and the boundary file cannot disagree. The driver
        # is resolved here because this method is also reached from the no-host path, which has no
        # driver of its own.
        patch_types = driver_for(str(snap.mesher)).patch_types(snap)
        result.meta["patch_types"] = dict(sorted(patch_types.items()))
        # **What 2D this mesh was built as** (ADR-0031), so Results can show the plane it was
        # meshed in even after the settings change. Absent for a 3D mesh.
        two_d = dict(getattr(snap, "two_d", {}) or {})
        if two_d:
            result.meta["two_d"] = two_d
        with self._lock:
            self._result_id = str(result.id)
        self._emit("node.added", node_id=result.id, path=result.path)
        self._make_active(project, result)
        return result

    def _drop_result(self, project: Project, previous: Node | None) -> None:
        """Remove a result node and its cached surfaces (None: nothing to remove)."""
        if previous is None:
            return
        old_id, old_path = previous.id, previous.path
        project.remove(old_id)
        # **Its surfaces go with it**: a cache file of a result that no longer exists would be
        # served for nothing and never cleaned up.
        self._remove_surfaces(str(old_id))
        self._emit("node.removed", node_id=old_id, path=old_path)

    def _end_orphans(self, project: Project) -> None:
        """Mark `cancelled` any kept result still saying it is being made.

        A new mesh stops the job in flight (`cancel`, then `_job` is forgotten), and with one result
        per mesher that job's result may be another mesher's, which stays: nothing would ever write
        its end, so it would read "Meshing" for good.
        """
        from stokes.mesh.model import result_nodes

        for child in result_nodes(project.find(RESULTS_PATH)):
            if child.params.get("state") in ("queued", "running", "cancelling"):
                project.set_param(child.id, "state", "cancelled")
                child.status = "failed"  # ADR-0024 4.2: a cancelled result reads as failed
                self._emit("node.changed", node_id=child.id, path=child.path, param="state")

    def _case_for(self, snap: Any, engine: str) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
        """The driver's basis, case files and job steps, for the mesher the snapshot names.

        **The driver is chosen by the SETTING** (the owner's 1045 decision). This used to import
        the blockMesh driver directly and unconditionally, so `mesher: snappy` was a value the tree
        showed, the result node recorded, and the backend ignored: the demo kept meshing a bounding
        box while everything said snappyHexMesh.

        The snapshot comes from `stokes.mesh.snapshot.snapshot_for`, which returns the record the
        configured mesher needs, so a driver never meets a record it cannot read.
        """
        driver = driver_for(str(snap.mesher))
        basis = mesh_basis(snap, driver.DRIVER_VERSION, engine)
        files = driver.write(snap)
        steps = driver.steps(snap)
        return basis, files, steps

    def _cells(self, snap: Any) -> int:
        """The cell count the chosen driver's background block will produce.

        **The driver's own count, not a local `nx * ny * nz`.** For blockMesh the two are equal; for
        snappyHexMesh the background block is only the starting point, and keeping the arithmetic in
        the driver is what stops the two definitions drifting.
        """
        return int(driver_for(str(snap.mesher)).cell_count(snap))

    def _new_job_event(self) -> threading.Event:
        """A fresh `_finished` for a new job. **Called before anything can wait on it.**

        The old event is *replaced*, not cleared: a `wait()` already blocked on the previous
        job's event
        keeps waiting for that job, and a `wait()` that arrives now waits on the new one. A single
        cleared-and-reused flag could not tell those two apart - which was the bug.
        """
        with self._lock:
            self._finished = threading.Event()
            return self._finished

    def _reset(self, result: Node, snap: Any, engine: str, basis: str) -> None:
        """Forget the previous run's state."""
        with self._lock:
            self._step = None
            self._total = 0
            self._done_steps = 0
            self._engine = engine
            self._warnings = tuple(
                f"tag {tag} lies inside the domain; blockMesh cannot mesh it"
                for tag in snap.unmapped
            )
            # `_finished` is REPLACED by `_new_job_event`, not cleared here: clearing a shared event
            # would also release a `wait()` that belongs to the job being replaced.
            self._cancel.clear()
            self._result_id = str(result.id)
            # **The fidelity inputs, kept for the fetch that runs after the job finishes.** They
            # come from the snapshot, which is not held anywhere else by the time the mesh is done:
            # `generate` builds it, uses it, and lets it go. Storing the three small things the
            # measurement needs is cheaper than keeping a whole snapshot alive per run.
            self._fidelity_case = _FidelityCase.of(snap)

    def _subscribe(self, runner: Any) -> None:
        """Listen to the runner's events, once."""
        with self._lock:
            if self._unsubscribe is not None:
                return
            self._unsubscribe = runner.subscribe(self._on_runner_event)

    # ------------------------------------------------------------------ the listener

    def _on_runner_event(  # noqa: PLR0911 - one return per event kind, so the order is readable
        self, name: str, data: dict[str, Any]
    ) -> None:
        """**Runs on the runner's event thread and must return at once.**

        It maps an event to a service event and hands the real work to a worker thread. In
        particular it
        never calls `runner.call`: that blocks the loop this is running on, and `LocalRunner.call`
        refuses it rather than hanging.
        """
        with self._lock:
            job = self._job
            result_id = self._result_id
        if job is None or result_id is None:
            return
        if str(data.get("job", "")) != job:
            return
        project = self._session.project
        if project is None:
            return
        result = project.find(result_id)
        if result is None:
            return
        path = result.path
        if name == "log.lines":
            step = str(data.get("step", ""))
            self._emit_throttled(
                "mesh.log", node_id=result_id, step=step, lines=data.get("lines") or []
            )
            return
        if name != "job.state":
            return
        state = str(data.get("state", ""))
        named_step = data.get("step")
        if named_step:
            with self._lock:
                self._step = str(named_step)
        if state not in _FINAL:
            # The stage is what a UI shows while a job stops; `cancelling` is the job's own word
            # for it.
            stage = "cancelling" if state == "cancelling" else state
            index = int(data.get("index") or 0)
            with self._lock:
                self._done_steps = max(self._done_steps, index)
                done, count = self._done_steps, self._total
            self._emit_throttled(
                "mesh.progress",
                node_id=result_id,
                path=path,
                step=str(named_step or ""),
                stage=stage,
                fraction=(done / count) if count else 0.0,
                message="",
            )
            return
        # **A final state with a STEP NAME is that step ending, not the job.** `_set_step` emits
        # `job.state` with `state: "done"` when blockMesh finishes, and the job's own final event is
        # the one carrying `step: null`. Without this check the first step ended the wait: measured,
        # on the duct, `mesh.generate(wait=True)` returned while checkMesh was still running and the
        # result had no quality at all.
        if named_step is not None:
            self._emit_throttled(
                "mesh.progress",
                node_id=result_id,
                path=path,
                step=str(named_step),
                stage="done" if state == "done" else state,
                fraction=1.0,
                message="",
            )
            return
        # The job itself has ended: read its status off the loop thread, then change the tree.
        threading.Thread(
            target=self._finish_from_job,
            args=(job, result_id, state),
            name="stokes-mesh-finish",
            daemon=True,
        ).start()

    def _finish_from_job(self, job: str, result_id: str, state: str) -> None:
        """Read `job.status` (blocking, so not on the loop) and finish on the session thread.

        **No `path` parameter**, for the same reason `_finish` has none: the value was carried
        through two frames and used in neither, because `_finish_with` takes the path from the node
        it looks up.
        """
        from stokes.geometry.store import GeometryStore
        from stokes.run.hosts import HostRegistry

        job_status: dict[str, Any] = {}
        # **The job that sent the event, not whatever `_job` is by now** (FIXES-B B8) - and **on the
        # host that job runs on**, which is the half that was missing.
        try:
            runner = HostRegistry.of(self._session).runner(self._host_of(result_id))
            job_status = runner.call("job.status", job=job, timeout=30.0)
        except Exception:
            log.warning("could not read the job status", extra=fields(job=job))
        error = job_status.get("error") or None
        # **The job's own duration, in seconds**, from the record the agent wrote: its first step's
        # `started` (or `created`) to its `ended` (FIXES-B B2).
        seconds = _job_seconds(job_status)
        project = self._session.project
        if project is None:
            return
        result = project.find(result_id)
        if result is None:
            return
        # **The step's report comes back through the job** (`state.json`'s `reports`), which is why
        # the quality is written here and not by parsing a log on this side: the agent parses
        # (ADR-0005).
        report = _report_of(job_status)
        # **The layer table is its OWN report, read beside the quality one** (brief M-504):
        # `_report_of` returns the checkMesh report only, and that contract stays; the snappy
        # step's coverage table comes from `reports["snappyHexMesh"]` through the sibling
        # accessor.
        layers = _layer_report_of(job_status)
        # **The surfaces are fetched here, on this worker thread, before the tree changes**
        # (T-210): the fetch blocks on the agent, and the session thread must not wait on it. A job
        # that did not end `done` has no mesh to draw.
        surfaces: _Surfaces | None = None
        fidelity: dict[str, Any] | None = None
        if state == "done" and not error:
            surfaces = self._fetch_surfaces(job, result_id)
            # **The fidelity measurement, on this worker thread too.** It blocks on the agent the
            # same way the surface fetch does, and the session thread must not wait on either. It
            # cannot fail the mesh: a mesh that exists and is measured is a success even when the
            # comparison with its CAD could not be made.
            fidelity = self._fetch_fidelity(job, result_id)
        GeometryStore.of(self._session).on_session_thread(
            lambda: self._finish(
                project,
                job,
                result_id,
                state,
                report,
                error,
                seconds,
                surfaces=surfaces,
                fidelity=fidelity,
                layers=layers,
            ),
            self._cancel,
        )

    def _finish(
        self,
        project: Project,
        job: str,
        result_id: str,
        state: str,
        report: dict[str, Any],
        error: tuple[str, str] | dict[str, Any] | None,
        seconds: float,
        surfaces: _Surfaces | None = None,
        fidelity: dict[str, Any] | None = None,
        layers: dict[str, Any] | None = None,
    ) -> None:
        """Set the result's final state on the session thread and emit the matching event.

        **No `path` parameter.** It had one, unused, plus a `del path` to silence the linter that
        its absence caused — the same "assign then delete" shape as the `del ebytes` removed from
        `unpack_artifact`. `_finish_with` looks the node up and takes its path from the node, so the
        caller never needed to pass one.
        """
        with self._lock:
            # **Only the job this service is following may end the result** (FIXES-B B8): the final
            # event of a job that a newer generate replaced must not mark the new result.
            stale_job = self._job != job
        result = project.find(result_id)
        if stale_job or result is None:
            # Nobody will use what the fetch wrote for a result that was replaced meanwhile.
            if surfaces is not None and result is None:
                self._remove_surfaces(result_id)
            return
        code, message = _error_of(error)
        if state == "done":
            final, code, message = "done", "", ""
        elif state == "cancelled":
            final = "cancelled"
        elif state == "skipped":
            final = "skipped"
        else:
            final = "failed"
        self._finish_with(
            project,
            result,
            final,
            error=(code, message) if code else None,
            report=report,
            seconds=seconds,
            surfaces=surfaces,
            fidelity=fidelity,
            layers=layers,
        )

    def _finish_with(
        self,
        project: Project,
        result: Node,
        state: str,
        error: tuple[str, str] | None = None,
        report: dict[str, Any] | None = None,
        seconds: float = 0.0,
        surfaces: _Surfaces | None = None,
        fidelity: dict[str, Any] | None = None,
        layers: dict[str, Any] | None = None,
    ) -> None:
        """The one place a result reaches a final state.

        **The quality is written from the step's report**, and `ok` is the report's verdict ANDed
        with the settings' thresholds — so a mesh can be geometrically fine and still fail because
        the user asked for a stricter tolerance than it meets. The snappy layer table rides the
        same path as its own report (`layers`), written onto the result beside the quality.
        """
        result_id, path = str(result.id), result.path
        project.set_param(result_id, "state", state)
        if seconds:
            project.set_param(result_id, "seconds", seconds)
        if error is not None:
            code, message = error
            project.set_param(result_id, "ok", False)
            project.set_param(result_id, "error", f"{code}: {message}")
        elif report:
            # **The report decides `ok`, and it must be written LAST.** My first version set `ok
            # = True`
            # for a `done` job and then wrote the quality's verdict, so every mesh came out `ok`
            # — the
            # node status was `warning` and the flag said fine. Measured: four cases in a row.
            self._write_quality(project, result, report, fidelity=fidelity)
            self._write_layers(result, layers)
        elif state == "done":
            # **No report at all is not a pass.** Nothing measured the mesh, so `ok` says so.
            project.set_param(result_id, "ok", False)
            self._write_layers(result, layers)
        self._emit("node.changed", node_id=result_id, path=path, param="state")
        extract_error = surfaces.error if surfaces is not None else None
        if state == "done" and surfaces is not None:
            self._apply_surfaces(project, result, surfaces)
        if extract_error is not None:
            # **The result stays `done`**: the mesh exists on the host and its quality is written;
            # only the picture of it could not be fetched (ADR-0024 5.3).
            code, message = extract_error
            self._emit("mesh.failed", node_id=result_id, path=path, code=code, message=message)
        elif state == "done":
            self._emit("mesh.done", node_id=result_id, path=path, seconds=seconds)
        elif state == "cancelled":
            self._emit("mesh.cancelled", node_id=result_id, path=path)
        elif state == "skipped":
            code, message = error or ("mesh.no_host", "")
            self._emit("mesh.skipped", node_id=result_id, path=path, code=code, message=message)
        else:
            code, message = error or ("mesh.error", "the mesh failed")
            self._emit("mesh.failed", node_id=result_id, path=path, code=code, message=message)
        # **Only touch the service state if this finish is still the current job's.** A finish for a
        # result a new generate has already replaced must not clear the running job's id -
        # that would
        # make `cancel()` return False for the job that IS running.
        # **The quality distributions are a FOLLOW-UP task, not a finish step** (brief M-506,
        # R14c): the result is `done` and fresh NOW, and the fetch runs after this method
        # returns, on the same worker thread, with its own timeout — the fidelity numbers are
        # already written, so a slow or failed distribution cannot starve them (the [F] flag's
        # ordering rule). **Every mesher's case writes an ASCII polyMesh** the reader can read,
        # so the marker is not snappy-only.
        if state == "done" and not error:
            result.meta["quality_dist"] = {"pending": True}
        with self._lock:
            current, finished = self._job, self._finished
            if current is None or current == str(result.params.get("job") or ""):
                self._job = None
                self._step = None
        if finished is not None:
            finished.set()
        if state == "done" and not error:
            self._fetch_quality(str(result.params.get("job") or ""), result_id)

    def _fetch_quality(self, job: str, result_id: str) -> None:
        """Fetch the quality distributions and write `meta.quality_dist` (brief M-506, R14c).

        **Runs after `_finish_with` returns**, on the same worker thread: the result is already
        `done` and fresh, so this cannot hold the node in `running` — the review's R14c. The
        reply is capped by the schema (64 KiB; histograms 40 bins), and the faces artifact, when
        the reply names one, is fetched with the existing transfer beside `.stkmsh`. **Never
        raises, and never fails the mesh**: a distribution that could not be computed leaves
        `{"error": ...}` in the meta, and the Quality page shows checkMesh's numbers with the
        reason.
        """
        from stokes.run.hosts import HostRegistry

        project = self._session.project
        result = project.find(result_id) if project is not None else None
        if result is None or str(result.params.get("state") or "") != "done":
            return  # replaced meanwhile: nobody will read the distribution
        try:
            runner = HostRegistry.of(self._session).runner(self._host_of(result_id))
            reply = runner.call("extract.quality", job=job, timeout=QUALITY_TIMEOUT_S)
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            # **A missing method is "not available yet", not an error** (the second review's
            # R9): until the agent ships `extract.quality`, every finished mesh would otherwise
            # carry an error in its meta — a failure nobody asked for and a warning the page
            # would show forever. The pending marker stays, and the page keeps showing
            # checkMesh's numbers.
            # **The agent's own wording is `unknown method`** (`AgentServer._dispatch`), not the
            # `unknown command` this first matched. Measured on a real WSL mesh: the method is
            # registered nowhere, the phrase missed, and every finished mesh got a warning and an
            # error in `quality_dist` instead of the intended "not available yet".
            lowered = message.lower()
            if (
                "unknown method" in lowered
                or "unknown command" in lowered
                or "no method" in lowered
            ):
                return
            log.warning(
                "the quality distributions could not be fetched",
                extra=fields(job=job, error=message[:200]),
            )
            result = project.find(result_id) if project is not None else None
            if result is not None:
                result.meta["quality_dist"] = {"error": message}
                self._emit_dist_changed(result_id, result.path)
            return
        # **Validated before it is stored** (the second review's R9): the reply is the agent's
        # answer about a mesh, not a trusted document — size-capped (the project's own load
        # limit is 64 MiB; the distributions' budget is 1 MiB), shape-checked, and every number
        # finite, because a NaN reaching the meta reaches `json.dumps` as `NaN`.
        checked = _check_dist_reply(reply)
        result = project.find(result_id) if project is not None else None
        if result is None:
            return
        result.meta["quality_dist"] = checked
        self._emit_dist_changed(result_id, result.path)

    def _emit_dist_changed(self, result_id: str, path: str) -> None:
        """The second `node.changed` (brief M-506, R14c): the distributions arrived after the
        result was finished, and the Quality page reads them on this event."""
        self._emit("node.changed", node_id=result_id, path=path, param="quality_dist")

    def _write_quality(
        self,
        project: Project,
        result: Node,
        report: dict[str, Any],
        *,
        fidelity: dict[str, Any] | None = None,
    ) -> None:
        """Write the checkMesh numbers onto the result and decide `ok` and the node status.

        **`ok` is not the mesh's own verdict alone.** The report says whether OpenFOAM's checks
        passed; the settings say how strict this project is. A mesh can satisfy OpenFOAM and fail a
        user's `max_non_ortho`, and that difference is the whole point of the thresholds.

        A number the log did not contain stays `None` and makes `ok` false — **never a default**,
        because a mesh reported fine on the strength of a figure nobody read is worse than one
        reported unknown.
        """
        settings = project.find(SETTINGS_PATH)
        thresholds = {
            "max_non_ortho": float(settings.params["max_non_ortho"]) if settings else 70.0,
            "max_skewness": float(settings.params["max_skewness"]) if settings else 4.0,
        }
        inner = dict((report.get("error") and {}) or report)
        # `patches` is a table, not a quality number, and `quality` is capped at 64 KiB by the
        # schema.
        quality = {key: value for key, value in inner.items() if key != "patches"}
        quality["thresholds"] = thresholds
        # **The fidelity block rides with the quality numbers, in the same record** (Claude's 1115
        # point 2): it is part of the mesh result, not a separate artefact, so the card reads one
        # object. Absent when it could not be measured, and absent is what the card shows as
        # nothing - never as a pass.
        if fidelity is not None:
            quality["fidelity"] = fidelity
        for name in ("cells", "points", "faces"):
            if report.get(name) is not None:
                project.set_param(result.id, name, int(report[name]))
        project.set_param(result.id, "quality", quality)
        ok = _quality_ok(report, thresholds)
        project.set_param(result.id, "ok", ok)
        if not ok and result.status == "ok":
            # **`warning`, not `failed`**: the run worked and the mesh is real; it is the
            # quality that
            # does not meet the bar, and a UI shows those differently.
            result.status = "warning"

    def _write_layers(self, result: Node, layer_report: dict[str, Any] | None) -> None:
        """Store the snappy layer table on the result and warn where a wall fell short (M-504).

        The table is the parser's `layers` list - per wall, its faces, the mean layer count, the
        mean thickness and the coverage as a percentage of the wanted thickness. **Stored as
        `meta.layers`**, beside `patch_tags` and the other per-mesh records, because it is about
        THIS mesh and not a quality threshold.

        **A wall under 80 % adds a warning, and the mesh stays what it is**: layers that
        collapsed are a real answer about the geometry (sharp edges, a wall meeting another
        surface), not a failed mesh - the coverage card and the warning say it, and the user
        decides whether to change the counts. The warning names the wall; one each, so a
        project with two short walls gets both named.
        """
        if not layer_report:
            return
        table = layer_report.get("layers")
        if not isinstance(table, list) or not table:
            return
        result.meta["layers"] = table
        warnings = list(result.meta.get("warnings") or ())
        named = False
        for row in table:
            if not isinstance(row, dict):
                continue
            coverage = row.get("coverage_percent")
            patch = str(row.get("patch", ""))
            if not isinstance(coverage, (int, float)) or isinstance(coverage, bool):
                continue
            if float(coverage) >= LAYER_COVERAGE_WARN_PCT:
                continue
            shown = "unassigned" if patch == "unassigned" else patch
            warnings.append(f"walls: layers on {float(coverage):.0f} % of the faces of {shown}")
            named = True
        if named:
            result.meta["warnings"] = warnings

    def _fail(self, project: Project, result: Node, code: str, message: str) -> None:
        self._finish_with(project, result, "failed", error=(code, message))

    # ------------------------------------------------------------------ surfaces, T-210

    def _fetch_surfaces(self, job: str, result_id: str, host: str | None = None) -> _Surfaces:
        """Ask the agent for the job's surfaces and keep them in the cache. **A worker thread.**

        Never raises: a failure is an answer (`_Surfaces.error`), because the mesh itself is fine
        and the result must still reach `done` with its quality. ``host`` is the job's host when the
        result node does not exist yet (`adopt_job_mesh`); otherwise the node says.
        """
        from stokes.geometry.errors import MeshFormatError
        from stokes.run.artifacts import fetch_artifact
        from stokes.run.hosts import HostRegistry

        target: Path | None = None
        try:
            runner = HostRegistry.of(self._session).runner(host or self._host_of(result_id))
            summary = runner.call("extract.surfaces", job=job, timeout=EXTRACT_TIMEOUT_S)
            target = self._surface_path(result_id)
            fetch_artifact(
                runner,
                job,
                str(summary["artifact"]),
                target,
                str(summary["sha256"]),
                int(summary["size"]),
            )
            data, header = self._load(result_id)
            return _Surfaces(
                patches=tuple(header["patches"]), flatness=_symmetry_flatness(data, header)
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "")
            message = str(exc) or type(exc).__name__
            log.warning(
                "the surfaces could not be fetched", extra=fields(job=job, error=message[:200])
            )
            if target is not None or isinstance(exc, MeshFormatError):
                self._remove_surfaces(result_id)
            return _Surfaces(
                error=("mesh.extract", message),
                reason="no_vtk" if code == "agent.no_vtk" else "",
            )

    def _fetch_fidelity(self, job: str, result_id: str) -> dict[str, Any] | None:
        """Ask the agent how closely the mesh followed its CAD. **A worker thread.**

        **Never raises, and never fails the mesh.** The mesh exists and its quality is measured; the
        fidelity is a report ABOUT it, so a measurement that could not be made leaves the block
        absent - which the card shows as nothing at all, rather than as a pass.

        Returns the measurement's JSON, or None when it could not be taken. The reasons it can be
        absent are ordinary: a mesher other than snappy stages no surfaces to measure against, and a
        project meshed from a box has no CAD at all.
        """
        from stokes.mesh.drivers.snappy import GEOMETRY_DIR
        from stokes.run.hosts import HostRegistry

        del GEOMETRY_DIR
        case = self._fidelity_case
        if case is None or not case.surfaces:
            # **Nothing to measure against, which is not an error.** A blockMesh case stages no
            # surfaces and a box domain has no CAD, so the block simply does not appear rather than
            # appearing as a failure.
            return None
        try:
            runner = HostRegistry.of(self._session).runner(self._host_of(result_id))
            measured = runner.call(
                "mesh.fidelity",
                job=job,
                characteristic_length=case.characteristic_length,
                cad_surfaces=list(case.surfaces),
                cell_size=case.cell_size,
                timeout=FIDELITY_TIMEOUT_S,
            )
            return dict(measured) if isinstance(measured, dict) else None
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            log.warning(
                "the mesh fidelity could not be measured",
                extra=fields(job=job, error=message[:200]),
            )
            return None

    def adopt_job_mesh(self, job: str, host: str, name: str, mesher: str, case_id: str) -> None:
        """Make a finished job's own mesh the project's mesh result. **A worker thread.**

        **For an imported OpenFOAM case** (ADR-0029): its recipe meshes inside the same job that
        solves, so no `mesh.generate` ever made a result for it, and the viewport, which draws the
        `mesh_patch` nodes of the current result, had nothing to show and nothing to colour.
        This fetches the job's surfaces here, then on the session thread replaces the current
        result with one for this job (state `done`, `meta.case` the case node) and adds its
        patches, exactly as a generated mesh's are added. Not an undo step, like any mesh result.
        A failed fetch adds a result with the reason in `error`, so the tree says why nothing is
        drawn.
        """
        from stokes.geometry.store import GeometryStore
        from stokes.model.ids import new_id, stable_id

        result_id = new_id("n")
        surfaces = self._fetch_surfaces(job, result_id, host=host)

        def apply() -> None:
            project = self._session.project
            if project is None:
                self._remove_surfaces(result_id)
                return
            group = project.find(RESULTS_PATH)
            if group is None:
                group = project.add(
                    "/mesh", "group", "results", node_id=stable_id("g", RESULTS_PATH)
                )
                self._emit("node.added", node_id=group.id, path=RESULTS_PATH)
            kind = mesher if mesher in MESHERS else "blockmesh"
            # One result per mesher (ADR-0034): the case's earlier mesh of this kind goes.
            self._drop_result(project, self._result_of(project, kind))
            taken = {str(child.name) for child in group.children}
            chosen = name if name not in taken else f"{name}-{result_id[-4:]}"
            result = project.add(
                RESULTS_PATH,
                "mesh_result",
                chosen,
                {
                    "mesher": kind,
                    "host": host,
                    "state": "done",
                    "job": job,
                },
                node_id=result_id,
            )
            result.meta["case"] = case_id
            self._emit("node.added", node_id=result.id, path=result.path)
            self._make_active(project, result)
            self._apply_surfaces(project, result, surfaces)

        GeometryStore.of(self._session).on_session_thread(apply, self._cancel)

    def _apply_surfaces(self, project: Project, result: Node, surfaces: _Surfaces) -> None:
        """On the session thread: one `mesh_patch` per patch, or the extraction's error.

        Not undo steps, like the result itself. Emits `node.added` per patch; the caller emits
        `mesh.done` (or `mesh.failed` for an error) afterwards.
        """
        result_id = str(result.id)
        result.meta["surfaces"] = surfaces.reason or ("failed" if surfaces.error else "ok")
        if surfaces.error is not None:
            code, message = surfaces.error
            project.set_param(result_id, "error", f"{code}: {message}")
            return
        from stokes.model.schema import NAME_RE

        self._write_flatness(project, result, surfaces.flatness)
        tags = dict(result.meta.get("patch_tags") or {})
        types = dict(result.meta.get("patch_types") or {})
        taken: set[str] = set()
        for position, patch in enumerate(surfaces.patches):
            real = str(patch["name"])
            name = real if NAME_RE.match(real) and real not in taken else f"patch_{position}"
            taken.add(name)
            tag_id = str(tags.get(real) or "")
            kind = str(patch.get("patch_type") or "patch")
            if kind == "patch":
                kind = str(types.get(real) or kind)
            node = project.add(
                result.path,
                "mesh_patch",
                name,
                {
                    "index": position,
                    "patch_type": kind,
                    "faces": int(patch["faces"]),
                    "triangles": int(patch["triangles"]),
                    "tag_id": tag_id,
                    "color": self._tag_color(project, tag_id),
                },
            )
            self._emit("node.added", node_id=node.id, path=node.path)

    def _write_flatness(
        self, project: Project, result: Node, flatness: tuple[dict[str, Any], ...]
    ) -> None:
        """Store the symmetry planes' flatness and warn where one came out bent.

        **A bent plane makes the mesh `needs a look`** (`ok` false, status `warning`): on the mixing
        elbow it was the one defect behind two diverged runs and `checkMesh` passed the tet mesh
        regardless. It is not a failed mesh - the hex-dominant 2.5 mm mesh was 1.4 mm off and still
        converged - so the warning says a run CAN diverge, and the user decides.
        """
        from stokes.mesh.flatness import Flatness, warning

        if not flatness:
            return
        result.meta["symmetry_flatness"] = list(flatness)
        case = self._fidelity_case
        cell_size = case.cell_size if case is not None else None
        warnings = list(result.meta.get("warnings") or ())
        bent = False
        for record in flatness:
            sentence = warning(Flatness(**record), cell_size)
            if sentence is not None:
                warnings.append(sentence)
                bent = True
        if not bent:
            return
        result.meta["warnings"] = warnings
        with self._lock:
            if self._result_id == str(result.id):
                self._warnings = tuple(warnings)
        project.set_param(result.id, "ok", False)
        if result.status == "ok":
            result.status = "warning"

    def _tag_color(self, project: Project, tag_id: str) -> str:
        """The colour of the tag a patch came from, or the default when the tag is gone."""
        tag = project.find(tag_id) if tag_id else None
        if tag is not None and tag.type == "tag":
            return str(tag.params.get("color") or DEFAULT_PATCH_COLOR)
        return DEFAULT_PATCH_COLOR

    def _cache_base(self) -> Path:
        """The project's cache folder, or the session's scratch folder while it has none."""
        from stokes.geometry.shapes import cache_dir

        folder = self._session.project_path
        if folder is not None:
            return cache_dir(folder)
        from stokes.geometry.store import GeometryStore

        scratch = GeometryStore.of(self._session).shape_dir()
        with self._lock:
            self._scratch = scratch
        return scratch

    def _surface_path(self, result_id: str, base: Path | None = None) -> Path:
        """`<cache>/mesh/<result id>.stkmsh`, refused unless it really is inside `<cache>/mesh`.

        The id becomes part of a path, so it must be a node id; and neither the folder nor the
        file may be a link, because a link would take a read or a write somewhere else.
        """
        from stokes.model.ids import ID_RE

        if not ID_RE.match(result_id):
            raise MeshInvalid(f"{result_id!r} is not a node id")
        folder = (base if base is not None else self._cache_base()) / SURFACE_FOLDER
        path = folder / f"{result_id}{SURFACE_SUFFIX}"
        if folder.is_symlink() or path.is_symlink() or path.resolve().parent != folder.resolve():
            raise MeshInvalid(f"the surface file of {result_id} is not inside the mesh cache")
        return path

    def _remove_surfaces(self, result_id: str) -> None:
        """Delete a result's cache file (and a part left by a failed fetch). Never raises."""
        try:
            path = self._surface_path(result_id)
        except MeshInvalid:
            return
        with self._lock:
            self._surfaces.pop(str(path), None)
        for item in (path, path.with_name(path.name + ".part")):
            with contextlib.suppress(OSError):
                item.unlink()

    def _load(self, result_id: str) -> tuple[memoryview, dict[str, Any]]:
        """A result's surface file as a `memoryview` and its validated header.

        At most `SURFACE_LRU` files are held. An entry is trusted only while the file's size and
        modification time are what they were when it was read, so a file replaced under the same
        name is read again. Raises `FileNotFoundError` for a missing file and `MeshFormatError` for
        a malformed one.
        """
        from stokes.mesh.surfaces import read_header

        path = self._surface_path(result_id)
        info = path.stat()
        stamp = (info.st_size, info.st_mtime_ns)
        key = str(path)
        with self._lock:
            found = self._surfaces.get(key)
            if found is not None and found[0] == stamp:
                self._surfaces.move_to_end(key)
                return found[1], found[2]
        view = memoryview(path.read_bytes())
        header = read_header(view)
        with self._lock:
            self._surfaces[key] = (stamp, view, header)
            self._surfaces.move_to_end(key)
            while len(self._surfaces) > SURFACE_LRU:
                self._surfaces.popitem(last=False)
        return view, header

    def _patch_source(self, node_id: str) -> tuple[Node, memoryview, dict[str, Any]] | None:
        """The `mesh_patch` node, its result's surface bytes and header; None when unavailable."""
        project = self._session.project
        node = project.find(node_id) if project is not None else None
        if node is None or node.id != node_id or node.type != "mesh_patch" or node.parent is None:
            return None
        try:
            data, header = self._load(str(node.parent.id))
        except (OSError, MeshInvalid):
            return None
        except Exception:
            log.warning("a surface file is unreadable", extra=fields(node_id=node_id))
            return None
        return node, data, header

    def patch_transport(self, node_id: str) -> bytes | None:
        """One patch in transport version 2, or None when the node or its surfaces are unknown."""
        from stokes.mesh.surfaces import patch_transport

        source = self._patch_source(node_id)
        if source is None:
            return None
        node, data, header = source
        text = str(node.params.get("color") or DEFAULT_PATCH_COLOR)
        try:
            color = (int(text[1:3], 16) / 255, int(text[3:5], 16) / 255, int(text[5:7], 16) / 255)
            return patch_transport(data, header, int(node.params["index"]), color)
        except Exception:
            log.warning("a patch could not be packed", extra=fields(node_id=node_id))
            return None

    def patch_edges(self, node_id: str) -> bytes | None:
        """One patch's cell edges in the edge codec, or None when unknown or missing."""
        from stokes.mesh.surfaces import patch_edges

        source = self._patch_source(node_id)
        if source is None:
            return None
        node, data, header = source
        try:
            return patch_edges(data, header, int(node.params["index"]))
        except Exception:
            log.warning("the edges of a patch could not be packed", extra=fields(node_id=node_id))
            return None

    def _surfaces_state(self, result: Node | None) -> str:
        """`ok | missing | no_vtk | pending` for `mesh.status` (ADR-0024 4.7)."""
        if result is None:
            return "missing"
        if result.params.get("state") in ("queued", "running", "cancelling"):
            return "pending"
        if result.params.get("state") != "done":
            return "missing"
        try:
            if self._surface_path(str(result.id)).is_file():
                return "ok"
        except MeshInvalid:
            pass
        return "no_vtk" if result.meta.get("surfaces") == "no_vtk" else "missing"

    def project_opened(self, project: Project, path: Path | None) -> None:
        """Session hook: another project is the session's now. Nothing is fetched on a reopen: the
        routes read the cache file, and `mesh.status` says `missing` when there is none."""
        del project, path  # the cache is found through the session's current project
        with self._lock:
            self._surfaces.clear()
            self._scratch = None
            # **The prepared record belongs to the PREVIOUS project** (brief M-501): a revision
            # number alone cannot tell two projects apart, and reusing the old record would
            # answer `mesh.tasks` for a project that is no longer open.
            self._prepared = None
            self._prepared_revision = None

    def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
        """Session hook: Save as copies the surface files to the new cache (the first save too,
        out of the scratch folder). Synchronous: a route asked right after a save must find them."""
        del project
        from stokes.geometry.shapes import cache_dir
        from stokes.model.ids import ID_RE

        with self._lock:
            scratch = self._scratch
        old = cache_dir(previous) if previous is not None else scratch
        new = cache_dir(path)
        if old is None or old == new:
            return
        source, target = old / SURFACE_FOLDER, new / SURFACE_FOLDER
        if not source.is_dir():
            return
        for file in source.glob(f"*{SURFACE_SUFFIX}"):
            if not ID_RE.match(file.name[: -len(SURFACE_SUFFIX)]) or file.is_symlink():
                continue
            try:
                target.mkdir(parents=True, exist_ok=True)
                part = target / (file.name + ".part")
                shutil.copyfile(file, part)
                part.replace(target / file.name)
            except OSError:
                log.warning("a surface file was not copied", extra=fields(name=file.name))

    def project_copied(self, project: Project, path: Path, previous: Path | None = None) -> None:
        """Session hook: copy the surface files into a COPY, leaving the session in the original.

        **The same files a save copies**, because a reopened copy must find them; the session has
        not moved, which is why this is not `project_saved` (Opus F3). `previous` is the folder the
        copy came FROM - the session's own - so the copy reads from there."""
        self.project_saved(project, path, previous or self._session.project_path)

    # ------------------------------------------------------------------ control

    def cancel(self) -> bool:
        """Ask the running job to stop. Returns whether there was one.

        **Only the job id is read under the lock**, because that is all this needs: the result node
        is reached through the job's own `job.state` event, which `_on_runner_event` handles. An
        earlier version also read `self._result_id` here and then `del`-ed it — a local assigned and
        discarded to keep a linter quiet, which is the same shape as the `del path` removed from
        `_finish`.
        """
        from stokes.run.hosts import HostRegistry

        with self._lock:
            job = self._job
        if job is None:
            return False
        runner = HostRegistry.of(self._session).runner(self._host_of(self._result_id))
        try:
            runner.call("job.cancel", job=job, timeout=30.0)
        except Exception:
            log.warning("the cancel call failed", extra=fields(job=job))
        return True

    def wait(self, timeout: float | None) -> bool:
        """Wait for the job this service is running. **Never call this on the session thread.**

        **`_job is None` is NOT "finished".** That was the bug: between `cancel()` and `_reset`, and
        again while `job.create` was in flight, no job was current - and returning `True`
        there told a
        caller (and journal replay, through `settle`) that a mesh it had just asked for was done. A
        service with no job AND no event has nothing to wait for; a service with an event waits on
        **that job's** event, whatever `_job` currently says.
        """
        with self._lock:
            finished = self._finished
        if finished is None:
            return True
        return finished.wait(WAIT_SECONDS if timeout is None else timeout)

    def settle(self, timeout: float | None, *, coalesce: bool = False) -> None:
        """Session hook: journal replay waits here between records (ADR-0022 decision 10.1).

        **`coalesce` is accepted and ignored, and that difference from `opstore.settle` is
        deliberate.**

        `OperationStore.settle` forwards it because an operation may be waiting on a 300 ms
        debounce, so a burst of changes recorded inside one window must run the operation *once*;
        forcing every pending run there re-ran it once per record — measured at four runs for a
        burst that took two (T-092).

        **Meshing has no debounce and does not auto-run.** A change makes the result stale and
        starts nothing, so there is no pending run for `coalesce` to hold open and nothing for it to
        collapse. The parameter is in the signature because the session calls this hook with it.
        """
        del coalesce  # no pending run exists, by the design above
        if not self.wait(WAIT_SECONDS if timeout is None else timeout):
            raise TimeoutError("the mesh did not finish in time")

    def status(self) -> dict[str, Any]:
        """What the mesh is doing, for `mesh.status`."""
        project = self._session.project
        result = self._result_node(project) if project is not None else None
        with self._lock:
            job, step, done, total = self._job, self._step, self._done_steps, self._total
            warnings = list(self._warnings)
        return {
            "state": str(result.params.get("state")) if result is not None else "",
            "result": str(result.id) if result is not None else None,
            "path": result.path if result is not None else "",
            "job": job or (str(result.params.get("job")) if result is not None else ""),
            "step": step or "",
            "fraction": (done / total) if total else 0.0,
            "stale": result.status == "stale" if result is not None else False,
            # **Which tasks' inputs changed** (brief M-501): written by the stale pass from the
            # basis parts, so this answers without a snapshot build.
            "changed": list(result.meta.get("changed") or ()) if result is not None else [],
            "warnings": warnings,
            # **The quality, so a client needs one call to show it.** `cells` and `ok` are what
            # a user
            # looks for after pressing Mesh.
            "cells": int(result.params.get("cells") or 0) if result is not None else 0,
            "ok": bool(result.params.get("ok")) if result is not None else False,
            "nodes": self._node_counts(result),
            "surfaces": self._surfaces_state(result),
            "quality": dict(result.params.get("quality") or {}) if result is not None else {},
        }

    def _node_counts(self, result: Node | None) -> dict[str, int]:
        """The point and face counts, when checkMesh reported them."""
        if result is None:
            return {}
        return {
            name: int(result.params.get(name) or 0)
            for name in ("points", "faces")
            if result.params.get(name)
        }

    def log(self, lines: int) -> dict[str, Any]:
        """The tail of the running step's log, through the agent."""
        from stokes.run.hosts import HostRegistry

        with self._lock:
            job, step = self._job, self._step
        if job is None or not step:
            return {"step": "", "lines": [], "truncated": False}
        runner = HostRegistry.of(self._session).runner(self._host_of(self._result_id))
        try:
            reply = runner.call("log.tail", job=job, step=step, lines=lines, timeout=30.0)
        except Exception:
            return {"step": step, "lines": [], "truncated": False}
        return {
            "step": step,
            "lines": reply.get("lines") or [],
            "truncated": bool(reply.get("truncated")),
        }

    # ------------------------------------------------------------------ staleness

    def on_event(self, name: str, data: dict[str, Any]) -> None:
        """Session hook: a change may make the result stale. **Returns at once.**"""
        if self._closed:
            return
        if name not in (
            "node.changed",
            "node.added",
            "node.removed",
            "tag.changed",
            "history.changed",
            "geometry.part_ready",
        ):
            return
        # **Our own events are not a reason to recheck**, or writing the result would restart the
        # timer for ever. Both the node id and the path are checked: a result we replaced has a
        # different id, and anything under `/mesh/results` is ours whatever its id.
        node_id = str(data.get("node_id", ""))
        path = str(data.get("path", ""))
        if node_id and self._result_id and node_id == self._result_id:
            return
        if path.startswith(RESULTS_PATH):
            return
        self._arm_stale_timer()

    def _arm_stale_timer(self) -> None:
        """One timer per burst: a parameter typed into a box must not rebuild per keystroke."""
        with self._lock:
            if self._stale_timer is not None:
                self._stale_timer.cancel()
            timer = threading.Timer(STALE_DEBOUNCE_S, self._check_stale)
            timer.daemon = True
            self._stale_timer = timer
        timer.start()

    def _check_stale(self) -> None:
        """Rebuild the snapshot off the session thread and set `stale` on the result."""
        from stokes.geometry.store import GeometryStore

        try:
            GeometryStore.of(self._session).on_session_thread(self._refresh_stale, self._cancel)
        except Exception:
            log.warning("the staleness check failed")

    def _refresh_stale(self) -> None:
        """On the session thread: does the current snapshot still produce this result's basis?"""
        from stokes.mesh.precheck import prepared_record
        from stokes.mesh.snapshot import snapshot_for

        project = self._session.project
        if project is None:
            return
        result = self._result_node(project)
        if result is None or result.params.get("state") != "done":
            return
        with self._lock:
            engine = self._engine or str(result.meta.get("engine") or "")
        settings = project.find(SETTINGS_PATH)
        stale = True
        if settings is not None and engine:
            try:
                snap = snapshot_for(self._session, settings)
                # The RESULT's driver: this asks "has the mesh behind this result gone stale?",
                # so it must use the driver that MADE it, exactly as `_reuse` does.
                driver = driver_for(str(result.params.get("mesher") or snap.mesher))
                stale = mesh_basis(snap, driver.DRIVER_VERSION, engine) != str(
                    result.params.get("basis")
                )
                # **The read commands' cache is filled HERE** (brief M-501): this pass already
                # built the snapshot, so the basis parts and the dry-run precheck cost one walk
                # more, and `mesh.tasks`/`mesh.precheck` read them without a build of their own.
                record = prepared_record(self._session, settings, snap=snap, engine=engine)
            except Exception:
                log.warning("the staleness check failed", exc_info=True)
                stale = True
                # The build refused: the precheck's most interesting input. Asked again so the
                # refusal reaches the findings instead of only the log.
                record = prepared_record(self._session, settings, engine=engine)
            self._remember_prepared(project, record)
        # **The explicit branch, not SIM108's ternary**: the `else` carries several lines of
        # reasoning and a ternary would push the first onto an unreadable line.
        if stale:  # noqa: SIM108 - the else branch explains itself over several lines
            wanted = "stale"
        else:
            # **Re-judge the stored quality against the CURRENT limits, without a job** (brief
            # M-501). A limit decides whether a mesh passes, not what it is: blockMesh's basis
            # does not carry the limits, so this re-judge is the whole story there. snappy's
            # `snap` part carries them, so a changed limit arrives here as `stale` and the
            # re-judge is skipped — Generate is what meshes with the new limit.
            wanted = self._rejudge(project, result)
        # **`changed[]` for the stepper**: the tasks whose basis parts differ, written with the
        # result so `mesh.status` answers without a build. A result made before the parts existed
        # carries none, and `mesh.tasks` marks its volume task stale by that absence itself; a
        # snapshot that cannot be built names nothing — the findings say why.
        record = self._prepared or {}
        changed = changed_tasks(
            result.meta.get("basis_parts"),
            record.get("parts") if record.get("ok") else None,
        )
        if changed != list(result.meta.get("changed") or ()):
            result.meta["changed"] = changed
            self._emit("node.changed", node_id=result.id, path=result.path, param="changed")
        # **`node.status` is an attribute, not a parameter.** `set_param` refused it with
        # `UnknownParameter`, and my `except` in `_check_stale` swallowed that as "the check failed"
        # — so staleness silently never worked. Measured, twice.
        if result.status == wanted:
            return
        result.status = wanted
        self._emit("node.changed", node_id=result.id, path=result.path, param="status")

    def _remember_prepared(self, project: Project, record: dict[str, Any]) -> None:
        """Store the prepared record under the project's revision (the read commands' cache)."""
        with self._lock:
            self._prepared = record
            self._prepared_revision = int(project.revision)

    def prepared(self, settings: Any = None) -> dict[str, Any]:
        """The prepared basis parts and dry-run precheck for the current project revision.

        **Builds on a miss, on the session thread** — the commands run there, so the snapshot
        build is legal exactly as it is in the debounced stale pass that fills this cache after
        edits. `{}` only for a session with no project. `mesh.tasks` and `mesh.precheck` read this;
        neither builds a snapshot per call (the 200 ms budget, brief M-501).
        """
        project = self._session.project
        if project is None:
            return {}
        revision = int(project.revision)
        with self._lock:
            if self._prepared is not None and self._prepared_revision == revision:
                return self._prepared
        from stokes.mesh.precheck import prepared_record

        node = settings if settings is not None else project.find(SETTINGS_PATH)
        record = prepared_record(self._session, node, engine=self._current_engine())
        self._remember_prepared(project, record)
        return record

    def _current_engine(self) -> str:
        """The engine the mesh was or would be made by: the live one, else the result's record."""
        with self._lock:
            engine = self._engine
        if engine:
            return engine
        result = self._result_node(self._session.project)
        return str(result.meta.get("engine") or "") if result is not None else ""

    def _rejudge(self, project: Project, result: Node) -> str:
        """Re-read the stored quality against the CURRENT thresholds; answer the node's status.

        Writes nothing when the thresholds are the ones the quality already carries and `ok`
        already agrees — the common case, so an ordinary refresh rewrites nothing.
        """
        settings = project.find(SETTINGS_PATH)
        thresholds = {
            "max_non_ortho": float(settings.params["max_non_ortho"]) if settings else 70.0,
            "max_skewness": float(settings.params["max_skewness"]) if settings else 4.0,
        }
        quality = dict(result.params.get("quality") or {})
        ok = _quality_ok(quality, thresholds)
        if quality.get("thresholds") == thresholds and bool(result.params.get("ok")) == ok:
            return "ok" if ok else "warning"
        quality["thresholds"] = thresholds
        project.set_param(result.id, "quality", quality)
        project.set_param(result.id, "ok", ok)
        return "ok" if ok else "warning"

    def project_closed(self) -> None:
        """Session hook: **detach**, do not cancel.

        The job belongs to the agent, not to the document. On this machine the desktop *is* the
        server, so a closed project and a closed window are the same event and the runner's `atexit`
        does the killing; over SSH the job must survive, which is why nothing here cancels it.
        """
        with self._lock:
            self._job = None
            self._result_id = None
            self._step = None
            self._warnings = ()
            self._surfaces.clear()
            self._scratch = None
            self._prepared = None
            self._prepared_revision = None

    # ------------------------------------------------------------------ helpers

    def _host_of(self, result_id: str | None = None) -> str:
        """The host a mesh runs on: the result node's own record, or `local`.

        **The result node is the authority**, not the settings and not a remembered value. It
        is written when the mesh starts (`result.params["host"]`) and it is what `_reusable`
        already compares against, so every path that needs the right agent reads one fact.

        This exists because call sites hardcoded `local` - recorded as correct while
        `HOST_NAMES == ("local",)` and **wrong the day a second host exists**: cancelling a
        remote mesh would signal the local agent's job id and silently do nothing, and
        `log.tail` would read the wrong agent entirely. Falling back to `local` is right rather
        than raising: a result node that predates the `host` parameter ran locally.
        """
        project = self._session.project
        node = self._result_node(project) if project is not None else None
        if node is None or (result_id is not None and str(node.id) != result_id):
            return "local"
        return str(node.params.get("host") or "local") or "local"

    def _result_node(self, project: Project | None) -> Node | None:
        """The result in use (`active_result`), if there is one."""
        from stokes.mesh.model import active_result

        if project is None:
            return None
        found: Node | None = active_result(project.find(RESULTS_PATH))
        return found

    def _result_of(self, project: Project, mesher: str) -> Node | None:
        """The kept result of ``mesher``: **one per mesher** (ADR-0034), the latest if ever more."""
        from stokes.mesh.model import result_nodes

        mine = [
            child
            for child in result_nodes(project.find(RESULTS_PATH))
            if str(child.params.get("mesher") or "") == mesher
        ]
        return mine[-1] if mine else None

    def _make_active(self, project: Project, chosen: Node) -> None:
        """Mark ``chosen`` the result in use and every other one not; on the session thread."""
        from stokes.mesh.model import result_nodes

        for child in result_nodes(project.find(RESULTS_PATH)):
            wanted = child.id == chosen.id
            if child.params.get("active") is wanted:
                continue
            project.set_param(child.id, "active", wanted)
            self._emit("node.changed", node_id=child.id, path=child.path, param="active")

    def use(self, result_id: str) -> dict[str, Any]:
        """Make a kept result the one in use (`mesh.use`): shown by the viewport, solved on by runs.

        **Refused while a mesh is being made**: the job's events update the result they belong to
        and the status follows the result in use, so switching mid-job would leave the progress
        line describing a mesh the card no longer shows. Stop it, or let it finish.
        """
        from stokes.mesh.model import result_nodes

        project = self._session.require_project()
        chosen = next(
            (
                child
                for child in result_nodes(project.find(RESULTS_PATH))
                if str(child.id) == result_id
            ),
            None,
        )
        if chosen is None:
            raise MeshInvalid(f"there is no mesh result {result_id!r}", code="mesh.no_result")
        with self._lock:
            busy = self._job is not None
        if busy and str(chosen.id) != self._result_id:
            raise MeshBusy("a mesh is being made; stop it or let it finish before choosing another")
        self._make_active(project, chosen)
        with self._lock:
            self._warnings = tuple(chosen.meta.get("warnings") or ())
        # Whether the settings still make this mesh is a new question for a different result.
        self._arm_stale_timer()
        return self.status()

    def _info(self) -> dict[str, Any]:
        """What `mesh.generate` returns."""
        project = self._session.require_project()
        result = self._result_node(project)
        with self._lock:
            job, warnings = self._job, list(self._warnings)
        return {
            "result": str(result.id) if result is not None else None,
            "path": result.path if result is not None else "",
            "state": str(result.params.get("state")) if result is not None else "",
            "job": job or "",
            "basis": str(result.params.get("basis")) if result is not None else "",
            "reused": False,
            "warnings": warnings,
        }

    def _emit(self, name: str, **data: Any) -> None:
        """Send a session event, never letting a listener failure stop the service.

        **`**data`, not a dict**, so the call sites read like `opstore`'s: `node_id` and `path` are
        what a node event carries, and `announce` fills in the rest of the summary from the tree.
        """
        try:
            self._session.emit(name, **data)
        except Exception:
            log.warning("could not emit", extra=fields(event=name))

    def _emit_throttled(self, name: str, **data: Any) -> None:
        """At most `EVENT_INTERVAL_S` events of one name a second.

        **A progress event that names a new step or stage always goes out.** A step's end and the
        next step's start arrive within milliseconds, and the throttle dropped the start: the
        elbow walkthrough's Mesh card said "Meshing: surfaceFeatures" for the whole of a
        76-second snappyHexMesh, because no further event came until that step ended.
        """
        import time

        now = time.monotonic()
        last = self._last_event.get(name, 0.0)
        mark = (str(data.get("step", "")), str(data.get("stage", "")))
        changed = name == "mesh.progress" and mark != self._last_progress
        if now - last < EVENT_INTERVAL_S and not changed:
            return
        self._last_event[name] = now
        if name == "mesh.progress":
            self._last_progress = mark
        self._emit(name, **data)

    def close(self) -> None:
        """Stop listening. Called at process exit, not on a project hook."""
        with self._lock:
            self._closed = True
            unsubscribe, timer = self._unsubscribe, self._stale_timer
            self._unsubscribe = None
            self._stale_timer = None
        if timer is not None:
            timer.cancel()
        if unsubscribe is not None:
            # Closing must not raise: a listener that has already gone is not a failure.
            import contextlib

            with contextlib.suppress(Exception):
                unsubscribe()


def _engine_of(record: dict[str, Any]) -> str:
    """`"org-14"` from a host record's default install."""
    for install in record.get("installs") or []:
        if install.get("bashrc") and install.get("bashrc") == record.get("default_install"):
            return f"{install.get('flavour') or 'org'}-{install.get('version') or '14'}"
    installs = record.get("installs") or []
    if installs:
        first = installs[0]
        return f"{first.get('flavour') or 'org'}-{first.get('version') or '14'}"
    return "org-14"


def _report_of(status: dict[str, Any]) -> dict[str, Any]:
    """The checkMesh report a finished job carries, or an empty dict.

    A parser that failed puts `{"error": …}` there; that is passed through so the result can say the
    quality is unknown rather than pretend the mesh passed.
    """
    reports = status.get("reports") or {}
    for name in ("checkMesh", "check"):
        found = reports.get(name)
        if isinstance(found, dict):
            return found
    for found in reports.values():
        if isinstance(found, dict) and "mesh_ok" in found:
            return found
    return {}


def _layer_report_of(status: dict[str, Any]) -> dict[str, Any]:
    """The snappy step's layer report, or an empty dict (brief M-504).

    **A sibling of `_report_of`, not a change to it.** `_report_of` returns the checkMesh report
    only - and callers rely on that contract (the quality write, `_quality_ok`) - so the layer
    table, which comes from the `snappyHexMesh` step's parser, is read here, by its step name.
    """
    found = (status.get("reports") or {}).get("snappyHexMesh")
    return found if isinstance(found, dict) else {}


def _quality_ok(report: dict[str, Any], thresholds: dict[str, float]) -> bool:
    """Whether the mesh meets the bar: OpenFOAM's verdict **and** this project's thresholds.

    Any figure the log did not carry makes this false. That is deliberate: `None` means "not
    measured", and a mesh nobody measured is not one to call good.
    """
    if not report or report.get("error"):
        return False
    if not report.get("mesh_ok"):
        return False
    non_ortho = report.get("max_non_ortho")
    skew = report.get("max_skewness")
    if non_ortho is None or skew is None:
        return False
    return (
        float(non_ortho) <= thresholds["max_non_ortho"]
        and float(skew) <= thresholds["max_skewness"]
    )


def _error_of(error: Any) -> tuple[str, str]:
    """A job error as `(code, message)`."""
    if not error:
        return "", ""
    if isinstance(error, dict):
        return str(error.get("code") or "mesh.error"), str(error.get("message") or "")
    return "mesh.error", str(error)


def _job_seconds(status: dict[str, Any]) -> float:
    """How long a job ran, from the record the agent wrote; 0.0 when it cannot be told.

    **The record has no top-level `started`.** It has `created` (when the job was staged), one
    `started` and one `ended` per step, and `ended` for the job. The run began when the first step
    that started did, so the wait between `create` and `start` is not counted; a record whose steps
    never started falls back to `created`. A job still running has no `ended` and counts 0.0, and
    so does a timestamp that will not parse: the duration is a convenience on the result, not
    something worth failing a mesh over.
    """
    ended = status.get("ended")
    if not ended:
        return 0.0
    began = next(
        (step.get("started") for step in status.get("steps") or [] if step.get("started")),
        None,
    ) or status.get("created")
    if not began:
        return 0.0
    try:
        from datetime import datetime

        start = datetime.fromisoformat(str(began))
        finish = datetime.fromisoformat(str(ended))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (finish - start).total_seconds())
