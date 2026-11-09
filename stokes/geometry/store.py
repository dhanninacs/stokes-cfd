"""The per-session geometry store: part meshes by node id, imports, the cache and events.

One store per session, in ``session.extensions["geometry"]`` (:meth:`GeometryStore.of`). A
``part`` node is one solid (decision G1): it has its own mesh (one transport group, face ids
from 0), cache file (:mod:`stokes.geometry.cache`), derived parameters and status. The store
runs imports through :mod:`stokes.geometry.worker` and serves transport bytes to the HTTP
endpoint (ADR-0014).

Structure. ``geometry.import`` adds one placeholder part at once, because how many solids
a file holds is known only after OCCT has read it. Then, with one solid, the placeholder is the
part; with N solids it becomes a group with the same id holding one part per solid, named from
the file's solid names, with ids derived from the group's (a replayed journal rebuilds the same
ids). The import stays one undo step (:class:`stokes.geometry.commands.ImportStep`).

Threads. Each import runs on a daemon thread (``stokes-geometry-import``) that talks to its
worker process and emits events with ``session.emit``; the RPC hub delivers events raised on
any thread. That thread updates its parts' derived parameters, status and error by assignment
to keys that already exist, which CPython makes atomic. Building the group changes the tree,
so it runs where commands run: on the server's event loop once :meth:`GeometryStore.bind`
names it, otherwise on the import thread (the CLI and scripts, which wait for the import).

The project. The store implements the session's extension hooks
(:class:`stokes.api.session.SessionExtension`): ``project_opened`` drops the meshes of the
project it replaces and, once :meth:`GeometryStore.activate` has run, restores the new one's;
``project_saved`` writes the caches the folder lacks; ``project_closed`` drops everything. Only
the event loop binding and the activation come from the server's connection.

Events::

    geometry.import_progress {node_id, path, pass, solid, index, count, triangles}
        pass "read" twice: the import's top node (the group, or the part), solid null, index 0.
        First, count 0, as the job starts, because a worker that is not warm needs seconds to
        load OCP before it reads the file and nothing else is reported until then; then once
        the file is read, count = solids to tessellate. Then one per solid and pass with the
        part's node_id and path, solid = its name in the file, index = 0 .. count - 1
    geometry.part_ready {node_id, path, pass, complete}
        a part's mesh changed; fetch /api/geometry/{node_id}. After its coarse mesh
        (complete false), after its fine mesh (complete true, derived parameters already
        updated), after a cache load (pass "cache") and after a redo
    geometry.import_failed {node_id, path, code, message}
        per part left unfinished (the placeholder when the file cannot be read); code
        geometry.cancelled on cancel
    geometry.part_removed {node_id, path}        a part's mesh dropped (undo, or unfinished)
    node.removed / node.added                    the placeholder replaced by its group and parts
    node.changed {node_id, path}                 a part's derived parameters updated

Size: one class over about 900 lines, kept whole on purpose. Its lock guards the maps of
meshes, jobs and errors that the import threads, the HTTP threads and commands share; with
every access to them in one file the locking can be reviewed in one place. The undo command
that drives it is :mod:`stokes.geometry.commands`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from stokes import workspace_root
from stokes.errors import StokesError, ValidationError
from stokes.geometry import formats
from stokes.geometry.errors import (
    GeometryError,
    GeometryFileError,
    GeometryWorkerError,
    ImportCancelled,
)
from stokes.geometry.files import SourceStamp, node_name_for, unique_name
from stokes.geometry.mesh import PartMesh, Vec3, bbox_list, bbox_union
from stokes.geometry.shapes import cache_dir, copy_shapes, solid_part_id
from stokes.geometry.step import PASSES
from stokes.log import fields, get_logger
from stokes.model.ids import is_id, new_id

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.geometry.mesh import TriMesh
    from stokes.geometry.worker import ReadInfo, SolidInfo, SolidResult
    from stokes.model.nodes import Node
    from stokes.model.project import Project

log = get_logger(__name__)
T = TypeVar("T")
Dispatch = Callable[[Callable[[], None]], object]

EXTENSION = "geometry"
GEOMETRY_PATH = "/geometry"
PARTS_PATH = "/geometry/parts"
FINAL_PASS = PASSES[-1].name
SESSION_TIMEOUT = 300.0
"""How long an import waits for a busy event loop to take its new parts."""
CACHE_WAIT = 30.0
CACHE_WRITERS = 4
"""Threads writing an import's per-part mesh cache files.

Four, not more: 4,500 small atomic writes took 13.2 s on one thread, 7.7 s on four, 21.7 s on eight
and 37.2 s on thirty-two (NTFS directory and scanner contention, 2026-11-09), so more threads are
slower. The same 4,500 parts as one pack take 0.06 s."""
"""Longest a caller waits for another thread already reading the same part's cache file."""
_QUIET = ("undo", "replaced", "project", "closed", "removed")
_store_lock = threading.Lock()


def hex_color(rgb: Vec3) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def part_id_for(group_id: str, index: int) -> str:
    """The id of the part made from solid ``index`` when the import ``group_id`` holds N solids
    (the rule the import worker names the B-reps it caches by)."""
    return solid_part_id(group_id, index)


@dataclass
class MeshEntry:
    """A part's mesh as shown now: coarse while importing, then final."""

    part: PartMesh
    complete: bool = False
    packed: bytes | None = None
    basis: str | None = None
    """The B-rep version this mesh was made from, so a stale one can be refused (defect 2).

    **The in-memory entry needs this, and the caches do not cover it.** A session that is already
    open
    never reads the cache: `_entries` holds the imported mesh, so a units change left the viewport
    drawing millimetres while the part's record and its basis said inches. Checked in `_entry`,
    which
    is the one place every reader goes through.
    """


@dataclass(eq=False)
class ImportJob:
    """One running import. ``top`` is the placeholder that decides the structure at the read;
    ``targets`` maps file solid indices to part ids (known up front when re-importing).
    ``reason`` says who cancelled it. ``units`` is the length unit for a format the file does
    not declare (BREP, STL, OBJ); ``family`` is what the source gives the parts, and a solid the
    reader reports as ``mesh`` sets it on its part whatever the job started with."""

    source: Path
    project: Project
    top: str | None = None
    targets: dict[int, str] | None = None
    units: str | None = None
    family: str = "brep"
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    stage: str = "starting"
    stamp: SourceStamp | None = None
    order: dict[int, int] = field(default_factory=dict)
    finished: set[str] = field(default_factory=set)
    started: float = field(default_factory=time.monotonic)
    #: Run once the import has finished, through :meth:`GeometryStore.on_session_thread`.
    #:
    #: **The hook a caller needs when it cannot wait.** `wait` refuses to block the thread commands
    #: run on, because the import needs that same thread to build its parts - so a caller that wants
    #: to act on the finished geometry has nowhere to stand. The sample builder is exactly that
    #: caller: it tags the pipe's faces, and the faces do not exist until the read is done.
    on_done: Callable[[], None] | None = None
    adjacency: dict[str, tuple[Any, int, int]] = field(default_factory=dict)
    """Per finished part: its adjacency and its mesh's face and triangle counts, written once."""
    cache_pool: ThreadPoolExecutor | None = None
    cache_writes: list[Future[None]] = field(default_factory=list)
    """The writes queued on ``cache_pool``, which ``GeometryStore._drain_cache`` waits for."""
    bases: dict[str, str] = field(default_factory=dict)
    """The B-rep version each part's mesh was made from, by part id.

    Carried into the mesh bytes so a cache can tell "the same file" from "the same geometry"
    (Claude's defect 1).

    **Per PART, not per job.** One job reads one source file and makes a part per solid, and each
    solid's basis differs — the basis hashes the solid's index and its face count. A single field on
    the job is therefore wrong, and wrong in the worst way: the last solid read overwrote it, so
    every earlier part was cached under the LAST part's basis. Measured on a two-solid file, the
    pack entries came out swapped — `bracket` carrying `pin`'s basis — and on reopen the mismatched
    parts fell through to a re-import.
    """

    def nodes(self) -> list[str]:
        ids = [] if self.top is None else [self.top]
        return ids + [n for n in (self.targets or {}).values() if n not in ids]


