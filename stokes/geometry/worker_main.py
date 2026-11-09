"""The worker process's side of :mod:`stokes.geometry.worker`: read, tessellate, stream.

Runs in the spawned process only: :func:`child_main` imports OCP, says ``ready``, takes one job
and sends a ``read`` message, then the solids in batches (one STL write and few BRepMesh calls
serve many small solids), every chosen solid at each pass in turn, then ``done``. An import that
names a shape cache writes each solid's B-rep there just before its batch is first tessellated
(ADR-0021 decision 8.3), so the first parts still reach the viewport after one batch's writes.
An ``operation`` job (:func:`_operation_job`) loads its inputs through the shape provider, runs
the operation module, writes and announces each output, then tessellates the outputs as an
import tessellates solids. An ``export_step`` job (:func:`_export_job`) loads every part through
the same provider, so one call may mix source files and derived parts, and names each as its node
names it. A ``check_brep`` job (:func:`_brep_job`) loads its parts through the same provider, sends
one ``progress`` message per part and answers with the findings of each. A mesh-only source (STL,
OBJ, 3MF) takes :func:`_mesh_job` instead of :func:`_child_job`: one pass, no tessellation, and the
faces are the triangulation's regions between feature edges. The message layout is described in
:mod:`stokes.geometry.worker`.

**Large imports use helper processes** (:mod:`stokes.geometry.helpers`): they start before the
read, then tessellate the batches in parallel and the worker relays their frames, meshing a batch
itself if a helper fails. :func:`mesh_batch` is the one place a batch is meshed, so both produce
the same bytes.

Size: about 600 lines, kept whole: everything that runs inside the worker process is here, and
the five jobs share its readers, batches and message writers.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from collections.abc import Iterator, Sequence
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import numpy as np

from stokes.errors import ValidationError
from stokes.geometry import formats, readers, step
from stokes.geometry.errors import (
    CadUnavailable,
    GeometryError,
    GeometryFileError,
    GeometryWorkerError,
)
from stokes.geometry.mesh import DEFAULT_COLOR, U32, TriMesh, Vec3, vertex_normals, weld
from stokes.geometry.meshfile import MeshBody, split_faces, unit_scale
from stokes.geometry.step import Quality, StepSolid
from stokes.log import fields, get_logger

log = get_logger(__name__)

IDLE_TIMEOUT = 300.0
"""How long a started worker waits for its job before it exits."""
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_DETAILS_BYTES = 64 * 1024
"""An error's details travel with it (the candidates of ``geometry.seed_outside``) up to this."""
FIRST_BATCH_FACES = 250
BATCH_FACES = 2000


def _send_json(conn: Connection, message: dict[str, Any]) -> None:
    conn.send_bytes(json.dumps(message).encode("utf-8"))


