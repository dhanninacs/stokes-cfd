"""The geometry worker: source-file reading and tessellation in a separate process (ADR-0014,
ADR-0021 decision 1.4: STEP, IGES and BREP through :mod:`stokes.geometry.readers`).

``multiprocessing`` with the spawn method, one job per process: an OCCT fault ends only the
worker, and the parent reports it as :class:`GeometryWorkerError` with the exit code. The
worker imports OCP first (several seconds on a laptop), says ``ready``, then waits for one job,
so a process can be started ahead of time (:func:`prewarm`) and an import skips that delay.

Every message is a ``send_bytes`` frame: a UTF-8 JSON object, followed by the binary frames it
announces. Nothing is pickled after the hand-over of the target function at spawn, which goes
from parent to child; the parent length-limits and validates every frame it receives, because a
worker that parsed a hostile file is not trusted.

Worker to parent::

    {"type": "ready", "pid", "ocp"}
    {"type": "read", "solids": [{"index", "name", "kind", "color", "faces"}],
     "faces", "units", "seconds", "warnings"}
    {"type": "solid", "pass", "index", "count", "name", "kind", "color", "faces", "face_base",
     "vertices", "triangles", "untriangulated", "seconds"}
        then four frames: positions f32[3N], normals f32[3N], indices u32[3M], face ids u32[M]
    {"type": "done", "seconds"}
    {"type": "error", "code", "message", "details"}

Parent to worker: ``{"type": "job", "path", "passes": [{"name", "linear", "angular"}],
"workdir", "solids", "units", "breps"}``; ``solids`` lists the file indices to tessellate, or is
null for all; ``units`` is the length unit for a format the file does not declare, or null;
``breps`` (``{dir, stamp, top, targets}``, or null) is where to cache each solid's B-rep and
under which part ids (:mod:`stokes.geometry.shapes`). Solids stream one at a time: every chosen
solid at the first pass, then at the next, so the coarse model is complete before refinement
starts. Each solid numbers its faces from 0 (it becomes a part of its own). Cancelling kills the
process. This module is the parent's side; what runs inside the worker is
:mod:`stokes.geometry.worker_main`.

An operation (ADR-0021 decision 8.5) is ``{"type": "job", "kind": "operation", "op_type", "op"
(its node id), "seed" (what its outputs' versions derive from), "params" (SI), "inputs": [spec...]
(:func:`stokes.geometry.shapes.part_spec`), "cache_dir", "workdir", "passes"}``; it answers::

    {"type": "progress", "stage", "fraction", "message"}           any number, any time
    {"type": "shape", "index", "count", "part", "basis", "name", "faces", "valid", "closed",
     "volume", "bbox", "history": {part: {face id: [output face ids]}}, "history_source",
     "tags": {name: [output face ids]}, "report"}                    one per output, in order
    {"type": "solid", ...}                                           as an import, per output
    {"type": "done", "seconds", "version", "report", "warnings"}

and :meth:`GeometryWorker.operation` streams them to callbacks like :meth:`GeometryWorker.run`.

A STEP export (ADR-0021 decision 8.3) is ``{"type": "job", "kind": "export_step", "parts":
[spec...], "names": [the nodes' names], "out"}``, where each spec is what
:func:`stokes.geometry.shapes.part_spec` builds, so one export may mix source files and derived
parts; it answers ``{"type": "exported", "path", "solids", "names", "provider", "seconds"}``
(``provider`` names the job's source reads and cache hits, which T-030 asserts are 0 when the
cache is fresh) and then ``done``. :meth:`GeometryWorker.export_step` collects it.

A B-rep check (ADR-0021 decisions 5 and 8.3) is ``{"type": "job", "kind": "check_brep", "parts":
[spec...]}``, the same provider specs, so one check may mix source files and the derived parts of
operations; it answers any number of ``{"type": "progress", "stage", "fraction", "message",
"part", "index", "count"}`` (one per part, as the check reaches it) and then ``{"type": "brep",
"parts": [{part, name, index, valid, free_edges, open_shells, small_edges, small_faces,
free_edge_faces, small_face_ids, small_edge_faces, truncated, ok}], "provider", "seconds"}``,
followed by ``done``. :meth:`GeometryWorker.check_brep` collects it, calling its ``on_progress``
with the part, its index and the count of parts. Payloads of that job are documented in
:mod:`stokes.geometry.brep`.

Size: about 800 lines, kept whole: it is the parent's side of one protocol, and every frame a
worker sends is checked here before anything else sees it.
"""