class GeometryStore:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._lock = threading.RLock()
        self._entries: dict[str, MeshEntry] = {}
        self._reading: dict[str, threading.Event] = {}
        """Parts whose cache file is being read now: a second caller waits, never reopens."""
        self._jobs: dict[str, ImportJob] = {}
        self._errors: dict[str, GeometryError] = {}
        self._cached: set[tuple[Path, str]] = set()  # (project folder, part id) written or read
        self._active = False
        self._closed = False
        self._dispatch: Dispatch | None = None
        self._session_thread: int | None = None

    @classmethod
    def of(cls, session: Session) -> GeometryStore:
        """The session's store, created on first use."""
        with _store_lock:
            store = session.extensions.get(EXTENSION)
            if not isinstance(store, GeometryStore):
                store = cls(session)
                session.extensions[EXTENSION] = store
            return store

    def bind(self, dispatch: Dispatch, thread: int) -> None:
        """Change the tree through ``dispatch``, which runs a callable where commands run.

        The server passes its event loop's ``call_soon_threadsafe`` and the loop's thread, on
        which :meth:`wait` refuses to block (the import would wait for that same thread).
        """
        with self._lock:
            self._dispatch, self._session_thread = dispatch, thread

    def blocks_session(self) -> bool:
        """True on the thread an import may need for building its parts."""
        return self._session_thread is not None and threading.get_ident() == self._session_thread

    def shape_dir(self) -> Path:
        """Where the session's parts keep their B-reps: **the project's cache folder**.

        **One answer, because a project always has a folder** (claude-win2, reading 1, binding).
        There used to be a per-session `tempfile.mkdtemp` for a project that had not been saved,
        with `project_saved` copying the B-reps out of it - and the copy is what made a save slow
        and what could be interrupted. With the folder guaranteed from `project.new` onward there is
        nothing to copy out of.
        """
        folder = self._session.project_path
        if folder is None:
            from stokes.errors import StokesError

            raise StokesError(
                "this session has no project, so it has nowhere to keep geometry; "
                "open or create a project first",
                code="geometry.no_project",
            )
        return cache_dir(folder)

    def basis(self, node: Node) -> str:
        """The version of a part's B-rep **now**: recomputed when the node's units disagree with
        the basis stored for it, else the stored ``basis``; when none is stored (a project saved
        before ``basis`` existed) the hash of its mesh's source stamp; empty when nothing is known.

        **Why this recomputes rather than returning the stored value.** Claude's defect 2: the
        stored basis is written once, at import, so a later
        `parameter.set node=… name=units value=inch` changed the geometry and left the identity
        alone. Every cache keyed on the identity then hit — the topology record served at the old
        scale, the B-rep sidecar, and the mesh, whose key compares only the source stamp
        (`cache._fresh_against`) — so the part went on being drawn and measured at millimetres while
        the model said inches. Nothing looked broken; the numbers were simply from the old geometry.

        The units are part of the identity only when they are not the format's default
        (`fingerprints.is_default_units`), so recomputing is safe for old projects: a part carrying
        the default units recomputes to exactly the basis it already has.

        The stamp is read from the source file, so this touches the disk. It is called on the cold
        paths (a topology fetch, a mesh load, a selection check), not per frame; the mesh path
        already stats the source to test its cache.
        """
        from stokes.geometry.fingerprints import basis_for_comparison, basis_for_source, basis_of
        from stokes.geometry.formats import read_units

        stored = node.params.get("basis")
        if isinstance(stored, str) and stored:
            source = node.params.get("source")
            if not isinstance(source, str) or not source:
                return stored
            # A format that declares its units in the file has them in the bytes, so a changed
            # `units` parameter names nothing and cannot change the geometry: leave it alone.
            path = Path(source)
            try:
                if read_units(path, node.params.get("units")) is None:
                    return stored
                stamp = SourceStamp.of(path)
            except (OSError, GeometryError):
                # The source is gone or unreadable: the stored basis is the only account of the
                # geometry there is, and a missing file is not a geometry change.
                return stored
            # **Recomputed on the COMPARISON side of the units.** `basis_for_source` hashes the
            # units
            # text as typed, because that is what is stored on disk and in every tag (defect 3 of
            # the
            # second review: canonicalising it renamed the geometry of every existing part). But
            # this
            # is a staleness decision, and `in` and `inch` are one unit: comparing the typed text
            # would report a part imported as `inch` as changed the moment its units said `in`,
            # which
            # is the re-import defect 6 was about. `basis_for_comparison` canonicalises, so the two
            # spellings agree here without either stored value moving.
            # The STORED spelling, then the canonical one when it differs: the stored value is what
            # the worker wrote and what every tag and cache on disk carries, so returning the
            # canonical spelling when the two AGREE would hand callers a string that matches nothing
            # (the store's own "worker and store agree" test caught that: worker=8f7b4efc0800
            # store=9372b3a762f6). Canonicalising only when they differ keeps the answer stable for
            # the aliases and still moves it for a real change.
            recomputed = basis_for_source(
                path,
                stamp,
                solid_index(node),
                _count(node.params.get("faces")),
                node.params.get("units"),
            )
            if (
                recomputed
                and basis_for_comparison(
                    stamp,
                    solid_index(node),
                    _count(node.params.get("faces")),
                    node.params.get("units"),
                )
                == stored
            ):
                # An alias, not a change: `in` and `inch` are one unit, so this is NOT news and the
                # stored value stands.
                return stored
            # **The comparison decides, but the STORED value is what is returned.** The worker wrote
            # `basis_for_source(...)` with the units as typed, and this function's result is used as
            # a
            # cache key and compared against that stored value all over the store. Returning the
            # canonical spelling when it merely agreed would hand callers a string that matches
            # nothing: the store's own "worker and store agree" test caught exactly that —
            # worker=8f7b4efc0800 store=9372b3a762f6. So the canonical hash is used ONLY to answer
            # "has it changed?", and the stored value is returned either way.
            return recomputed or stored
        # **`self._entries`, not `self.mesh()`.** `mesh` goes through `_entry`, which calls
        # `_load_cache` when the part is not in memory — and `_load_cache` is the caller of this
        # function. That is a cycle: loading a part's cache asked for the basis, which asked for the
        # mesh, which tried to load the cache again. Measured as a read that never started in the
        # concurrency tests. The in-memory entry is enough here: on the load path there is nothing
        # cached yet (that is why it is loading), and off it the entry is already present.
        with self._lock:
            entry = self._entries.get(node.id)
        mesh = None if entry is None else entry.part
        return (basis_of(mesh, solid_index(node)) or "") if mesh is not None else ""

    # ------------------------------------------------------------------ meshes
    def mesh(self, node_id: str) -> PartMesh | None:
        """A part's current mesh (possibly still coarse), from memory or the cache."""
        entry = self._entry(node_id)
        return None if entry is None else entry.part

    def transport(self, node_id: str) -> bytes | None:
        """Transport version 2 bytes for one part, or None when there is nothing to serve."""
        from stokes.geometry.transport import pack_part

        entry = self._entry(node_id)
        if entry is None:
            return None
        with self._lock:
            if entry.packed is None:
                entry.packed = pack_part(entry.part)
            return entry.packed

    def topology(self, node_id: str) -> bytes | None:
        """The part's edge/vertex topology as a packed payload, or None when there is none.

        The lazy endpoint of ADR-0022 §15.2: the record is read from the part's topology cache
        when it is fresh for the part's current basis, otherwise it is generated in the geometry
        worker (never on the request thread) and cached. A part with no B-rep (a mesh-only import,
        or a derived part whose operation has not run) has no topology and gives None.
        """
        from stokes.geometry import topology_cachefile as cachefile
        from stokes.geometry.shapes import part_spec
        from stokes.geometry.topology_brep import sampling_policy
        from stokes.geometry.topology_transport import pack_topology

        project = self._session.project
        if project is None:
            return None
        node = project.find(node_id)
        if node is None or node.type != "part":
            return None
        basis = self.basis(node)
        folder = self.shape_dir()
        path = cachefile.topology_path(folder, node_id)
        if basis:
            # The policy is part of the key (A11): a cache written under different sampling
            # parameters is a miss, so the record is regenerated rather than served.
            cached = cachefile.read_topology(path, basis, sampling_policy())
            if cached is not None:
                return pack_topology(cached)
        spec = part_spec(node, folder, basis)
        if not spec.get("source") and not spec.get("brep"):
            return None  # nothing to read a B-rep from: this part has no topology
        from stokes.geometry.worker import acquire

        answer = acquire().topology(spec, cache=path)
        return _pack_topology_answer(answer)

    def _derived_basis(self, node_id: str) -> str | None:
        """The stored basis of a derived part, as the node itself records it.

        Read straight from the node rather than through `basis()`, which recomputes from a source
        file that a derived part does not have. The node's own value is what the topology record and
        the B-rep sidecar are keyed on, so the mesh agrees with them by construction.
        """
        node = self._node(node_id)
        if node is None:
            return None
        stored = node.params.get("basis")
        return stored if isinstance(stored, str) and stored else None

    def take(self, node_id: str) -> MeshEntry | None:
        """Remove a part's mesh (undo); a finished mesh comes back for :meth:`put`."""
        with self._lock:
            entry = self._entries.pop(node_id, None)
            self._errors.pop(node_id, None)
        if entry is not None:
            self._emit("geometry.part_removed", node_id, path=self._path(node_id))
        return entry if entry is not None and entry.complete else None

    def put(self, node_id: str, entry: MeshEntry) -> None:
        """Restore a mesh taken by :meth:`take` (redo)."""
        with self._lock:
            self._entries[node_id] = entry
        self._ready(node_id, FINAL_PASS, complete=True)

    def accept(
        self,
        node_id: str,
        mesh: TriMesh,
        pass_name: str,
        adjacency: Any = None,  # Any: topology.Adjacency from the worker
    ) -> None:
        """An operation's output mesh (ADR-0021 decision 8.5): the coarse one is shown at once;
        the final one is packed, cached, written to the part's derived parameters and announced,
        as an import's final mesh is. Derived meshes carry no source stamp: their version is the
        part's ``basis``.

        **The basis has to be recorded here too.** A derived part has no source stamp, so the basis
        is the only thing that can tell one version of its geometry from another — which is what
        the cache compares (Claude's defect 1). Writing the bytes without it made every derived mesh
        look like "bytes from before the basis was recorded" and be rejected as stale: the M1 gate
        failed with `MeshUnavailable` on the enclosure's output, because the mesh the operation had
        just written could not be read back.
        """
        from stokes.geometry.transport import pack_part, unpack

        basis = self._derived_basis(node_id)
        if pass_name != FINAL_PASS:
            with self._lock:
                self._entries[node_id] = MeshEntry(PartMesh((mesh,), "m", None, basis), basis=basis)
            self._ready(node_id, pass_name, complete=False)
            return
        data = pack_part(PartMesh((mesh,), "m", None, basis))
        final = MeshEntry(unpack(data), complete=True, packed=data, basis=basis)
        with self._lock:
            self._entries[node_id] = final
        folder = self._session.project_path
        if folder is not None:
            self._write_mesh(folder, node_id, data)
            if adjacency is not None:
                self._merge_adjacency(
                    folder, {node_id: (adjacency, mesh.faces, mesh.triangle_count)}
                )
        part = final.part
        node = self._node(node_id)
        project = self._session.project
        if node is not None and project is not None:
            for name, value in (
                ("triangles", part.triangles),
                ("faces", part.faces),
                ("bbox", bbox_list(part.bbox)),
            ):
                project.set_param(node.id, name, value)
            self._emit("node.changed", node_id, path=node.path)
        self._ready(node_id, FINAL_PASS, complete=True)

    def _write_mesh(self, folder: Path, node_id: str, data: bytes) -> None:
        from stokes.geometry.cache import write_cache

        try:
            write_cache(folder, node_id, data)
        except OSError as exc:
            log.warning("mesh cache not written", extra=fields(node_id=node_id, error=str(exc)))
            return
        with self._lock:
            self._cached.add((folder, node_id))

    # ------------------------------------------------------------------ jobs
    def import_file(
        self,
        top: str,
        source: Path,
        *,
        units: str | None = None,
        family: str | None = None,
    ) -> ImportJob:
        """Import ``source`` into the placeholder part ``top``; the read decides the structure.

        ``units`` is the length unit for a format the file does not declare; ``family`` is what
        the source gives the parts (its format's, looked up when not given)."""
        if family is None:
            family = formats.family_for(source)
        return self._start(
            ImportJob(source, self._session.require_project(), top=top, units=units, family=family)
        )

    def reimport(
        self, source: Path, targets: dict[int, str], *, units: str | None = None
    ) -> ImportJob:
        """Import solids of ``source`` (file index → part id) into parts that already exist.

        ``units`` is the length unit for a format the file does not declare (BREP)."""
        return self._start(
            ImportJob(source, self._session.require_project(), targets=targets, units=units)
        )

    def _start(self, job: ImportJob) -> ImportJob:
        self.cancel_many(job.nodes(), reason="replaced")
        job.stamp = _stamp(job.source)
        with self._lock:
            for node_id in job.nodes():
                self._jobs[node_id] = job
                self._errors.pop(node_id, None)
        thread = threading.Thread(
            target=self._run, args=(job,), name="stokes-geometry-import", daemon=True
        )
        thread.start()
        return job

    def _covering(self, node_id: str | None) -> list[ImportJob]:
        """The distinct jobs covering ``node_id``; every job for None."""
        with self._lock:
            found = {id(j): j for n, j in self._jobs.items() if node_id is None or n == node_id}
        return list(found.values())

    def cancel(self, node_id: str | None = None, *, reason: str = "user") -> list[str]:
        """Cancel the import covering a node, or every import; returns the nodes affected."""
        return self._cancel_jobs(self._covering(node_id), reason)

    def cancel_many(self, node_ids: Iterable[str], *, reason: str = "user") -> list[str]:
        """Cancel every import covering one of ``node_ids``."""
        wanted = set(node_ids)
        with self._lock:
            jobs = list({id(j): j for n, j in self._jobs.items() if n in wanted}.values())
        return self._cancel_jobs(jobs, reason)

    def _cancel_jobs(self, jobs: list[ImportJob], reason: str) -> list[str]:
        affected: list[str] = []
        for job in jobs:
            if not job.cancel.is_set():
                job.reason = reason
                job.cancel.set()
            affected.extend(n for n in job.nodes() if n not in affected)
        return affected

    def when_done(self, node_id: str, action: Callable[[], None]) -> bool:
        """Run ``action`` once the import covering ``node_id`` finishes; False if there is none.

        **The non-blocking counterpart of :meth:`wait`**, and the one to use on the session
        thread, where `wait` refuses. The action runs through :meth:`on_session_thread`, so it may
        change the tree; it does not run when the import was cancelled.
        """
        covering = self._covering(node_id)
        if not covering:
            return False
        for job in covering:
            job.on_done = action
        return True

    def wait(self, node_id: str | None = None, timeout: float | None = None) -> bool:
        """Block until the import(s) covering ``node_id`` (or all) end; False on timeout."""
        if self.blocks_session():
            raise ValidationError(
                "waiting for an import would block the server; follow the geometry.* events"
            )
        deadline = None if timeout is None else time.monotonic() + timeout
        for job in self._covering(node_id):
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not job.done.wait(left):
                return False
        return True

    def error(self, node_id: str) -> GeometryError | None:
        """Why the last import of a part failed, if it did."""
        with self._lock:
            return self._errors.get(node_id)

    def prewarm(self) -> int:
        """Start a worker process now, so the next import does not wait for OCP to load."""
        from stokes.geometry import worker

        return worker.prewarm(1)

    def activate(self) -> None:
        """Serve parts: load caches or re-import every part now and whenever a project opens
        (:meth:`project_opened`)."""
        with self._lock:
            if self._active:
                return
            self._active = True
        self._background(self.restore, "stokes-geometry-restore")

    def restore(self) -> None:
        """Give every part of the open project a mesh: the cache when fresh, else re-import.

        Parts of one file share one worker. A placeholder saved before its file was read
        imports again from the start; other parts re-import their own solid.
        """
        project = self._session.project
        if project is None:
            return
        folder = self._session.project_path
        if folder is not None:
            from stokes.geometry.cache import pack_reader

            pack_reader(folder).refresh()  # a rewrite by another process since the last read
        pending: dict[str, list[Node]] = {}
        derived: list[str] = []
        for node in parts_under(project.root):
            if self._session.project is not project:
                return
            with self._lock:
                busy = node.id in self._entries or node.id in self._jobs
            if busy:
                continue
            if self._load_cache(node) is not None:
                self._ready(node.id, "cache", complete=True)
                continue
            if node.params.get("derived"):
                derived.append(node.id)  # no file to read: its operation runs again
                continue
            pending.setdefault(str(node.params.get("source") or ""), []).append(node)
        # Every part that answered from its own cache file is in memory now: lay the project out
        # as one pack (T-101), so this is the last load that pays a file open per part.
        self._pack_project(self._session.project_path, project)
        for source, nodes in pending.items():
            self._restore_source(source, nodes)
        self._restore_operations(derived)  # after the re-imports: runs wait for those

    def reimport_part(self, node: Node) -> bool:
        """Re-read one part from its source, because something it depends on has changed.

        **For a units change in a live session** (Claude's defect 2). `parameter.set units=inch`
        leaves
        the source file byte-identical, so nothing else in the session notices: the record's basis
        moves, the entry's does not, and the viewport kept drawing the old scale. Dropping the entry
        is
        not enough on its own — the cache misses at the new basis, and a miss with no re-import
        leaves
        the part with no mesh at all.

        Returns whether a re-import was started. A part with no source, or one whose source file has
        gone, is left alone: there is nothing to read, and the caller's own error path reports it.
        """
        source = node.params.get("source")
        if not isinstance(source, str) or not source:
            return False
        path = Path(source)
        if not path.is_file():
            return False
        # The entry goes first: a re-import cannot replace what a reader is being served, and a
        # stale
        # entry would be returned by `_entry` in the window before the worker finishes.
        with self._lock:
            self._entries.pop(node.id, None)
        units = node.params.get("units")
        self.reimport(
            path, {solid_index(node): node.id}, units=units if isinstance(units, str) else None
        )
        return True

    def _restore_source(self, source: str, nodes: list[Node]) -> None:
        """Re-import the parts of one source file whose meshes the cache could not give."""
        path = Path(source)
        if not source or not path.is_file():
            for node in nodes:
                missing = GeometryFileError(
                    f"the source file {source or '(none)'} is missing and no mesh is cached",
                    details={"path": source},
                )
                self._record_failure(node.id, missing)
            return
        units = None
        try:
            if formats.format_for(path).units == "param":
                units = next(
                    (
                        n.params["units"]
                        for n in nodes
                        if isinstance(n.params.get("units"), str) and n.params["units"]
                    ),
                    None,
                )
        except GeometryError:
            pass  # an unknown suffix: the reader refuses the source with its own error
        targets: dict[int, str] = {}
        for node in nodes:
            if node.status == "new" and not node.params.get("faces"):
                self.import_file(node.id, path, units=units)  # never read: the structure is open
                continue
            node.status = "stale"
            index = solid_index(node)
            if index in targets:
                self.reimport(path, {index: node.id}, units=units)
            else:
                targets[index] = node.id
        if targets:
            self.reimport(path, targets, units=units)

    def _restore_operations(self, derived: list[str]) -> None:
        """Hand the project's operations to their service: those whose outputs have no mesh run
        again, and those whose inputs changed while the project was closed go stale."""
        from stokes.geometry.opstore import OperationStore

        OperationStore.of(self._session).restored(derived)

    def close(self) -> None:
        """Cancel every import and stop following the session's project (the hooks do nothing
        from now on; the store stays in ``session.extensions`` and answers queries)."""
        with self._lock:
            self._closed = True
        self.cancel(reason="closed")

    # ------------------------------------------------------------------ the session's project
    def project_opened(self, project: Project, path: Path | None) -> None:
        """Session hook: ``project`` replaces the open one. Imports and meshes of the old project
        go; once activated, the new project's parts are restored in the background."""
        with self._lock:
            if self._closed:
                return
        self._forget()
        with self._lock:
            active = self._active
        if active:
            self._background(self.restore, "stokes-geometry-restore")

    def project_closed(self) -> None:
        """Session hook: no project is open; its imports and meshes go."""
        with self._lock:
            if self._closed:
                return
        self._forget()

    def _current_cache(self) -> Path | None:
        """The cache folder the project is using **now**, or None when there is no project.

        Not `shape_dir()`, which raises when a session has no project: this runs inside a save hook,
        where "no project" is a state to tolerate rather than a mistake in a call.
        """
        folder = self._session.project_path
        return cache_dir(folder) if folder is not None else None

    def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
        """Session hook: write the caches ``path`` lacks, for parts imported before the project
        had a folder and after a save to a new one. Known from memory, so a save costs no file
        checks."""
        with self._lock:
            if self._closed:
                return
            missing = {
                n: e.packed
                for n, e in self._entries.items()
                if e.complete and e.packed and (path, n) not in self._cached
            }
        # The B-reps live on disk only: they come along from the folder the project WAS in, for
        # every part that has one there and not in the new folder.
        #
        # **`previous` names where the project came from; with none, it is where it IS**
        # (claude-win2, reading 1). This used to fall back to the session SCRATCH folder, because
        # that is where an unsaved project kept its B-reps - and when I removed the scratch I set
        # that to `None`, which silently stopped `copy_shapes` running at all. **Measured**: two
        # portability tests passed before the removal and failed after, "the sidecar did not
        # travel".
        #
        # With no `previous`, `session.project_path` is already the CURRENT folder - for a first
        # save, the one `project.new` created - so reading from it is the relationship the scratch
        # fallback expressed.
        source = cache_dir(previous) if previous is not None else self._current_cache()
        shapes_from = source if source is not None and source != cache_dir(path) else None
        parts = [n.id for n in parts_under(project.root)] if shapes_from is not None else []

        def write_missing() -> None:
            from stokes.geometry.cache import write_cache

            for node_id, payload in missing.items():
                if project.find(node_id) is None:
                    continue
                try:
                    write_cache(path, node_id, payload)
                except OSError as exc:
                    log.warning(
                        "mesh cache not written", extra=fields(node_id=node_id, error=str(exc))
                    )
                    continue
                with self._lock:
                    self._cached.add((path, node_id))
            if shapes_from is not None:
                copy_shapes(shapes_from, cache_dir(path), parts)
            # The parts this session knows are in the new folder now: lay them out as one pack so
            # the next open reads one file rather than one per part (T-101).
            if not self._pack_project(path, project):
                self._mark_pack_stale(path)

        if missing or parts:
            self._background(write_missing, "stokes-geometry-cache")

    def project_copied(self, project: Project, path: Path, previous: Path | None = None) -> None:
        """Session hook: write this store's cache into a COPY, leaving the session in the original.

        **`project_saved` cannot be reused because it reads the session's path**, which Save a copy
        deliberately leaves on the original (Opus F3). This does the same work against `path`, with
        `previous` - the folder the copy came from - as the source of the B-reps.

        A **closed** store contributes nothing, exactly as `project_saved` decides: the contract the
        tests measure is that a closed store has no cache to give."""
        with self._lock:
            if self._closed:
                return
        source = previous if previous is not None else self._session.project_path
        self.project_saved(project, path, source)

    def pack_project(self, project: Project | None = None) -> bool:
        """Lay every finished part's cache bytes out as the project's one ``meshes.bin``.

        Returns whether a pack was written. Called when a project is activated, so a project
        written by a build from before the pack existed gains one without a migration step, and
        after a save, so the new folder's pack has the parts the save just wrote. A part whose
        bytes are not in memory is taken from its per-part cache file; a part with neither is
        simply left out of the pack and still answers from the per-part path.
        """
        return self._pack_project(self._session.project_path, project or self._session.project)

    def _pack_project(self, folder: Path | None, project: Project | None) -> bool:
        """Write the pack when it lacks a part this project has; return whether one was written.

        A pack that already holds every part with a cache is left alone: nothing is read and
        nothing is rewritten, so an unchanged project's open costs no file open for this. The
        bytes come from memory first (a part that loaded or imported this session), then from the
        old pack, and only then from the part's own file: a per-part file is read only for a part
        the pack could not give, which is what a project from before the pack existed has.
        """
        from stokes.geometry.cache import build_pack, cached_ids, iter_payloads, pack_reader

        if folder is None or project is None or self._session.project is not project:
            return False
        with self._lock:
            if self._closed:
                return False
            known = {
                n: e.packed
                for n, e in self._entries.items()
                if e.complete and e.packed and project.find(n) is not None
            }
        payloads: dict[str, bytes] = {n: d for n, d in known.items() if d is not None}
        pack = pack_reader(folder)
        held = set(pack.ids())
        wanted = {n for n in cached_ids(folder) | set(payloads) if project.find(n) is not None}
        if wanted <= held:
            return False  # the pack has every part: it is current, whatever opened it
        for node_id in sorted(wanted - set(payloads)):
            data = pack.read(node_id) if node_id in held else None
            if data is not None:
                payloads[node_id] = data
        # Parts this session did not load and the old pack lacks come from their own files.
        rest = [n for n in sorted(wanted) if n not in payloads]
        for node_id, data in iter_payloads(folder, rest):
            payloads[node_id] = data
        if not payloads:
            return False
        try:
            target = build_pack(folder, payloads)
        except OSError as exc:
            log.warning("mesh pack not written", extra=fields(error=str(exc)))
            return False
        log.info(
            "mesh pack written",
            extra=fields(parts=len(payloads), bytes=target.stat().st_size),
        )
        return True

    def _pack_finished(self, job: ImportJob) -> None:
        """Lay a finished import's parts out as the project's pack, from the bytes in memory.

        **Why here and not at the next open**: an import wrote one file per part and no pack, so
        the first cold open of a saved 4,500-part project paid 4,500 file opens (49.5 s on the
        development laptop, 2.8 s once the files were in the OS cache) and only then built the
        pack. Every part's bytes are in memory at this point, and the pack is one file: 0.06 s to
        write for the same 4,500 parts. Opened from the pack the same project takes 1.5 s. Not
        fatal: a project without a pack opens from the per-part files as before.
        """
        folder = self._session.project_path
        if folder is None or job.cancel.is_set() or self._session.project is not job.project:
            return
        try:
            self._pack_project(folder, job.project)
        except Exception:
            log.warning("the import's pack was not written", exc_info=True)

    def _mark_pack_stale(self, folder: Path) -> None:
        """Drop a pack this session can no longer vouch for (a save that could not pack)."""
        from stokes.geometry.cache import forget_packs

        forget_packs(folder)

    def _forget(self) -> None:
        """Cancel every import and B-rep check and drop every mesh: they belonged to the project
        that went, as did the scratch folder of its B-reps."""

        from stokes.geometry import checkstore
        from stokes.geometry.cache import forget_packs

        self.cancel(reason="project")
        checks = self._session.extensions.get(checkstore.EXTENSION)
        if isinstance(checks, checkstore.CheckStore):
            checks.cancel(reason="project")
        with self._lock:
            self._entries.clear()
            self._reading.clear()
            self._errors.clear()
            self._cached.clear()
            folder = self._session.project_path
        if folder is not None:
            forget_packs(folder)  # the project went: its pack handle goes with it

    # ------------------------------------------------------------------ queries
    def status(self, node_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(node_id)
            entry = self._entries.get(node_id)
            error = self._errors.get(node_id)
        if job is not None:
            return {
                "status": "importing",
                "stage": job.stage,
                "solids_done": len(job.finished),
                "count": len(job.targets or {}),
            }
        if entry is not None and entry.complete:
            return {"status": "ready"}
        if error is not None:
            state = "cancelled" if isinstance(error, ImportCancelled) else "failed"
            return {"status": state, "error": error.to_dict()}
        return {"status": "missing"}

    def describe(self, node: Node) -> dict[str, Any]:
        """Everything ``geometry.info`` reports about a part (from memory; no file reads)."""
        with self._lock:
            entry = self._entries.get(node.id)
        cache = self._cache_path(node.id)
        info: dict[str, Any] = {
            "node_id": node.id,
            "path": node.path,
            "name": node.name,
            "type": node.type,
            "params": node.summary()["params"],
            "status": node.status,
            "import": self.status(node.id),
            "url": f"/api/geometry/{node.id}",
            "cache": {
                "path": str(cache) if cache else None,
                "present": bool(cache and cache.is_file()),
            },
            "solids": [],
        }
        if entry is not None:
            info["solids"] = [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "color": list(s.color),
                    "faces": s.faces,
                    "vertices": s.vertex_count,
                    "triangles": s.triangle_count,
                    "quality": s.quality,
                }
                for s in entry.part.solids
            ]
        return info

    def summarise(self, node: Node) -> dict[str, Any]:
        """``geometry.info`` for a group: its parts (at any depth) and their totals."""
        parts = list(parts_under(node))
        items: list[dict[str, Any]] = []
        faces = triangles = 0
        for part in parts:
            params = part.params
            items.append(
                {
                    "node_id": part.id,
                    "path": part.path,
                    "name": part.name,
                    "status": part.status,
                    "import": self.status(part.id),
                    "solid": params.get("solid"),
                    "units": params.get("units"),
                    "family": params.get("family"),
                    "faces": params.get("faces"),
                    "triangles": params.get("triangles"),
                    "color": params.get("color"),
                    "visible": params.get("visible"),
                    "opacity": params.get("opacity"),
                    "url": f"/api/geometry/{part.id}",
                }
            )
            faces += _count(params.get("faces"))
            triangles += _count(params.get("triangles"))
        with self._lock:
            own_job = node.id in self._jobs
        states = {i["import"]["status"] for i in items}
        if own_job or "importing" in states:
            overall = "importing"
        elif items and states == {"ready"}:
            overall = "ready"
        elif states & {"failed", "cancelled"}:
            overall = "failed"
        else:
            overall = "missing"
        boxes = [_box(p.params.get("bbox")) for p in parts]
        return {
            "node_id": node.id,
            "path": node.path,
            "name": node.name,
            "type": node.type,
            "status": node.status,
            "import": {"status": overall},
            "parts": items,
            "faces": faces,
            "triangles": triangles,
            "bbox": bbox_list(bbox_union(boxes)),
        }

    # ------------------------------------------------------------------ the job thread
    def _run(self, job: ImportJob) -> None:
        import shutil

        from stokes.geometry import worker

        # **Under the workspace root, not /tmp** (see `stokes.workspace_root`). An import of a
        # large CAD file writes its converted mesh here, and unscoped `mkdtemp` put that in
        # `/tmp` - which is a tmpfs on this machine and filled twice.
        workdir = workspace_root.scratch("stokes-import-")
        log.info("import started", extra=fields(nodes=job.nodes(), source=str(job.source)))
        try:
            self._announce_read(job)
            seconds = worker.run_import(
                job.source,
                workdir=workdir,
                solids=None if job.targets is None else sorted(job.targets),
                units=job.units,
                on_read=lambda info: self._on_read(job, info),
                on_solid=lambda result: self._on_solid(job, result),
                cancel=job.cancel,
                breps=self._breps(job),
            )
        except GeometryError as exc:
            self._failed(job, exc)
        except Exception as exc:
            log.exception("import failed", extra=fields(nodes=job.nodes()))
            self._failed(job, GeometryWorkerError(f"the import failed: {exc}"))
        else:
            try:
                self._drain_cache(job)  # every part's cache file is written before it is finished
                self._finish(job, seconds)
                self._pack_finished(job)
            except GeometryError as exc:
                self._failed(job, exc)
            except Exception as exc:
                log.exception("import could not be finished", extra=fields(nodes=job.nodes()))
                self._failed(job, GeometryWorkerError(f"the import could not be finished: {exc}"))
        finally:
            self._drain_cache(job)
            shutil.rmtree(workdir, ignore_errors=True)
            with self._lock:
                for node_id in job.nodes():
                    if self._jobs.get(node_id) is job:
                        del self._jobs[node_id]
            job.done.set()
            # **After `done`, so a waiter that does not block still sees a finished job** - and
            # through `on_session_thread`, because the hook changes the tree.
            if job.on_done is not None:
                self.on_session_thread(job.on_done, job.cancel)

    def _breps(self, job: ImportJob) -> dict[str, Any] | None:  # Any: JSON
        """Where and under which ids the worker caches the B-reps it reads (ADR-0021 decision
        8.3): the session's shape folder, the source stamp the versions derive from, and the
        placeholder or the re-imported parts. None without a stamp: no version to file them by."""
        if job.stamp is None:
            return None
        targets = None if job.targets is None else {str(k): v for k, v in job.targets.items()}
        return {
            "dir": str(self.shape_dir()),
            "stamp": job.stamp.to_json(),
            # **The source path is what lets the worker resolve the units.** `basis_for_source`
            # returns the units' contribution only for a format that takes them from the import
            # parameter (`read_units`), and it needs the PATH to know which format this is. Leaving
            # it out made the worker's `self.source` None, so it hashed every part as though the
            # units were unknown — which for the default units is right by luck, and for `inch` was
            # the drift Claude found: the sidecar carried one basis and the store recomputed
            # another, so the first load of an inches part re-read its source. Measured: the worker
            # hashed `650ad5754998` where the store said `877745ccd215`.
            "source": str(job.source),
            "top": job.top,
            "targets": targets,
        }

    def _on_read(self, job: ImportJob, info: ReadInfo) -> None:
        if job.cancel.is_set():
            return
        if job.targets is None:
            targets = self._structure(job, info.solids)
            if targets is None:  # the placeholder went away before the file was read
                job.reason = job.reason or "removed"
                job.cancel.set()
                return
            job.targets = targets
            with self._lock:
                if self._jobs.get(job.top or "") is job:
                    for node_id in targets.values():
                        self._jobs[node_id] = job
        else:
            for index in [k for k in job.targets if k >= len(info.solids)]:
                part_id = job.targets.pop(index)
                gone = GeometryFileError(f"{job.source.name} no longer has solid {index + 1}")
                self._fail_part(job, part_id, gone)
        job.order = {k: i for i, k in enumerate(sorted(job.targets))}
        job.stage = "read"
        solids = {s.index: s for s in info.solids}
        from stokes.geometry.fingerprints import basis_for_source

        for index, part_id in job.targets.items():
            solid = solids[index]
            params: dict[str, Any] = {"faces": solid.faces, "units": info.units}
            if solid.kind == "mesh":
                # A mesh-only solid names the part's family whatever the job started as: a
                # re-import of a mesh part (undo-redo, a restored project) carries no family.
                params["family"] = "mesh"
            if job.stamp is not None:
                # The version, written before any mesh of it arrives, so that no tag stamps the
                # old one on new face ids (ADR-0021 decision 8.4); basis_of gives the same.
                # The units, resolved the SAME way the reader resolves them (Claude's decision):
                # they are part of the geometry's identity for BREP/STL/OBJ. **Not `job.units`**,
                # which is the raw import parameter and is None whenever the caller did not type one
                # (`import_step` with no `units`), while the reader fills in the format's default.
                # Using the raw value made the basis written here disagree with the one the reader
                # recomputes, so every such part failed with "changed since it was imported" — 22
                # tests caught it, and this is the second place the same resolution had to be
                # shared.
                basis = basis_for_source(job.source, job.stamp, index, solid.faces, info.units)
                params["basis"] = basis
                # Kept on the job BY PART ID, so the mesh this part later produces is cached with
                # the version it was made from rather than only the file's stamp. A single field
                # would be overwritten by the next solid of the same file.
                job.bases[part_id] = basis
            if solid.color is not None and self._color_untouched(job, part_id):
                params["color"] = hex_color(solid.color)  # the file's, unless the user chose one
            self._update_node(job, part_id, **params)
        if info.warnings:
            self._warn_parts(job, info.warnings)
        self._read_progress(job, len(job.targets))

    def _announce_read(self, job: ImportJob) -> None:
        """Say the import is under way, before the worker is asked for anything.

        A worker that is not warm needs seconds to load OCP and more to read the file, and the
        read pass reports nothing until the file is read; this lets a client show the part as
        loading from the start. The solid count is not known yet, so ``count`` is 0.
        """
        if not job.cancel.is_set() and self._read_node(job):
            self._read_progress(job, 0)

    def _read_progress(self, job: ImportJob, count: int) -> None:
        """The read pass for the import's top node: ``count`` is 0 until the file has been read,
        then the number of solids to tessellate."""
        top = self._read_node(job)
        self._emit(
            "geometry.import_progress",
            top,
            path=self._path(top),
            **{"pass": "read"},
            solid=None,
            index=0,
            count=count,
            triangles=0,
        )

    def _structure(self, job: ImportJob, solids: tuple[SolidInfo, ...]) -> dict[int, str] | None:
        """File solid index → part id: the placeholder itself, or a group of new parts."""
        if job.top is None:
            return None
        if len(solids) == 1:
            return {solids[0].index: job.top}
        built = self._on_session_thread(job, lambda: self._build_group(job, solids))
        if built is None:
            return None
        targets, events = built
        for event, data in events:
            self._session.emit(event, **data)
        return targets

    def _build_group(
        self, job: ImportJob, solids: tuple[SolidInfo, ...]
    ) -> tuple[dict[int, str], list[tuple[str, dict[str, Any]]]] | None:
        """Replace the placeholder by a group of one part per solid. Runs where commands run."""
        project = job.project
        if self._session.project is not project or job.cancel.is_set() or job.top is None:
            return None
        holder = project.find(job.top)
        if holder is None or holder.type != "part" or holder.parent is None:
            return None
        default_color = project.spec_of(holder).param("color").default
        inherited: dict[str, Any] = {}
        if holder.params.get("color") != default_color:
            inherited["color"] = holder.params["color"]
        if holder.params.get("visible") is False:
            inherited["visible"] = False
        old_path = holder.path
        detached, parent_id, index = project.remove(holder.id)
        group = project.add(parent_id, "group", holder.name, node_id=holder.id, index=index)
        events: list[tuple[str, dict[str, Any]]] = [
            ("node.removed", {"node_id": holder.id, "path": old_path}),
            ("node.added", {"node_id": group.id, "path": group.path}),
        ]
        targets: dict[int, str] = {}
        taken: set[str] = set()
        try:
            for solid in solids:
                name = unique_name(node_name_for(solid.name, "solid"), taken)
                taken.add(name)
                part_id = part_id_for(group.id, solid.index)
                params = {
                    "source": str(job.source),
                    "solid": solid.index,
                    "family": job.family,
                    **inherited,
                }
                part = project.add(
                    group.id,
                    "part",
                    name,
                    params,
                    node_id=new_id() if project.find(part_id) else part_id,
                )
                part.status = "new"
                targets[solid.index] = part.id
                events.append(("node.added", {"node_id": part.id, "path": part.path}))
        except StokesError:
            project.remove(group.id)
            project.attach(parent_id, detached, index)
            raise
        return targets, events

    def _on_session_thread(self, job: ImportJob, action: Callable[[], T]) -> T | None:
        """Run ``action`` where commands run; None when the job is cancelled meanwhile."""
        return self.on_session_thread(action, job.cancel)

    def on_session_thread(self, action: Callable[[], T], cancel: threading.Event) -> T | None:
        """Run ``action`` where commands run (the server's event loop once :meth:`bind` named
        it, else here) and return what it returns; None when ``cancel`` is set before it starts.
        Background jobs change the tree through this: imports build their groups, operations
        add their outputs."""
        with self._lock:
            dispatch = self._dispatch
        if dispatch is None or self.blocks_session():
            return action()
        gate = threading.Lock()
        state = ["queued"]
        results: list[T] = []
        failures: list[Exception] = []
        done = threading.Event()

        def run() -> None:
            with gate:
                if state[0] == "abandoned":
                    return
                state[0] = "running"
            try:
                results.append(action())
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()

        try:
            dispatch(run)
        except RuntimeError:  # the event loop has closed: no command runs beside us
            return action()
        deadline = time.monotonic() + SESSION_TIMEOUT
        while not done.wait(0.1):
            late = time.monotonic() > deadline
            if cancel.is_set() or late:
                with gate:
                    if state[0] == "queued":
                        state[0] = "abandoned"
                        if late:
                            raise GeometryWorkerError(
                                f"the server was busy for {SESSION_TIMEOUT:.0f} s; "
                                "the job was abandoned"
                            )
                        return None
        if failures:
            raise failures[0]
        return results[0] if results else None

    def _on_solid(self, job: ImportJob, result: SolidResult) -> None:
        if job.cancel.is_set() or job.targets is None:
            return
        part_id = job.targets.get(result.index)
        if part_id is None:
            return
        mesh = result.mesh
        self._emit(
            "geometry.import_progress",
            part_id,
            path=self._path(part_id),
            **{"pass": result.pass_name},
            solid=mesh.name,
            index=job.order.get(result.index, 0),
            count=len(job.targets),
            triangles=mesh.triangle_count,
        )
        if result.pass_name == FINAL_PASS:
            self._complete(job, part_id, mesh, result.adjacency)
            return
        with self._lock:
            if job.cancel.is_set():
                return
            self._entries[part_id] = MeshEntry(
                PartMesh((mesh,), "m", job.stamp), basis=job.bases.get(part_id)
            )
        self._ready(part_id, result.pass_name, complete=False)

    def _complete(
        self,
        job: ImportJob,
        part_id: str,
        mesh: TriMesh,
        adjacency: Any = None,  # Any: topology.Adjacency from the worker
    ) -> None:
        """A part's final mesh: pack it once, cache it, update the node, announce it."""
        from stokes.geometry.transport import pack_part, unpack

        # The basis the mesh was made from, recorded in the bytes so the cache can tell "the same
        # file" from "the same geometry" (Claude's defect 1). `job.bases` is written by the import,
        # by part id; a derived part gets its own from the operation that made it.
        data = pack_part(PartMesh((mesh,), "m", job.stamp, job.bases.get(part_id)))
        final = MeshEntry(unpack(data), complete=True, packed=data)
        with self._lock:
            if job.cancel.is_set():
                return
            self._entries[part_id] = final
        job.finished.add(part_id)
        self._write_cache(job, part_id, data)
        if adjacency is not None:
            job.adjacency[part_id] = (adjacency, mesh.faces, mesh.triangle_count)
        part = final.part
        self._update_node(
            job, part_id, triangles=part.triangles, faces=part.faces, bbox=bbox_list(part.bbox)
        )
        self._set_status(job, part_id, "ok", None)
        self._ready(part_id, FINAL_PASS, complete=True)

    def _finish(self, job: ImportJob, seconds: float) -> None:
        if job.cancel.is_set():
            raise ImportCancelled("the import was cancelled")
        targets = job.targets or {}
        if job.targets is None or any(n not in job.finished for n in targets.values()):
            raise GeometryWorkerError("the worker finished without sending every solid")
        log.info(
            "import finished",
            extra=fields(
                nodes=job.nodes(),
                parts=len(targets),
                worker_seconds=round(seconds, 3),
                seconds=round(time.monotonic() - job.started, 3),
            ),
        )
        self._write_adjacency_archive(job)

    def _failed(self, job: ImportJob, exc: GeometryError) -> None:
        if job.reason in _QUIET:
            return  # the nodes went away or another job took over: nothing to report
        if isinstance(exc, ImportCancelled):
            exc = ImportCancelled("the import was cancelled", details=exc.details)
        if job.targets is None:
            unfinished = [job.top] if job.top else []
        else:
            unfinished = [n for n in job.targets.values() if n not in job.finished]
        for part_id in unfinished:
            self._fail_part(job, part_id, exc)

    def _fail_part(self, job: ImportJob, part_id: str, exc: GeometryError) -> None:
        with self._lock:
            partial = self._entries.pop(part_id, None)
        if partial is not None:
            self._emit("geometry.part_removed", part_id, path=self._path(part_id))
        self._record_failure(part_id, exc, job)

    def _record_failure(
        self, node_id: str, exc: GeometryError, job: ImportJob | None = None
    ) -> None:
        with self._lock:
            self._errors[node_id] = exc
        if job is not None:
            self._set_status(job, node_id, "failed", exc.message)
        else:
            node = self._node(node_id)
            if node is not None:
                node.status = "failed"
                node.meta = {**node.meta, "error": exc.message}
        log.warning(
            "import failed", extra=fields(node_id=node_id, code=exc.code, error=exc.message)
        )
        self._emit(
            "geometry.import_failed",
            node_id,
            path=self._path(node_id),
            code=exc.code,
            message=exc.message,
        )

    # ------------------------------------------------------------------ helpers
    def _entry(self, node_id: str) -> MeshEntry | None:
        node = self._node(node_id)
        if node is None:
            return None
        with self._lock:
            entry = self._entries.get(node_id)
            busy = node_id in self._jobs
        if entry is not None and not busy:
            # **An entry made from a different B-rep version is not this part's mesh** (Claude's
            # defect 2). A units change leaves the source file identical, so nothing else in the
            # session notices: the record's basis moves and the entry holds the old scale. Dropped
            # here — the one place every reader goes through — so the cache is re-read, and re-read
            # it MISSES (the bytes carry the old basis), which is what makes the part re-import.
            #
            # Only when nothing is importing: a job's own intermediate passes carry the basis it was
            # started with, and dropping them mid-run would fight the worker.
            # **Only when the node actually records a basis**, and only against a non-empty answer:
            #  falls back to hashing the mesh's face COUNT when  is not yet recorded,
            # which is every part mid-import and every part the import left without one. Comparing
            # against that discarded good entries and the meshes came back all zeros — 46 tests.
            # A node with no stored basis is a project saved before the field existed, and its entry
            # is the authority; there is nothing to compare it with.
            stored = node.params.get("basis")
            current = self.basis(node) if isinstance(stored, str) and stored else None
            # **An entry with NO basis is not a stale one** — the same rule as Claude's defect 1,
            # and
            # for the same reason: the importer builds the entry before the node records its basis,
            # so
            # `entry.basis` is None for every part whose import is still being written up. Reading
            # that
            # as "different" discarded good meshes and the demo came out with no tags at all,
            # because
            # its `_faces_of` needs a mesh. Only an entry that RECORDS a basis can disagree.
            if current is not None and entry.basis is not None and entry.basis != current:
                with self._lock:
                    if self._entries.get(node_id) is entry:
                        del self._entries[node_id]
                entry = None
        if entry is not None or busy:
            return entry
        return self._load_cache(node)

    def _node(self, node_id: str) -> Node | None:
        """The part node with this id in the open project, or None."""
        project = self._session.project
        if project is None or not is_id(node_id):
            return None
        node = project.find(node_id)
        return node if node is not None and node.type == "part" else None

    def _path(self, node_id: str) -> str | None:
        project = self._session.project
        node = project.find(node_id) if project is not None and is_id(node_id) else None
        return None if node is None else node.path

    def _read_node(self, job: ImportJob) -> str:
        """The node the read-pass progress names: the import's top node, or the parts' group."""
        if job.top is not None:
            return job.top
        ids = list((job.targets or {}).values())
        parents = {n.parent.id for i in ids if (n := job.project.find(i)) and n.parent}
        if len(parents) == 1 and len(ids) > 1:
            return parents.pop()
        return ids[0] if ids else ""

    def _cache_path(self, node_id: str) -> Path | None:
        from stokes.geometry.cache import cache_file

        folder = self._session.project_path
        return None if folder is None else cache_file(folder, node_id)

    def _load_cache(self, node: Node) -> MeshEntry | None:
        """A part's cached mesh, read from its cache file once.

        The cache load is the expensive part of a cold project (one file open per part) and two
        threads can want the same part at the same time: the batch route answering the viewport and
        the background restore that runs when the first client connects. Reading the file twice
        would be pure waste, so the first caller marks the part as being read and the second waits
        for it instead of opening the same file again.
        """
        from stokes.geometry.cache import read_cache

        folder = self._session.project_path
        if folder is None:
            return None

        def claim() -> MeshEntry | str | None:
            """``"read"`` to read the file here, else the entry or None once the reader is done."""
            with self._lock:
                found = self._entries.get(node.id)
                if found is not None:
                    return found
                gate = self._reading.get(node.id)
                if gate is None:
                    self._reading[node.id] = threading.Event()
                    return "read"
            # Another caller is reading this same part: wait for it rather than open it again.
            gate.wait(CACHE_WAIT)
            with self._lock:
                return self._entries.get(node.id)

        claimed = claim()
        if claimed != "read":
            return claimed if isinstance(claimed, MeshEntry) else None
        gate = self._reading[node.id]
        try:
            source = str(node.params.get("source") or "")
            # The part's CURRENT basis, so a cache made from another version is rejected. The
            # source stamp cannot carry this: a units change leaves the file identical and changes
            # the geometry (Claude's defect 1).
            found = read_cache(
                folder, node.id, Path(source) if source else None, self.basis(node) or None
            )
            if found is None:
                with self._lock:
                    self._entries.pop(node.id, None)
                return None
            part, data = found
            entry = MeshEntry(part, complete=True, packed=data)
            with self._lock:
                existing = self._entries.setdefault(node.id, entry)
                self._cached.add((folder, node.id))
            if existing is entry:
                node.status = "ok"
            return existing
        finally:
            with self._lock:
                self._reading.pop(node.id, None)
            gate.set()

    def _write_cache(self, job: ImportJob, node_id: str, data: bytes) -> None:
        """Queue a part's cache file on the job's writer threads; `_drain_cache` waits for them.

        **Off the import thread**: each file is a small atomic write bound by the file system, 3 ms
        each here, so 4,500 parts held the import thread for 13 s of a 53 s import while the worker
        and the helpers had finished. The writes are independent (one file per part), the part is
        served from memory meanwhile (`MeshEntry.packed`), and an import is reported finished only
        after every write has ended.
        """
        folder = self._session.project_path
        if folder is None or self._session.project is not job.project:
            return
        with self._lock:
            if job.cache_pool is None:
                job.cache_pool = ThreadPoolExecutor(
                    max_workers=CACHE_WRITERS, thread_name_prefix="stokes-mesh-cache"
                )
            pool = job.cache_pool
        job.cache_writes.append(pool.submit(self._write_cache_now, folder, node_id, data))

    def _write_cache_now(self, folder: Path, node_id: str, data: bytes) -> None:
        from stokes.geometry.cache import write_cache

        try:
            write_cache(folder, node_id, data)
        except OSError as exc:
            log.warning("mesh cache not written", extra=fields(node_id=node_id, error=str(exc)))
            return
        with self._lock:
            self._cached.add((folder, node_id))

    def _drain_cache(self, job: ImportJob) -> None:
        """Wait for the job's queued cache writes and stop its writer threads. Safe to repeat."""
        with self._lock:
            pool, job.cache_pool = job.cache_pool, None
            writes, job.cache_writes = job.cache_writes, []
        if pool is None:
            return
        for write in writes:
            try:
                write.result()
            except Exception:
                log.warning("a mesh cache write failed", exc_info=True)
        pool.shutdown(wait=True)

    def _write_adjacency_archive(self, job: ImportJob) -> None:
        """Write the finished parts' adjacency into the project's one archive, once per import.

        Reading the archive first and merging keeps the entries of parts imported earlier, so one
        import writes one file rather than one file per part.
        """
        folder = self._session.project_path
        if folder is None or not job.adjacency or self._session.project is not job.project:
            return
        self._merge_adjacency(folder, job.adjacency)

    def _merge_adjacency(self, folder: Path, entries: dict[str, tuple[Any, int, int]]) -> None:
        """Merge per-part adjacency (with each mesh's face and triangle counts) into the
        project's archive. Any: topology.Adjacency."""
        from stokes.geometry.adjacency_cache import read_archive, write_archive

        try:
            merged = read_archive(folder)
            merged.update(entries)
            write_archive(folder, merged)
        except OSError as exc:
            log.warning("adjacency archive not written", extra=fields(error=str(exc)))

    def _update_node(
        self, job: ImportJob, node_id: str, **params: Any
    ) -> None:  # Any: plain param values
        project = self._session.project
        if project is not job.project:
            return
        node = project.find(node_id)
        if node is None or node.type != "part":
            return
        for name, value in params.items():
            project.set_param(node.id, name, value)
        self._emit("node.changed", node_id, path=node.path)

    def _warn_parts(self, job: ImportJob, warnings: tuple[str, ...]) -> None:
        """The read's notes on every part the import touches: the part's ``meta`` carries them
        in the import's result, as ``error`` already does for a failed read."""
        if self._session.project is not job.project:
            return
        for part_id in (job.targets or {}).values():
            node = job.project.find(part_id)
            if node is not None and node.type == "part":
                node.meta = {**node.meta, "warnings": list(warnings)}

    def _color_untouched(self, job: ImportJob, node_id: str) -> bool:
        node = job.project.find(node_id)
        if node is None:
            return False
        default = job.project.spec_of(node).param("color").default
        return bool(node.params.get("color") == default)

    def _set_status(self, job: ImportJob, node_id: str, status: str, error: str | None) -> None:
        if self._session.project is not job.project:
            return
        node = job.project.find(node_id)
        if node is None:
            return
        node.status = status
        meta = {k: v for k, v in node.meta.items() if k != "error"}
        if error:
            meta["error"] = error
        node.meta = meta  # replaced whole: a reader iterating the old dict is unaffected

    def _ready(self, node_id: str, quality: str, *, complete: bool) -> None:
        self._emit(
            "geometry.part_ready",
            node_id,
            path=self._path(node_id),
            **{"pass": quality},
            complete=complete,
        )

    def _emit(self, event: str, node_id: str, **data: Any) -> None:  # Any: JSON payload
        self._session.emit(event, node_id=node_id, **data)

    def _background(self, target: Callable[[], None], name: str) -> None:
        def guarded() -> None:
            try:
                target()
            except Exception:
                log.exception("geometry background task failed", extra=fields(task=name))

        threading.Thread(target=guarded, name=name, daemon=True).start()