def _details(details: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    """An error's details as JSON for the parent, or none when they are too large to send."""
    try:
        text = json.dumps(details, default=str)
    except (TypeError, ValueError):
        return {}
    if len(text) > MAX_DETAILS_BYTES:
        return {}
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


def _silence_stdout() -> None:
    """OCCT prints transfer statistics on standard output; the worker talks over its pipe."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(devnull, 1)
    except OSError:
        log.debug("could not silence the worker's standard output")
    finally:
        os.close(devnull)


def child_main(conn: Connection) -> None:
    """Entry point of the worker process."""
    _silence_stdout()
    try:
        try:
            import OCP
            import OCP.STEPCAFControl
        except ImportError as exc:
            message = {"type": "error", "code": CadUnavailable.code, "message": f"OCP: {exc}"}
            _send_json(conn, message)
            return
        version = str(getattr(OCP, "__version__", ""))
        _send_json(conn, {"type": "ready", "pid": os.getpid(), "ocp": version})
        if not conn.poll(IDLE_TIMEOUT):
            return
        job = json.loads(conn.recv_bytes(MAX_JSON_BYTES).decode("utf-8"))
        try:
            _dispatch(conn, job)
        except GeometryError as exc:
            message = {"type": "error", "code": exc.code, "message": exc.message}
            _send_json(conn, {**message, "details": _details(exc.details)})
        except Exception as exc:
            detail = "".join(traceback.format_exception_only(exc)).strip()
            message = {"type": "error", "code": GeometryWorkerError.code, "message": detail}
            _send_json(conn, message)
    except (EOFError, OSError):
        return  # the parent went away or cancelled the job
    finally:
        conn.close()


def _dispatch(conn: Connection, job: dict[str, Any]) -> None:
    """Run the job the parent asked for: an import (B-rep or mesh-only), a B-rep check, a STEP
    export, an operation, or an edge/vertex topology read (ADR-0022 §15.2)."""
    kind = str(job.get("kind", "import"))
    if kind == "check_brep":
        _brep_job(conn, job)
    elif kind == "export_step":
        _export_job(conn, job)
    elif kind == "operation":
        _operation_job(conn, job)
    elif kind == "topology":
        _topology_job(conn, job)
    elif formats.format_for(Path(str(job["path"]))).family == "mesh":
        _mesh_job(conn, job)
    else:
        _child_job(conn, job)


def _read_model(path: Path, units: str | None = None) -> Any:  # Any: StepModel
    """The source file as a model (STEP, IGES or BREP through :func:`readers.read_source`),
    mapping an OCCT failure like the import path does."""
    try:
        return readers.read_source(path, units=units)
    except GeometryError:
        raise
    except ValidationError:
        raise  # the caller's own units went in: say so, do not dress it as a read failure
    except Exception as exc:
        raise GeometryFileError(f"OCCT could not read {path.name}: {exc}") from exc


def _brep_job(conn: Connection, job: dict[str, Any]) -> None:
    """Report the B-rep findings of the parts the job's specs name.

    Every part is loaded through the shape provider, so one check may mix source files and the
    derived parts of operations (ADR-0021 decisions 5 and 8.3) and a part whose B-rep is cached is
    not read from its source file again. One ``progress`` message per part, then ``brep`` with the
    findings (face ids in the part's own numbering) and the job's source reads and cache hits.
    """
    start = time.perf_counter()
    from stokes.geometry.brep import check_brep
    from stokes.geometry.shapes import MM, ShapeProvider

    specs = [s for s in job.get("parts") or [] if isinstance(s, dict)]
    count = len(specs)
    provider = ShapeProvider()

    def progress(part: str, index: int, name: str) -> None:
        _send_json(
            conn,
            {
                "type": "progress",
                "stage": "check",
                "fraction": (index + 1) / count if count else 1.0,
                "message": f"checking {name}"[:256],
                "part": part,
                "index": index,
                "count": count,
            },
        )

    findings: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        part = str(spec.get("part") or "")
        name = str(spec.get("name") or part)[:128]
        progress(part, index, name)
        shape = provider.load(spec)
        found = check_brep([(index, name, shape)], unit_scale=MM)[0]
        findings.append({"part": part, "name": name, **found.to_json()})
    _send_json(
        conn,
        {
            "type": "brep",
            "parts": findings,
            "provider": {"source_reads": provider.source_reads, "cache_hits": provider.cache_hits},
            "seconds": time.perf_counter() - start,
        },
    )
    _send_json(conn, {"type": "done", "seconds": time.perf_counter() - start})


def _topology_cache_write(path: Path, value: Any, policy: str = "") -> bool:
    """Write the topology cache, and never let a failure cost the answer (A10).

    The cache is an accelerator: the record was just computed and is about to be sent, so a cache
    that cannot be written must cost only itself. Two realistic failures: an unwritable directory
    (`PermissionError`), and — on Windows — a concurrent reader holding the sidecar, which raises
    `WinError 32` when it is replaced. The previous version let both propagate out of the job, so a
    perfectly good topology was reported as a failure.

    Returns whether the write happened, which the job's own timing note can mention.
    """
    from stokes.geometry import topology_cachefile as cachefile

    try:
        cachefile.write_topology(path, value, policy)
    except (OSError, GeometryError) as error:
        # OSError covers PermissionError and WinError 32; **GeometryError** covers the
        # `GeometryFileError` the cache raises for a record it refuses (a bad basis, a part over the
        # size limit). `GeometryFileError` is a `StokesError`, NOT a `ValueError` — catching
        # `ValueError` here, as the first version did, let those refusals fail the whole job;
        # the defect A10 is about.
        #
        # `fields(...)` rather than a bare `extra=`: the log is JSON, and the bare form
        # dropped both the path and the message.
        log.warning(
            "the topology cache could not be written; the answer is unaffected",
            extra=fields(path=str(path), error=str(error)),
        )
        return False
    return True


def _topology_job(conn: Connection, job: dict[str, Any]) -> None:
    """Report one part's edge and vertex topology (ADR-0022 §15.2).

    The part is loaded through the shape provider, so a source file or a derived operation output
    is read the same way a B-rep check reads it. The topology is generated from the B-rep here in
    the worker (never on the request or UI thread) and written to the topology cache when the job
    names one. The answer is one header message and one frame of float32 edge points; the parent
    packs the two with :mod:`stokes.geometry.topology_transport`.

    A mesh-only part has no B-rep: the provider raises for it and the error names the part, which is
    the explicit refusal ADR-0022 15.1 asks for rather than invented ids.
    """
    start = time.perf_counter()
    from stokes.geometry.shapes import MM, ShapeProvider
    from stokes.geometry.topology_brep import part_topology, sampling_policy

    spec = job.get("part") or {}
    if not isinstance(spec, dict):
        raise GeometryFileError("the topology job needs a part specification")
    part = str(spec.get("part") or "")
    name = str(spec.get("name") or part)[:128]
    provider = ShapeProvider()
    shape = provider.load(spec)
    # The spec may leave `basis` empty; the provider derives it from the source stamp. Use what it
    # actually read or cached, so the record is keyed by the same basis the face ids and mesh use.
    basis = str(spec.get("basis") or provider.bases.get(part) or "")
    # The provider returns millimetres; part_topology converts to metres with `scale`.
    value = part_topology(shape, part=part, basis=basis, scale=MM)

    cache = job.get("cache")
    path = Path(str(cache)) if cache else None
    cached = path is not None and _topology_cache_write(path, value, sampling_policy())

    points = [edge.points for edge in value.edges]
    blob = (
        np.concatenate([np.ascontiguousarray(p, dtype="<f4") for p in points]).tobytes()
        if points
        else b""
    )
    _send_json(
        conn,
        {
            "type": "topology",
            "part": value.part,
            "name": name,
            "basis": value.basis,
            "scale": value.scale,
            "units": "m",
            "faces": int(value.faces),
            "edges": value.edge_json(),
            "vertices": value.vertex_json(),
            # v3: the sizes of things. **This is the THIRD place that names every field of a
            # PartTopology by hand** (with `topology_transport.pack_topology` and
            # `store._pack_topology_answer`), and all three had to be extended together: the field
            # added to the record and to the codec was still dropped here, so the endpoint served
            # `faces: 0, solids: 0` and Face-mode area was unreachable — measured, after the client
            # work was already done. Three writers of one shape is the defect; the tests now cover
            # each path, and consolidating them is worth a task of its own.
            "faceSizes": value.face_size_json(),
            "solids": value.solid_json(),
            "bytes": len(blob),
            "cached": cached,
            "provider": {"source_reads": provider.source_reads, "cache_hits": provider.cache_hits},
            "seconds": time.perf_counter() - start,
        },
    )
    if blob:
        conn.send_bytes(blob)
    _send_json(conn, {"type": "done", "seconds": time.perf_counter() - start})


def _export_job(conn: Connection, job: dict[str, Any]) -> None:
    """Write the parts the job's specs name, named as the node names say, to a STEP file in
    metres. Every part is loaded through the shape provider, so one export may mix source files
    and the derived parts of operations (ADR-0021 decision 8.3)."""
    start = time.perf_counter()
    out = Path(str(job["out"]))
    from stokes.geometry.brep import StepSolidOut, write_step
    from stokes.geometry.shapes import ShapeProvider

    specs = [s for s in job.get("parts") or [] if isinstance(s, dict)]
    names = [str(n)[:128] for n in job.get("names") or []]
    if len(names) != len(specs):
        raise GeometryWorkerError(f"the export job names {len(names)} parts but gives {len(specs)}")
    provider = ShapeProvider()
    chosen = [(name, provider.load(spec)) for name, spec in zip(names, specs, strict=True)]
    out.parent.mkdir(parents=True, exist_ok=True)
    written = write_step([StepSolidOut(name, shape) for name, shape in chosen], out)
    _send_json(
        conn,
        {
            "type": "exported",
            "path": str(out),
            "solids": written,
            "names": [name for name, _shape in chosen],
            "provider": {"source_reads": provider.source_reads, "cache_hits": provider.cache_hits},
            "seconds": time.perf_counter() - start,
        },
    )
    _send_json(conn, {"type": "done", "seconds": time.perf_counter() - start})


def _child_job(conn: Connection, job: dict[str, Any]) -> None:
    start = time.perf_counter()
    path = Path(str(job["path"]))
    workdir = Path(str(job["workdir"]))
    passes = [
        Quality(str(p["name"]), float(p["linear"]), float(p["angular"])) for p in job["passes"]
    ]
    from stokes.geometry import helpers

    # **Helpers start BEFORE the read**, so the seconds each takes to load OCP overlap the read
    # instead of following it (a 52 MB file reads for 26 s). A small file gets none.
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    pool = helpers.HelperPool.start(helpers.helper_count(size)) if size else None
    try:
        model = _read_model(path, job.get("units"))
        solids = model.solids
        _send_json(
            conn,
            {
                "type": "read",
                "solids": [
                    {
                        "index": i,
                        "name": s.name,
                        "kind": s.kind,
                        "color": None if s.color is None else list(s.color),
                        "faces": s.faces,
                    }
                    for i, s in enumerate(solids)
                ],
                "faces": model.faces,
                "units": model.units,
                "seconds": model.seconds,
                "warnings": list(model.warnings),
            },
        )
        workdir.mkdir(parents=True, exist_ok=True)
        wanted = None if job.get("solids") is None else {int(i) for i in job["solids"]}
        chosen = [(i, s) for i, s in enumerate(solids) if wanted is None or i in wanted]
        count = len(solids)
        cache = _BrepWriter.of(job, model)
        if pool is not None and pool.size and model.faces >= helpers.MIN_FACES:
            _mesh_with_helpers(conn, pool, chosen, passes, model, workdir, cache, count)
        else:
            for quality in passes:
                for batch in _batches(chosen):
                    if cache is not None and quality is passes[0]:
                        cache.write(batch)  # before the batch is meshed: no triangulation in it
                    _mesh_and_send(conn, batch, quality, passes, model, workdir, count)
    finally:
        if pool is not None:
            pool.close()
    _send_json(conn, {"type": "done", "seconds": time.perf_counter() - start})


def _spec_of(index: int, solid: StepSolid, quality: Quality) -> dict[str, Any]:
    """What tessellating one solid needs, as JSON: its place in the file, how it is named and
    coloured, and its deflections at ``quality`` (measured on the shape, which a helper does not
    have until it has read the batch)."""
    linear, angular = step.deflections(solid.shape, quality)
    return {
        "index": index,
        "name": solid.name,
        "kind": solid.kind,
        "color": None if solid.color is None else list(solid.color),
        "linear": linear,
        "angular": angular,
    }


def mesh_batch(
    conn: Any,  # Any: a Connection, or anything with send_bytes (a helper's frame sink)
    shapes: Sequence[Any],  # Any: OCP TopoDS_Shape
    specs: Sequence[dict[str, Any]],
    *,
    quality: str,
    unit: float,
    workdir: Path | None,
    count: int,
    final: bool,
    parallel: bool = True,
) -> None:
    """Tessellate one batch and send each solid, in order, to ``conn`` (:func:`send_solid`).

    The one place a batch is meshed, so a helper process and the worker itself produce the same
    bytes. ``final`` builds the face adjacency too, once for the batch while the meshes are warm; it
    rides the solid message and the parent writes it beside the mesh cache. Not fatal to the import.
    """
    began = time.perf_counter()
    bodies = [
        step.Body(
            shape,
            float(spec["linear"]),
            float(spec["angular"]),
            str(spec["name"]),
            str(spec["kind"]),
            None if spec["color"] is None else (*spec["color"],),
        )
        for shape, spec in zip(shapes, specs, strict=True)
    ]
    meshes = step.tessellate_bodies(
        bodies, quality=quality, unit=unit, workdir=workdir, parallel=parallel
    )
    share = (time.perf_counter() - began) / max(1, len(bodies))
    found = _adjacency_of(meshes) if final else [None] * len(meshes)
    for spec, mesh, adjacency in zip(specs, meshes, found, strict=True):
        send_solid(conn, mesh, quality, int(spec["index"]), count, share, adjacency)


def _mesh_and_send(
    conn: Connection,
    batch: Sequence[tuple[int, StepSolid]],
    quality: Quality,
    passes: Sequence[Quality],
    model: Any,  # Any: StepModel
    workdir: Path,
    count: int,
) -> None:
    """One batch meshed here, in the worker itself."""
    mesh_batch(
        conn,
        [s.shape for _, s in batch],
        [_spec_of(i, s, quality) for i, s in batch],
        quality=quality.name,
        unit=model.unit_scale,
        workdir=workdir,
        count=count,
        final=bool(passes) and quality.name == passes[-1].name,
    )


HELPER_BATCH_TIMEOUT = 600.0
"""How long a helper may take on one batch before the worker meshes it itself."""


def _mesh_with_helpers(
    conn: Connection,
    pool: Any,  # Any: stokes.geometry.helpers.HelperPool
    chosen: Sequence[tuple[int, StepSolid]],
    passes: Sequence[Quality],
    model: Any,  # Any: StepModel
    workdir: Path,
    cache: _BrepWriter | None,
    count: int,
) -> None:
    """Every pass of every batch meshed by the helpers, relayed to the parent in file order.

    All requests are queued up front (coarse for every batch, then fine), so the helpers never wait
    for the worker, and the replies are taken in the order a serial import would send them: the
    viewport still gets the first solids first. A batch a helper could not mesh is meshed here.
    The per-solid cache writes (two small files each, file-system bound) run on threads and a
    batch's are finished before its first solids are announced.
    """
    from concurrent.futures import ThreadPoolExecutor

    from stokes.geometry import helpers

    batches = list(_batches(chosen, helpers.BATCH_FACES))
    files: list[Path] = []  # every file written here, removed at the end
    shapes_files: list[Path] = []  # each batch's B-rep, shared by the passes
    writes: dict[int, Any] = {}
    ids: list[list[int]] = []
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="stokes-brep-cache") as writer:
        try:
            for position, quality in enumerate(passes):
                row: list[int] = []
                for number, batch in enumerate(batches):
                    if position == 0:
                        target = workdir / f"batch-{os.getpid()}-{number}.bin"
                        _write_batch(target, [s.shape for _, s in batch])
                        files.append(target)
                        shapes_files.append(target)
                        if cache is not None:
                            writes[number] = writer.submit(cache.write, batch)
                    # The solids' descriptions go in a file beside the batch, not in the request:
                    # a Windows pipe holds 4 KB, so a request of hundreds of names blocked the
                    # worker until a helper had read it, which stalled the first reply.
                    described = workdir / f"batch-{os.getpid()}-{position}-{number}.json"
                    described.write_text(
                        json.dumps([_spec_of(i, s, quality) for i, s in batch]), encoding="utf-8"
                    )
                    files.append(described)
                    row.append(
                        pool.submit(
                            {
                                "path": str(shapes_files[number]),
                                "bodies": str(described),
                                "quality": quality.name,
                                "unit": model.unit_scale,
                                "count": count,
                                "final": quality.name == passes[-1].name,
                            }
                        )
                    )
                ids.append(row)
            for position, quality in enumerate(passes):
                for number, batch in enumerate(batches):
                    if position == 0 and number in writes:
                        writes[number].result()  # its B-reps are in the cache before it is shown
                    outcome = pool.take(ids[position][number], HELPER_BATCH_TIMEOUT)
                    if outcome.ok:
                        for frame in outcome.frames:
                            conn.send_bytes(frame)
                        continue
                    log.warning(
                        "a helper could not mesh a batch; meshing it in the worker",
                        extra=fields(batch=number, why=outcome.message[:200]),
                    )
                    _mesh_and_send(conn, batch, quality, passes, model, workdir, count)
        finally:
            for target in files:
                target.unlink(missing_ok=True)


def _write_batch(target: Path, shapes: Sequence[Any]) -> None:  # Any: OCP TopoDS_Shape
    """The shapes of a batch, in order, as one binary B-rep a helper reads back."""
    from OCP.BinTools import BinTools

    BinTools.Write_s(step.compound_of(shapes), str(target))


def _adjacency_of(meshes: Sequence[TriMesh]) -> list[Any]:
    """Per-solid face adjacency of a tessellated batch, from one weld, or Nones on failure."""
    from stokes.geometry.mesh import PartMesh
    from stokes.geometry.topology import (
        adjacency_of,
        edges_of,
        facets_of,
        split_adjacency,
        surface_of,
    )

    try:
        parts = [PartMesh((mesh,)) for mesh in meshes]
        surface = surface_of(parts)
        return list(
            split_adjacency(surface, adjacency_of(surface, facets_of(surface), edges_of(surface)))
        )
    except Exception:  # the import must not fail because a side cache could not be built
        log.debug("face adjacency not built for a batch")
        return [None] * len(meshes)


def _batches(
    solids: Sequence[tuple[int, StepSolid]],
    batch_faces: int = BATCH_FACES,
) -> Iterator[list[tuple[int, StepSolid]]]:
    """Solids in file order, grouped so that one STL write serves many small ones.

    The first group is small, so the first solid reaches the viewport quickly. ``batch_faces`` is
    the size of the others; helpers use smaller ones so that every helper has batches to take.
    """
    batch: list[tuple[int, StepSolid]] = []
    faces = 0
    limit = min(FIRST_BATCH_FACES, batch_faces)
    for index, solid in solids:
        if batch and faces + solid.faces > limit:
            yield batch
            batch, faces, limit = [], 0, batch_faces
        batch.append((index, solid))
        faces += solid.faces
    if batch:
        yield batch


def send_solid(
    conn: Connection,
    mesh: TriMesh,
    pass_name: str,
    index: int,
    count: int,
    seconds: float,
    adjacency: Any = None,
) -> None:
    ids = mesh.face_ids if mesh.face_ids is not None else np.zeros(0, dtype=np.uint32)
    pairs = (
        np.zeros((0, 2), dtype="<i8")
        if adjacency is None
        else np.ascontiguousarray(adjacency.pairs, dtype="<i8")
    )
    cos = (
        np.zeros(0, dtype="<f8")
        if adjacency is None
        else np.ascontiguousarray(adjacency.cos, dtype="<f8")
    )
    _send_json(
        conn,
        {
            "type": "solid",
            "pass": pass_name,
            "index": index,
            "count": count,
            "name": mesh.name,
            "kind": mesh.kind,
            "color": list(mesh.color),
            "faces": mesh.faces,
            "face_base": mesh.face_base,
            "vertices": mesh.vertex_count,
            "triangles": mesh.triangle_count,
            "untriangulated": mesh.faces - int(np.unique(ids).size),
            "adjacency": int(pairs.shape[0]),
            "seconds": seconds,
        },
    )
    for array in (mesh.positions, mesh.normals, mesh.indices, ids):
        conn.send_bytes(memoryview(np.ascontiguousarray(array)).cast("B"))
    conn.send_bytes(pairs.tobytes())
    conn.send_bytes(cos.tobytes())


def _mesh_job(conn: Connection, job: dict[str, Any]) -> None:
    """Stream a mesh-only source (STL, OBJ or 3MF) as one solid per body, in one pass.

    There is nothing to tessellate: the triangles go over as read (welded per face, for sharp
    shading), split into faces at the feature angle (:func:`split_faces`) and scaled from the
    file's units into metres. One pass only, the last the job names.
    """
    start = time.perf_counter()
    path = Path(str(job["path"]))
    passes = [
        Quality(str(p["name"]), float(p["linear"]), float(p["angular"])) for p in job["passes"]
    ]
    pass_name = passes[-1].name if passes else "fine"
    model = readers.read_mesh(path, units=job.get("units"))
    unit = unit_scale(model.units)
    bodies: list[tuple[MeshBody, U32, int]] = []
    for body in model.bodies:
        ids = split_faces(body.positions, body.triangles)
        bodies.append((body, ids, int(ids.max()) + 1 if ids.size else 0))
    count = len(bodies)
    _send_json(
        conn,
        {
            "type": "read",
            "solids": [
                {
                    "index": index,
                    "name": body.name,
                    "kind": "mesh",
                    "color": None if body.color is None else list(body.color),
                    "faces": faces,
                }
                for index, (body, _ids, faces) in enumerate(bodies)
            ],
            "faces": sum(faces for _body, _ids, faces in bodies),
            "units": model.units,
            "seconds": time.perf_counter() - start,
            "warnings": list(model.warnings),
        },
    )
    wanted = None if job.get("solids") is None else {int(i) for i in job["solids"]}
    for index, (body, ids, faces) in enumerate(bodies):
        if wanted is not None and index not in wanted:
            continue
        began = time.perf_counter()
        mesh = _mesh_solid(body, ids, faces, unit, pass_name)
        send_solid(conn, mesh, pass_name, index, count, time.perf_counter() - began)
    _send_json(conn, {"type": "done", "seconds": time.perf_counter() - start})


def _mesh_solid(body: MeshBody, face_ids: U32, faces: int, unit: float, pass_name: str) -> TriMesh:
    """One mesh body as the parent's mesh: corners welded per face, positions in metres."""
    corners = body.positions[body.triangles].reshape(-1, 3)
    positions, indices = weld(corners, np.repeat(face_ids, 3))
    normals = vertex_normals(positions, indices)
    metres = (positions.astype(np.float64) * unit).astype(np.float32)
    color: Vec3 = body.color if body.color is not None else DEFAULT_COLOR
    return TriMesh(
        body.name, "mesh", color, metres, normals, indices, face_ids, faces, 0, pass_name
    )


class _BrepWriter:
    """The import's half of the shape cache: each solid's B-rep at the version the session will
    give its part (:func:`stokes.geometry.fingerprints.basis_for` of the parent's source stamp),
    under the id the part will have. A re-import names its parts; a first import of one solid
    is the placeholder's, of several the group's rule
    (:func:`stokes.geometry.shapes.solid_part_id`). A write the disk refuses is logged and
    skipped: the provider reads the source instead."""

    def __init__(
        self,
        folder: Path,
        stamp: Any,
        ids: dict[int, str],
        scale: float,
        units: str | None = None,
        source: Path | None = None,
    ) -> None:
        self.folder, self.stamp, self.ids, self.scale = folder, stamp, ids, scale
        self.units = units
        self.source = source

    @classmethod
    def of(cls, job: dict[str, Any], model: Any) -> _BrepWriter | None:  # Any: StepModel
        from stokes.geometry.files import SourceStamp
        from stokes.geometry.shapes import solid_part_id
        from stokes.model.ids import is_id

        spec = job.get("breps")
        if not isinstance(spec, dict) or not spec.get("dir"):
            return None
        stamp = SourceStamp.from_json(spec.get("stamp"))
        if stamp is None:
            return None
        named = spec.get("targets")
        top = spec.get("top")
        count = len(model.solids)
        ids: dict[int, str] = {}
        if isinstance(named, dict) and named:
            ids = {int(k): str(v) for k, v in named.items()}
        elif isinstance(top, str):
            ids = {i: top if count == 1 else solid_part_id(top, i) for i in range(count)}
        ids = {i: part for i, part in ids.items() if is_id(part)}
        return cls(
            Path(str(spec["dir"])),
            stamp,
            ids,
            float(model.unit_scale),
            str(model.units or ""),
            Path(str(spec["source"])) if spec.get("source") else None,
        )

    def write(self, batch: Sequence[tuple[int, StepSolid]]) -> None:
        from stokes.geometry.fingerprints import basis_for_source
        from stokes.geometry.shapes import brep_path, write_shape

        # **One resolver, shared with the store.** The basis written here must be the one the store
        # recomputes, or the two disagree and the part reads as "changed since it was imported".
        # `basis_for_source` is that single resolution; it applies `read_units`, so a STEP (whose
        # units are in its bytes) hashes them as absent while a BREP hashes the part's own —
        # exactly the rule the store follows.
        #
        # Two earlier versions were wrong here and both are worth remembering:
        #
        # 1. passing the model's `"mm"` for every format made a STEP re-read its source (its
        #    identity is its bytes, not the parameter);
        # 2. deriving the name from the SCALE — `unit_name(0.0254)` returns `"in"` while the reader
        #    and the node both say `"inch"` — so the worker hashed one string and the store another.
        #    Claude's defect 3: the code hashed `unit_name(scale)` while the comment claimed it used
        #    `read_units`. Calling the shared function makes that class of drift impossible.
        for index, solid in batch:
            part = self.ids.get(index)
            if part is None:
                continue
            basis = basis_for_source(
                self.source, self.stamp, index, solid.faces, self.units or None
            )
            try:
                write_shape(
                    brep_path(self.folder, part),
                    solid.shape,
                    part=part,
                    basis=basis,
                    scale=self.scale,
                )
            except (OSError, GeometryError) as exc:
                log.debug("shape cache not written", extra=fields(solid=index, error=str(exc)))


def _operation_job(conn: Connection, job: dict[str, Any]) -> None:
    """Run one operation: load the inputs (shape provider), run the module, then per output
    write its B-rep, send ``shape``, and tessellate the outputs as an import does."""
    import OCP

    from stokes.geometry import operations
    from stokes.geometry.errors import OperationFailed, OperationInvalid
    from stokes.geometry.operations.base import OpContext, OpInput
    from stokes.geometry.shapes import MM, ShapeProvider

    start = time.perf_counter()
    run = operations.load(str(job.get("op_type")))
    folder = Path(str(job["cache_dir"])) if job.get("cache_dir") else None
    if folder is None:
        raise OperationInvalid("the operation has no shape cache to write its outputs to")
    version = str(getattr(OCP, "__version__", ""))
    passes = [
        Quality(str(p["name"]), float(p["linear"]), float(p["angular"])) for p in job["passes"]
    ]

    def progress(stage: str, fraction: float, message: str = "") -> None:
        reply = {"stage": stage[:64], "fraction": fraction, "message": message[:256]}
        _send_json(conn, {"type": "progress", **reply})

    progress("read", 0.0, "reading the inputs")
    provider = ShapeProvider()
    specs = [s for s in job.get("inputs") or [] if isinstance(s, dict)]
    inputs = [
        OpInput(str(s["part"]), str(s.get("name") or s["part"]), provider.load(s)) for s in specs
    ]
    result = run(OpContext(dict(job.get("params") or {}), inputs, progress))
    if not result.outputs:
        raise OperationFailed("the operation made nothing")
    count = len(result.outputs)
    for index, output in enumerate(result.outputs):
        _send_json(conn, _shape_message(job, output, inputs, index, count, version, folder))
    workdir = Path(str(job["workdir"]))
    workdir.mkdir(parents=True, exist_ok=True)
    for quality in passes:
        progress("mesh", 0.0, f"{quality.name} mesh of {count} output(s)")
        began = time.perf_counter()
        bodies = [
            step.Body(o.shape, *step.deflections(o.shape, quality), o.name, "solid", None)
            for o in result.outputs
        ]
        meshes = step.tessellate_bodies(bodies, quality=quality.name, unit=MM, workdir=workdir)
        share = (time.perf_counter() - began) / count
        final = quality.name == passes[-1].name
        found = _adjacency_of(meshes) if final else [None] * count
        for index, (mesh, adjacency) in enumerate(zip(meshes, found, strict=True)):
            send_solid(conn, mesh, quality.name, index, count, share, adjacency)
    done = {"seconds": time.perf_counter() - start, "version": version}
    reads = {"source_reads": provider.source_reads, "cache_hits": provider.cache_hits}
    _send_json(
        conn,
        {
            "type": "done",
            **done,
            "report": {**result.report, "provider": reads},
            "warnings": [str(w)[:512] for w in result.warnings[:64]],
        },
    )


def _shape_message(
    job: dict[str, Any],
    output: Any,  # Any: OpOutput
    inputs: Sequence[Any],  # Any: OpInput
    index: int,
    count: int,
    version: str,
    folder: Path,
) -> dict[str, Any]:
    """Write output ``index``'s B-rep and describe it: its id, version, faces, check, the face
    history from the inputs and the faces it names for tags."""
    from stokes.geometry import operations
    from stokes.geometry.operations.base import check_shape
    from stokes.geometry.operations.faces import face_table, ids_of, map_history
    from stokes.geometry.shapes import MM, brep_path, write_shape

    part = operations.output_id(str(job["op"]), index)
    basis = operations.output_basis(str(job["seed"]), version, index)
    write_shape(brep_path(folder, part), output.shape, part=part, basis=basis, scale=MM)
    check = check_shape(output.shape)
    table = face_table(output.shape)
    history = map_history(output.history, inputs, output.shape)
    # T-056-fix: edges and vertices are carried beside faces. They were dropped silently before, so
    # a
    # tag holding an edge lost it even when the operation never touched it. Each kind is mapped with
    # the same walk, so an id means the same thing in all three.
    history_edges = map_history(output.history, inputs, output.shape, kind="edge")
    history_vertices = map_history(output.history, inputs, output.shape, kind="vertex")
    tags = {
        str(n)[:64]: ids_of(output.shape, faces, table=table) for n, faces in output.tags.items()
    }
    return {
        "type": "shape",
        "index": index,
        "count": count,
        "part": part,
        "basis": basis,
        "name": str(output.name)[:128],
        "faces": check["faces"],
        "valid": check["valid"],
        "closed": check["closed"],
        "volume": check["volume"],
        "bbox": check["bbox"],
        "history": {p: {str(k): v for k, v in m.items()} for p, m in history.items()},
        "history_edges": {p: {str(k): v for k, v in m.items()} for p, m in history_edges.items()},
        "history_vertices": {
            p: {str(k): v for k, v in m.items()} for p, m in history_vertices.items()
        },
        "history_source": "occt" if output.history is not None else "same",
        "tags": tags,
        "report": {**output.report, "check": check},
    }