from __future__ import annotations

import json
import multiprocessing
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

import numpy as np

from stokes.geometry.errors import (
    CadUnavailable,
    GeometryError,
    GeometryFileError,
    GeometryWorkerError,
    ImportCancelled,
    MeshOnly,
    OperationFailed,
    OperationInvalid,
    SeedOutside,
)
from stokes.geometry.mesh import DEFAULT_COLOR, TriMesh, Vec3
from stokes.geometry.step import PASSES, Quality
from stokes.geometry.topology import Adjacency
from stokes.geometry.worker_main import MAX_DETAILS_BYTES, MAX_JSON_BYTES, child_main
from stokes.log import fields, get_logger
from stokes.model.ids import is_id

log = get_logger(__name__)

DEFAULT_TIMEOUT = 1800.0
MAX_BUFFER_BYTES = 1024**3
MAX_POOL = 4
MAX_TAGS = 256
"""Tags one operation output may name for its faces."""
_POLL_SECONDS = 0.1
_BASIS_RE = re.compile(r"^[0-9a-f]{1,32}$")
_F4 = np.dtype("<f4")
_U4 = np.dtype("<u4")
_I8 = np.dtype("<i8")
_F8 = np.dtype("<f8")


@dataclass(frozen=True)
class SolidInfo:
    index: int
    name: str
    kind: str
    color: Vec3 | None
    faces: int


@dataclass(frozen=True)
class ReadInfo:
    """What the worker found in the file before tessellating."""

    solids: tuple[SolidInfo, ...]
    faces: int
    units: str
    seconds: float
    warnings: tuple[str, ...] = ()
    """The reader's notes (an unknown IGES unit flag), to surface on the parts."""


@dataclass(frozen=True)
class SolidResult:
    pass_name: str
    index: int
    count: int
    mesh: TriMesh
    untriangulated: int
    seconds: float
    adjacency: Adjacency | None = None
    """Face adjacency of this part's mesh, built in the worker on the final pass."""


@dataclass(frozen=True)
class ShapeResult:
    """One output of an operation, as the worker described it (``shape`` message), checked."""

    index: int
    count: int
    part: str
    basis: str
    name: str
    faces: int
    valid: bool
    closed: bool
    volume: float
    """m^3"""
    bbox: tuple[float, ...]
    """six lengths in m, or none for an empty shape"""
    history: dict[str, dict[int, tuple[int, ...]]]
    """input part -> input face id -> output face ids"""
    history_source: str
    """``occt`` when the operation gave a history, ``same`` when faces map only to themselves"""
    tags: dict[str, tuple[int, ...]]
    report: dict[str, Any]  # Any: JSON
    # T-056-fix: the edge and vertex histories. Defaulted, and LAST, because a dataclass requires
    # every defaulted field to follow the non-defaulted ones — the compiler caught that when I first
    # put them beside `history`.
    history_edges: dict[str, dict[int, tuple[int, ...]]] = field(default_factory=dict)
    """input part -> input edge id -> output edge ids"""
    history_vertices: dict[str, dict[int, tuple[int, ...]]] = field(default_factory=dict)
    """input part -> input vertex id -> output vertex ids"""


@dataclass(frozen=True)
class OperationRun:
    """How an operation job ended: the worker's seconds, its OCP version, report and warnings."""

    seconds: float
    version: str
    report: dict[str, Any]  # Any: JSON
    warnings: tuple[str, ...]


ReadCallback = Callable[[ReadInfo], None]
SolidCallback = Callable[[SolidResult], None]
ProgressCallback = Callable[[str, float, str], None]
ShapeCallback = Callable[[ShapeResult], None]
CheckProgress = Callable[[str, int, int], None]
"""A check job's progress: the part id, its index in the job and how many parts the job has."""