def _stamp(source: Path) -> SourceStamp | None:
    try:
        return SourceStamp.of(source)
    except OSError:
        return None


def parts_under(root: Node) -> Iterator[Node]:
    """The part nodes in a subtree, ``root`` included."""
    for node in root.walk():
        if node.type == "part":
            yield node


def solid_index(node: Node) -> int:
    """Which solid of its source file a part is (its ``solid`` parameter, 0 when unset)."""
    value = node.params.get("solid", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _count(value: Any) -> int:  # Any: a stored integer parameter
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _box(value: Any) -> tuple[Vec3, Vec3] | None:  # Any: a stored bbox parameter
    if not isinstance(value, list) or len(value) != 6:
        return None
    x0, y0, z0, x1, y1, z1 = (float(v) for v in value)
    return ((x0, y0, z0), (x1, y1, z1))


def _pack_topology_answer(answer: dict[str, Any]) -> bytes:  # Any: JSON + a binary frame
    """Repack a worker topology answer (its header plus its one points frame) for the endpoint.

    The worker sends the edges' polylines as one concatenated float32 frame, split by each edge's
    declared point count; the endpoint serves the same layout the codec defines, so the frame is
    spliced straight into it. Going back through ``PartTopology`` here keeps one writer of the
    format (the codec) rather than a second one in the worker.
    """
    import json
    import struct

    import numpy as np

    from stokes.geometry.topology_brep import (
        TOPOLOGY_VERSION,
        PartTopology,
        TopoEdge,
        TopoFace,
        TopoSolid,
        TopoVertex,
    )
    from stokes.geometry.topology_transport import MAX_HEADER_BYTES

    edges_raw = answer.get("edges") or []
    data = answer.get("data") or b""
    offset = 0
    edges: list[TopoEdge] = []
    for index, entry in enumerate(edges_raw):
        if not isinstance(entry, dict):
            continue
        count = int(entry.get("points") or 0)
        points = np.frombuffer(data, "<f4", count * 3, offset).reshape(count, 3)
        offset += count * 12
        edges.append(
            TopoEdge(
                edge_id=int(entry.get("id", index)),
                length=float(entry.get("length") or 0.0),
                centre=tuple(entry.get("centre") or (0.0, 0.0, 0.0)),
                start=tuple(entry.get("start") or (0.0, 0.0, 0.0)),
                end=tuple(entry.get("end") or (0.0, 0.0, 0.0)),
                tangent=tuple(entry.get("tangent") or (0.0, 0.0, 0.0)),
                curve=str(entry.get("curve") or ""),
                points=points.astype(np.float32, copy=False),
                pickable=bool(entry.get("pickable", True)),
                # T-124a: the faces this edge borders, which the worker now sends.
                faces=tuple(int(f) for f in (entry.get("faces") or ())),
            )
        )
    vertices = [
        TopoVertex(
            vertex_id=int(entry.get("id", index)),
            position=tuple(entry.get("position") or (0.0, 0.0, 0.0)),
            degree=int(entry.get("degree") or 0),
            edge_ids=tuple(int(e) for e in entry.get("edges") or ()),
            edge_lengths=tuple(float(x) for x in entry.get("lengths") or ()),
        )
        for index, entry in enumerate(answer.get("vertices") or [])
        if isinstance(entry, dict)
    ]
    value = PartTopology(
        part=str(answer.get("part") or ""),
        basis=str(answer.get("basis") or ""),
        scale=float(answer.get("scale") or 1.0),
        edges=edges,
        vertices=vertices,
        faces=int(answer.get("faces") or 0),
        # v3: the sizes of things. **Rebuilt field by field, which is the hazard**: this function
        # constructs a PartTopology by hand rather than reusing the worker's, so every field added
        # to
        # the record must be added here too or it is dropped on the endpoint's path — and silently,
        # because the payload is still well-formed. That is precisely how T-124a's edge-to-face data
        # went missing, and the v3 tests caught it recurring.
        face_sizes=[
            TopoFace(
                face_id=int(entry.get("id", index)),
                area=float(entry.get("area") or 0.0),
                centroid=tuple(entry.get("centroid") or (0.0, 0.0, 0.0)),
            )
            for index, entry in enumerate(answer.get("faceSizes") or [])
            if isinstance(entry, dict)
        ],
        solids=[
            TopoSolid(
                volume=float(entry.get("volume") or 0.0),
                centroid=tuple(entry.get("centroid") or (0.0, 0.0, 0.0)),
                area=float(entry.get("area") or 0.0),
            )
            for entry in (answer.get("solids") or [])
            if isinstance(entry, dict)
        ],
    )
    header: dict[str, Any] = {
        "version": TOPOLOGY_VERSION,
        "part": value.part,
        "basis": value.basis,
        "units": "m",
        "scale": value.scale,
        "faces": int(value.faces),
        "edges": value.edge_json(),
        "vertices": value.vertex_json(),
        "faceSizes": value.face_size_json(),
        "solids": value.solid_json(),
    }
    body = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body += b"\0" * ((-len(body)) % 4)
    if len(body) > MAX_HEADER_BYTES:
        from stokes.geometry.errors import MeshFormatError

        raise MeshFormatError("the topology header is too large to serve")
    frames = [np.ascontiguousarray(e.points, dtype="<f4").tobytes() for e in edges]
    return struct.pack("<I", len(body)) + body + b"".join(frames)