class GeometryWorker:
    """One worker process. ``start`` spawns it; ``run`` hands it a job and streams the results."""

    def __init__(self) -> None:
        self._process: BaseProcess | None = None
        # Any: a Connection on POSIX, a PipeConnection on Windows (typeshed keeps them apart)
        self._conn: Any = None
        self._ready = False
        self.ocp = ""
        """The OCP version the process loaded, from its ``ready`` message."""

    def start(self) -> GeometryWorker:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe(duplex=True)
        process = ctx.Process(target=child_main, args=(child,), name="stokes-geometry", daemon=True)
        process.start()
        child.close()
        self._process, self._conn, self._ready = process, parent, False
        log.debug("geometry worker started", extra=fields(pid=process.pid))
        return self

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def run(
        self,
        path: Path,
        *,
        workdir: Path,
        passes: Sequence[Quality] = PASSES,
        solids: Sequence[int] | None = None,
        units: str | None = None,
        on_read: ReadCallback | None = None,
        on_solid: SolidCallback | None = None,
        cancel: threading.Event | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        breps: dict[str, Any] | None = None,  # Any: JSON
    ) -> float:
        """Import ``path`` (only the ``solids`` given by file index, when given).

        ``units`` is the length unit for a format the file does not declare (BREP); ``breps``
        names the shape cache to write each solid's B-rep to (``{dir, stamp, top, targets}``).
        Blocks the calling thread and returns the worker's seconds.
        """
        if self._conn is None:
            self.start()
        deadline = time.monotonic() + timeout
        try:
            while not self._ready:
                self._step(deadline, cancel, path, None, None)
            self._send(
                {
                    "type": "job",
                    "path": str(path),
                    "passes": _passes(passes),
                    "workdir": str(workdir),
                    "solids": None if solids is None else [int(i) for i in solids],
                    "units": units,
                    "breps": breps,
                }
            )
            while True:
                seconds = self._step(deadline, cancel, path, on_read, on_solid)
                if seconds is not None:
                    return seconds
        finally:
            self.kill()

    def check_brep(
        self,
        specs: Sequence[dict[str, Any]],  # Any: JSON shape specs (shapes.part_spec)
        *,
        on_progress: CheckProgress | None = None,
        cancel: threading.Event | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Load the parts the shape-provider ``specs`` describe and return the ``brep`` body: the
        findings, keyed by part id, and the ``provider`` summary (its source reads and cache
        hits).

        Every part loads through the shape provider, so one check may mix source files and the
        derived parts of operations and a cached B-rep is not read from its source again
        (ADR-0021 decisions 5 and 8.3). ``on_progress`` is called on the calling thread with the
        part id, its index and the number of parts, once per part."""
        parts = [dict(spec) for spec in specs]
        return self._collect(
            {"type": "job", "kind": "check_brep", "parts": parts},
            "brep",
            Path("check"),
            timeout,
            label="the B-rep check",
            cancel=cancel,
            on_progress=on_progress,
        )

    def topology(
        self,
        spec: dict[str, Any],  # Any: JSON shape spec (shapes.part_spec)
        *,
        cache: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Read the part's edge and vertex topology (ADR-0022 §15.2); return the ``topology``
        body with its packed points under ``data``.

        ``spec`` is what :func:`stokes.geometry.shapes.part_spec` builds, so a source file and a
        derived operation output are read the same way. ``cache`` is where the worker writes the
        record when given. A mesh-only part has no B-rep and raises
        :class:`~stokes.geometry.errors.GeometryUnsupported`.
        """

        def read_frame(message: dict[str, Any]) -> bytes:
            size = _int(message, "bytes")
            if size == 0:
                return b""
            if size > MAX_BUFFER_BYTES or size % 12:
                raise GeometryWorkerError(
                    "the geometry worker sent a topology buffer of the wrong size"
                )
            return self._conn.recv_bytes(MAX_BUFFER_BYTES) if self._conn is not None else b""

        return self._collect(
            {
                "type": "job",
                "kind": "topology",
                "part": dict(spec),
                "cache": str(cache) if cache is not None else None,
            },
            "topology",
            Path("topology"),
            timeout,
            label="the topology read",
            cancel=cancel,
            frame=read_frame,
        )

    def export_step(
        self,
        specs: Sequence[dict[str, Any]],  # Any: JSON shape specs (shapes.part_spec)
        out: Path,
        *,
        names: Sequence[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Write the parts the shape-provider ``specs`` describe to ``out``; return the
        ``exported`` body, whose ``provider`` names the source reads and cache hits the job did.

        ``names`` are the product names written into the file (each spec's own ``name`` when not
        given). Every part is loaded through the shape provider, so one export may mix source
        files and the derived parts of operations (ADR-0021 decision 8.3)."""
        parts = [dict(spec) for spec in specs]
        labels = (
            [str(name) for name in names]
            if names is not None
            else [str(spec.get("name") or "") for spec in parts]
        )
        return self._collect(
            {
                "type": "job",
                "kind": "export_step",
                "parts": parts,
                "names": labels,
                "out": str(out),
            },
            "exported",
            out,
            timeout,
            label="the STEP export",
        )

    def operation(
        self,
        job: dict[str, Any],  # Any: JSON (op_type, op, seed, params, inputs, cache_dir, workdir)
        *,
        passes: Sequence[Quality] = PASSES,
        on_progress: ProgressCallback | None = None,
        on_shape: ShapeCallback | None = None,
        on_solid: SolidCallback | None = None,
        cancel: threading.Event | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        label: str = "the operation",
    ) -> OperationRun:
        """Run one operation (ADR-0021 decision 8.5) and stream it: progress, then per output
        its ``shape`` and its coarse and fine ``solid`` meshes. The callbacks run on the calling
        thread, which this blocks; cancelling kills the process, as for an import. ``label``
        names the run in errors ("the operation cut_1")."""
        if self._conn is None:
            self.start()
        deadline = time.monotonic() + timeout
        where = Path(label)
        try:
            while not self._ready:
                self._step(deadline, cancel, where, None, None, label)
            self._send({**job, "type": "job", "kind": "operation", "passes": _passes(passes)})
            while True:
                message = self._next(deadline, cancel, where, label)
                kind = message.get("type")
                try:
                    if kind == "progress":
                        if on_progress is not None:
                            on_progress(*_progress(message))
                    elif kind == "shape":
                        shape = _shape(message)
                        if on_shape is not None:
                            on_shape(shape)
                    elif kind == "solid":
                        result = self._solid(message)
                        if on_solid is not None:
                            on_solid(result)
                    elif kind == "done":
                        return _finished(message)
                    elif kind == "error":
                        raise _error_from(message, where, keep_path=False)
                    else:
                        raise GeometryWorkerError(
                            f"the geometry worker sent an unknown message {kind!r}"
                        )
                except (EOFError, OSError) as exc:
                    raise self._died(where, label) from exc
        finally:
            self.kill()

    def _collect(
        self,
        job: dict[str, Any],
        want: str,
        path: Path,
        timeout: float,
        label: str | None = None,
        cancel: threading.Event | None = None,
        on_progress: CheckProgress | None = None,
        frame: Callable[[dict[str, Any]], bytes] | None = None,
    ) -> dict[str, Any]:
        """Send one job; wait for its ``want`` message and ``done``; return the ``want`` body.

        ``path`` is what the job is about (its source, or its output when it has none);
        ``label`` names it in errors. ``cancel`` kills the worker when set; ``on_progress`` takes
        any ``progress`` message the job sends while it runs.

        ``frame``, when given, reads the job's one optional binary frame: it is called with the
        ``want`` message the moment that message arrives, and its bytes are returned under ``data``.
        Few jobs have one (the topology read does, ADR-0022 15.2); the frame is read here, between
        the header and ``done``, because reading it anywhere else would desynchronise the pipe.
        """
        if self._conn is None:
            self.start()
        deadline = time.monotonic() + timeout
        try:
            while not self._ready:
                message = self._next(deadline, cancel, path, label)
                if message.get("type") == "ready":
                    self._ready, self.ocp = True, _text(message, "ocp", 64)
            self._send(job)
            payload: dict[str, Any] | None = None
            while True:
                message = self._next(deadline, cancel, path, label)
                kind = message.get("type")
                if kind == "progress":
                    if on_progress is not None:
                        on_progress(*_check_progress(message))
                elif kind == want:
                    payload = message
                    if frame is not None:
                        payload = {**message, "data": frame(message)}
                elif kind == "done":
                    if payload is None:
                        raise GeometryWorkerError(f"the geometry worker sent no {want!r} result")
                    return payload
                elif kind == "error":
                    raise _error_from(message, path)
                else:
                    raise GeometryWorkerError(
                        f"the geometry worker sent an unknown message {kind!r}"
                    )
        finally:
            self.kill()

    def kill(self) -> None:
        """Stop the process if it still runs and close the pipe. Safe to call twice."""
        process, conn = self._process, self._conn
        if process is not None:
            if process.is_alive():
                process.kill()
            process.join(5)
        if conn is not None:
            conn.close()
        self._conn = None

    # ------------------------------------------------------------------ parent side
    def _send(self, message: dict[str, Any]) -> None:
        assert self._conn is not None
        try:
            self._conn.send_bytes(json.dumps(message).encode("utf-8"))
        except OSError as exc:
            raise self._died(None) from exc

    def _step(
        self,
        deadline: float,
        cancel: threading.Event | None,
        path: Path,
        on_read: ReadCallback | None,
        on_solid: SolidCallback | None,
        label: str | None = None,
    ) -> float | None:
        """Wait for and handle one message; the worker's seconds once it is done."""
        message = self._next(deadline, cancel, path, label)
        try:
            return self._handle(message, path, on_read, on_solid)
        except (EOFError, OSError) as exc:
            raise self._died(path, label) from exc

    def _next(
        self,
        deadline: float,
        cancel: threading.Event | None,
        path: Path,
        label: str | None = None,
    ) -> dict[str, Any]:
        """The next message; ``label`` names the job in errors (the import of ``path`` when
        None)."""
        conn = self._conn
        assert conn is not None
        what = label or f"the import of {path.name}"
        while True:
            if cancel is not None and cancel.is_set():
                self.kill()
                raise ImportCancelled(f"{what} was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.kill()
                raise GeometryWorkerError(f"{what} timed out", details={"path": str(path)})
            try:
                if conn.poll(min(_POLL_SECONDS, remaining)):
                    return self._recv_json()
            except (EOFError, OSError) as exc:
                raise self._died(path, label) from exc
            if not self.alive and not _readable(conn):
                raise self._died(path, label)

    def _recv_json(self) -> dict[str, Any]:
        assert self._conn is not None
        frame = self._conn.recv_bytes(MAX_JSON_BYTES)
        try:
            message = json.loads(frame.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GeometryWorkerError("the geometry worker sent a malformed message") from exc
        if not isinstance(message, dict):
            raise GeometryWorkerError("the geometry worker sent a malformed message")
        return message

    def _handle(
        self,
        message: dict[str, Any],
        path: Path,
        on_read: ReadCallback | None,
        on_solid: SolidCallback | None,
    ) -> float | None:
        kind = message.get("type")
        if kind == "ready":
            self._ready, self.ocp = True, _text(message, "ocp", 64)
        elif kind == "read":
            info = _read_info(message)
            if on_read is not None:
                on_read(info)
        elif kind == "solid":
            result = self._solid(message)
            if on_solid is not None:
                on_solid(result)
        elif kind == "done":
            return _number(message, "seconds")
        elif kind == "error":
            raise _error_from(message, path)
        else:
            raise GeometryWorkerError(f"the geometry worker sent an unknown message {kind!r}")
        return None

    def _solid(self, message: dict[str, Any]) -> SolidResult:
        assert self._conn is not None
        vertices, triangles = _int(message, "vertices"), _int(message, "triangles")
        faces, face_base = _int(message, "faces"), _int(message, "face_base")
        frames = [self._conn.recv_bytes(MAX_BUFFER_BYTES) for _ in range(4)]
        sizes = (vertices * 12, vertices * 12, triangles * 12, triangles * 4)
        if tuple(len(f) for f in frames) != sizes:
            raise GeometryWorkerError("the geometry worker sent buffers of the wrong size")
        positions = np.frombuffer(frames[0], _F4).reshape(vertices, 3)
        normals = np.frombuffer(frames[1], _F4).reshape(vertices, 3)
        indices = np.frombuffer(frames[2], _U4).reshape(triangles, 3)
        face_ids = np.frombuffer(frames[3], _U4)
        if triangles and (
            int(indices.max()) >= vertices
            or int(face_ids.min()) < face_base
            or int(face_ids.max()) >= face_base + faces
        ):
            raise GeometryWorkerError("the geometry worker sent indices out of range")
        mesh = TriMesh(
            _text(message, "name"),
            _text(message, "kind"),
            _color(message.get("color")),
            positions.astype(np.float32, copy=False),
            normals.astype(np.float32, copy=False),
            indices.astype(np.uint32, copy=False),
            face_ids.astype(np.uint32, copy=False),
            faces,
            face_base,
            _text(message, "pass"),
        )
        adjacency_count = _int(message, "adjacency")
        pair_frame = self._conn.recv_bytes(MAX_BUFFER_BYTES)
        cos_frame = self._conn.recv_bytes(MAX_BUFFER_BYTES)
        if len(pair_frame) != adjacency_count * 16 or len(cos_frame) != adjacency_count * 8:
            raise GeometryWorkerError("the geometry worker sent adjacency of the wrong size")
        adjacency: Adjacency | None = None
        if adjacency_count:
            pairs = np.frombuffer(pair_frame, _I8).reshape(adjacency_count, 2).astype(np.int64)
            cos = np.frombuffer(cos_frame, _F8).astype(np.float64)
            if int(pairs.min()) < 0 or int(pairs.max()) >= faces:
                raise GeometryWorkerError("the geometry worker sent adjacency out of range")
            adjacency = Adjacency(pairs, cos)
        return SolidResult(
            mesh.quality,
            _int(message, "index"),
            _int(message, "count"),
            mesh,
            _int(message, "untriangulated"),
            _number(message, "seconds"),
            adjacency,
        )

    def _died(self, path: Path | None, label: str | None = None) -> GeometryWorkerError:
        process = self._process
        code = None
        if process is not None:
            process.join(2)
            code = process.exitcode
        self.kill()
        where = f" while importing {path.name}" if path is not None else ""
        if label is not None:
            where = f" during {label}"
        shown = f"0x{code & 0xFFFFFFFF:08X}" if code is not None and abs(code) > 255 else code
        return GeometryWorkerError(
            f"the geometry worker stopped unexpectedly (exit code {shown}){where}",
            details={"exitcode": code, "path": str(path) if path else None},
        )


def _readable(conn: Connection) -> bool:
    try:
        return conn.poll(0)
    except (EOFError, OSError):
        return False


# ---------------------------------------------------------------------- warm workers
# Process-wide, because a worker belongs to no session; empty unless prewarm() is called.
_pool: list[GeometryWorker] = []
_pool_lock = threading.Lock()


def prewarm(count: int = 1) -> int:
    """Start workers until ``count`` (at most :data:`MAX_POOL`) wait for a job; returns how many."""
    with _pool_lock:
        _pool[:] = [w for w in _pool if w.alive]
        while len(_pool) < min(count, MAX_POOL):
            _pool.append(GeometryWorker().start())
        return len(_pool)


def acquire() -> GeometryWorker:
    """A started worker: a warm one when available, otherwise a new one."""
    with _pool_lock:
        while _pool:
            worker = _pool.pop(0)
            if worker.alive:
                return worker
    return GeometryWorker().start()


def shutdown_pool() -> None:
    with _pool_lock:
        workers = list(_pool)
        _pool.clear()
    for worker in workers:
        worker.kill()


def run_import(
    path: Path,
    *,
    workdir: Path,
    passes: Sequence[Quality] = PASSES,
    solids: Sequence[int] | None = None,
    units: str | None = None,
    on_read: ReadCallback | None = None,
    on_solid: SolidCallback | None = None,
    cancel: threading.Event | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    breps: dict[str, Any] | None = None,  # Any: JSON
) -> float:
    """Import ``path`` in a worker process; the callbacks run on the calling thread.

    ``units`` is the length unit for a format the file does not declare (BREP); ``breps`` asks
    the worker to cache each solid's B-rep (:meth:`GeometryWorker.run`)."""
    return acquire().run(
        path,
        workdir=workdir,
        passes=passes,
        solids=solids,
        units=units,
        on_read=on_read,
        on_solid=on_solid,
        cancel=cancel,
        timeout=timeout,
        breps=breps,
    )


# ---------------------------------------------------------------------- message checks
def _int(message: dict[str, Any], key: str) -> int:
    value = message.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GeometryWorkerError(f"the geometry worker sent a bad {key!r}")
    return value


def _number(message: dict[str, Any], key: str) -> float:
    value = message.get(key, 0.0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise GeometryWorkerError(f"the geometry worker sent a bad {key!r}")
    return float(value)


def _text(message: dict[str, Any], key: str, limit: int = 256) -> str:
    value = message.get(key, "")
    if not isinstance(value, str):
        raise GeometryWorkerError(f"the geometry worker sent a bad {key!r}")
    return value[:limit]


def _color(value: Any) -> Vec3:  # Any: message JSON
    if (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(c, int | float) and not isinstance(c, bool) for c in value)
    ):
        r, g, b = (min(1.0, max(0.0, float(c))) for c in value)
        return (r, g, b)
    return DEFAULT_COLOR


def _read_info(message: dict[str, Any]) -> ReadInfo:
    raw = message.get("solids")
    if not isinstance(raw, list):
        raise GeometryWorkerError("the geometry worker sent a bad solid list")
    solids = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise GeometryWorkerError("the geometry worker sent a bad solid list")
        color = entry.get("color")
        solids.append(
            SolidInfo(
                _int(entry, "index"),
                _text(entry, "name"),
                _text(entry, "kind"),
                None if color is None else _color(color),
                _int(entry, "faces"),
            )
        )
    warnings = _warnings(message)
    return ReadInfo(
        tuple(solids),
        _int(message, "faces"),
        _text(message, "units"),
        _number(message, "seconds"),
        warnings,
    )


def _warnings(message: dict[str, Any]) -> tuple[str, ...]:
    """The reader's notes from the read message: a short list of plain text, or none."""
    raw = message.get("warnings", [])
    if not isinstance(raw, list) or any(not isinstance(w, str) for w in raw):
        raise GeometryWorkerError("the geometry worker sent a bad warnings list")
    return tuple(w[:512] for w in raw[:16])


def _passes(passes: Sequence[Quality]) -> list[dict[str, Any]]:  # Any: JSON
    return [{"name": p.name, "linear": p.linear, "angular": p.angular} for p in passes]


def _progress(message: dict[str, Any]) -> tuple[str, float, str]:
    fraction = min(1.0, max(0.0, _number(message, "fraction")))
    return _text(message, "stage", 64), fraction, _text(message, "message", 256)


def _check_progress(message: dict[str, Any]) -> tuple[str, int, int]:
    """A check job's ``progress`` message: the part it is checking, its index and the count."""
    part = _text(message, "part", 64)
    if not part:
        raise GeometryWorkerError("the geometry worker sent check progress without a part")
    return part, _int(message, "index"), _int(message, "count")


def _id_map(value: Any, what: str) -> dict[str, dict[int, tuple[int, ...]]]:  # Any: JSON
    """A per-part id map from a worker message: ``{part: {input id: (output ids, ...)}}``.

    Used for the edge and vertex histories (T-056-fix), which travel beside the face one. A
    malformed
    map fails the whole message rather than being dropped: silently losing it is what made the
    review
    call the original behaviour out.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise GeometryWorkerError(f"the geometry worker sent a bad {what}")
    out: dict[str, dict[int, tuple[int, ...]]] = {}
    for input_part, mapped in value.items():
        if not is_id(str(input_part)) or not isinstance(mapped, dict):
            raise GeometryWorkerError(f"the geometry worker sent a bad {what}")
        try:
            out[str(input_part)] = {
                int(k): tuple(int(x) for x in v) for k, v in mapped.items() if isinstance(v, list)
            }
        except (TypeError, ValueError) as exc:
            raise GeometryWorkerError(f"the geometry worker sent a bad {what}") from exc
    return out


def _ids(value: Any, faces: int, what: str) -> tuple[int, ...]:  # Any: message JSON
    """A list of output face ids, each below ``faces``."""
    if not isinstance(value, list) or not all(
        isinstance(v, int) and not isinstance(v, bool) and 0 <= v < faces for v in value
    ):
        raise GeometryWorkerError(f"the geometry worker sent bad face ids in {what}")
    return tuple(value)


def _shape(message: dict[str, Any]) -> ShapeResult:
    """A ``shape`` message, checked: the worker read files, so it is not trusted."""
    index, count, faces = _int(message, "index"), _int(message, "count"), _int(message, "faces")
    part, basis = message.get("part"), message.get("basis")
    if index >= count or not (isinstance(part, str) and is_id(part)):
        raise GeometryWorkerError("the geometry worker sent a bad output")
    if not (isinstance(basis, str) and _BASIS_RE.match(basis)):
        raise GeometryWorkerError("the geometry worker sent a bad output version")
    flags = (message.get("valid"), message.get("closed"))
    if not all(isinstance(f, bool) for f in flags):
        raise GeometryWorkerError("the geometry worker sent a bad output check")
    box = message.get("bbox")
    if not isinstance(box, list) or len(box) not in (0, 6):
        raise GeometryWorkerError("the geometry worker sent a bad bounding box")
    bbox = tuple(_number({"v": v}, "v") for v in box)
    raw_history, raw_tags = message.get("history"), message.get("tags")
    if not isinstance(raw_history, dict) or not isinstance(raw_tags, dict):
        raise GeometryWorkerError("the geometry worker sent a bad history or tags")
    history: dict[str, dict[int, tuple[int, ...]]] = {}
    for input_part, mapped in raw_history.items():
        if not is_id(str(input_part)) or not isinstance(mapped, dict):
            raise GeometryWorkerError("the geometry worker sent a bad history")
        try:
            history[str(input_part)] = {
                int(k): _ids(v, faces, "the history") for k, v in mapped.items()
            }
        except ValueError as exc:
            raise GeometryWorkerError("the geometry worker sent a bad history") from exc
    # T-056-fix: the edge and vertex histories, validated the same way. Bounds are the output's own
    # edge and vertex counts when the message carries them, else the id validator's limits.
    history_edges = _id_map(message.get("history_edges"), "the edge history")
    history_vertices = _id_map(message.get("history_vertices"), "the vertex history")
    if len(raw_tags) > MAX_TAGS:
        raise GeometryWorkerError("the geometry worker sent too many tags")
    tags = {str(name)[:64]: _ids(ids, faces, "the tags") for name, ids in raw_tags.items()}
    report = message.get("report", {})
    return ShapeResult(
        index,
        count,
        part,
        basis,
        _text(message, "name", 128),
        faces,
        bool(flags[0]),
        bool(flags[1]),
        _number(message, "volume"),
        bbox,
        history,
        _text(message, "history_source", 16),
        tags,
        report if isinstance(report, dict) else {},
        history_edges,
        history_vertices,
    )


def _finished(message: dict[str, Any]) -> OperationRun:
    report = message.get("report", {})
    raw = message.get("warnings", [])
    if not isinstance(raw, list) or any(not isinstance(w, str) for w in raw):
        raise GeometryWorkerError("the geometry worker sent a bad warnings list")
    return OperationRun(
        _number(message, "seconds"),
        _text(message, "version", 64),
        report if isinstance(report, dict) else {},
        tuple(w[:512] for w in raw[:64]),
    )


_ERRORS: dict[str, type[GeometryError]] = {
    cls.code: cls
    for cls in (
        CadUnavailable,
        GeometryFileError,
        GeometryWorkerError,
        ImportCancelled,
        OperationFailed,
        OperationInvalid,
        MeshOnly,
        SeedOutside,
    )
}


def _error_from(message: dict[str, Any], path: Path, *, keep_path: bool = True) -> GeometryError:
    """The typed error a worker reported, with its details (bounded JSON) when it sent any, and
    the source path when the job had one."""
    cls = _ERRORS.get(str(message.get("code")), GeometryWorkerError)
    text = _text(message, "message", 2000) or "the geometry worker failed"
    details = message.get("details")
    found: dict[str, Any] = {}
    if isinstance(details, dict) and len(json.dumps(details)) <= MAX_DETAILS_BYTES:
        found = details
    return cls(text, details={**found, "path": str(path)} if keep_path else found)
