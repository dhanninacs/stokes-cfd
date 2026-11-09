"""The FastAPI application: static front end, geometry endpoint, WebSocket RPC.

Security (docs/engineering/security.md): binds to loopback only, every API and
WebSocket call carries the per-session token.

Geometry: ``/api/geometry/demo`` is the synthetic sample part (transport version 1);
``/api/geometry/{node_id}`` is one part node's mesh (one solid) from the session's geometry
store (version 2, with face ids), 404 while there is none; ``/api/geometry/batch`` serves up to
``MAX_BATCH_IDS`` of them in one binary reply (ADR-0019). The first WebSocket client
activates the store, which then loads cached meshes or re-imports the project's parts, and
binds it to the event loop, where imports build their groups of parts between commands. The
activation waits until that client is registered with the hub, so the events it starts with
(``geometry.part_ready`` from the cache, the first ``geometry.import_progress``) reach it.

Mesh: ``/api/mesh/{node_id}``, ``/api/mesh/edges/{node_id}`` and ``/api/mesh/batch`` serve the
``mesh_patch`` nodes of the mesh result from its cached surfaces (:mod:`stokes.server.mesh_routes`).

Upload (G1): ``POST /api/upload?name=...`` streams a browser's CAD file to the project's (or the
session's scratch) ``imports/`` folder and answers the absolute path, size and sha256 that
``geometry.import`` then takes.

Compression: replies are gzipped (level ``GZIP_LEVEL``) for a client that is not on loopback and
accepts it, and sent as they are to a client on loopback, which is where the desktop host always is
(ADR-0019 decision 5, decisions log G8).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import secrets
import struct
import tempfile
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from stokes import __version__
from stokes.api import Session
from stokes.build_info import build_info
from stokes.geometry.errors import MeshFormatError
from stokes.log import get_logger
from stokes.run.artifacts import ArtifactError
from stokes.run.store import SliceBusy
from stokes.server.mesh_routes import add_mesh_routes
from stokes.server.rpc import RpcHub

#: The shape of a `hashlib` object: `update` and `hexdigest` are all this code uses, and `hashlib`
#: publishes no class for it. A Protocol rather than the private `_Hash`, so a change inside hashlib
#: cannot break the annotation.
HashLike = Any

log = get_logger(__name__)

if TYPE_CHECKING:
    from stokes.geometry.store import GeometryStore
    from stokes.mesh.service import MeshService
    from stokes.run.store import RunStore

DEFAULT_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


def new_token() -> str:
    return secrets.token_urlsafe(24)


NO_MESH = 0xFFFFFFFF
"""Payload length in a batch frame that marks a part with no mesh yet."""

MAX_BATCH_IDS = 512
"""Ids one ``/api/geometry/batch`` request may name; more are refused with 413.

The viewport asks for 256 at a time (``BATCH_SIZE`` in web/src/viewport/parts.ts). The cap bounds
what one request can make the server read and hold: about 4 MB of meshes for the 4,500-part
benchmark, rather than everything a crafted URL lists.
"""

MAX_UPLOAD_BYTES = 1024**3

#: Threads that read the cache files of one ``/api/geometry/batch`` request at once.
BATCH_READERS = 8

#: How much of an upload is collected before a worker thread writes it (Opus review item 8).
#: **The trade is a thread hop against the work it carries.** A hop per request block (64 KiB by
#: default) costs more than the write, so blocks are combined up to this size; 4 MiB is four hops for
#: a 16 MiB file and a handful for a large one, while keeping peak extra memory small.
WRITE_BATCH_BYTES = 4 * 1024 * 1024

#: How many `name`, `name-2`, ... a single upload will try before giving up (Opus review item 9).
#: A bound, because a folder holding thousands of `part-N.step` files must produce an answer rather
#: than a spin; the same number the `exists()` loop it replaces would have walked.
LINK_ATTEMPTS = 1000

#: The longest stem an upload's name may have (Opus review item 17). The stored name is the stem plus
#: the suffix plus a possible `-999`, and `NAME_MAX` is 255 bytes - so this is far inside the
#: filesystem's limit while leaving the collision suffix room.
MAX_UPLOAD_STEM_CHARS = 100

"""The largest CAD file ``POST /api/upload`` accepts, in bytes (1 GiB).

The same number as ``stokes.geometry.files.MAX_SOURCE_BYTES``, which ``check_source_file``
applies to a source file and the import worker then reads: an upload above it would be written
and refused by the import, so the route refuses it first. ``tests/test_upload_route.py`` pins
the two together rather than importing 47 ms of geometry at server start-up."""

GZIP_LEVEL = 6
"""Compression level of the replies that are compressed, set rather than left to Starlette's
default of 9.

Mesh payloads are float arrays and compress about nine to one. Measured 2026-10-04 on a 256-part
reply of the 4,500-part benchmark (1.47 MB): level 6 gives 0.159 MB in 85 ms, level 9 gives
0.154 MB in 580 ms, which over the whole set was most of the 8.8 s the transfer took. Starlette
compresses large bodies on a worker thread, not the event loop.

Only a client that is not on loopback gets them (``SkipGzipOnLoopback``). On loopback the bytes
are free and the compression is not: the 4,500 parts took 13.5 s cold and 1.73 s warm for 2.82 MB
with gzip, and 7.9 s and 0.23 s for 26.2 MB without (tools/bench_viewport.py, 2026-10-04), and the
desktop host is always on loopback.
"""


def is_loopback(host: str | None) -> bool:
    """Whether ``host`` is a loopback address: 127.0.0.0/8, ``::1``, or ``::ffff:`` and a 127 one.

    A host this cannot place (none at all, a name, the test client's ``testclient``) is not
    loopback: it is treated as remote, which costs CPU and never correctness.
    """
    if host is None:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SkipGzipOnLoopback:
    """Keeps the gzip middleware behind it from compressing a reply to a loopback client.

    It takes ``Accept-Encoding`` out of the request, so ``GZipMiddleware`` sees a client that does
    not offer gzip and leaves the reply alone. It has to be the outer of the two.

    The address is the one the server reports for the connection (uvicorn takes it from
    ``X-Forwarded-For`` when a proxy on 127.0.0.1 sends one). This decides speed, not access: a
    wrong address costs a reply sent in the other form and nothing else; the token is what guards
    the API.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        client = scope.get("client")
        if scope["type"] == "http" and client is not None and is_loopback(client[0]):
            headers = [
                (name, value) for name, value in scope["headers"] if name != b"accept-encoding"
            ]
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def _batch_frame(node_id: str, payload: bytes | None) -> bytes:
    """One part of a ``/api/geometry/batch`` reply. See the route for the layout.

    ``0xFFFFFFFF`` marks a part that has no mesh yet, which is distinct from a zero-length
    mesh and from a missing entry.
    """
    raw_id = node_id.encode("utf-8")
    if payload is None:
        return struct.pack("<I", len(raw_id)) + raw_id + struct.pack("<I", NO_MESH)
    return struct.pack("<I", len(raw_id)) + raw_id + struct.pack("<I", len(payload)) + payload


def _add_geometry_routes(
    app: FastAPI,
    store_of: Callable[[], GeometryStore],
    check: Callable[[Request], None],
) -> None:
    """Register the geometry routes. Split out to keep ``create_app`` readable."""

    @app.get("/api/geometry/topology/{name}")
    def geometry_topology(name: str, request: Request) -> Response:
        """One part's edge and vertex topology (ADR-0022 §15.2).

        Registered before ``/api/geometry/{name}`` so this path is not read as a part named
        "topology". The response is the versioned topology payload of
        :mod:`stokes.geometry.topology_transport`: a JSON header, then the edges' float32 polylines.
        A part with no B-rep (a mesh-only import, or a derived part whose operation has not run yet)
        is a 404, and so is an unknown id: the client shows "no edges" rather than inventing them.
        """
        check(request)
        payload = store_of().topology(name)
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown geometry topology")
        return Response(payload, media_type="application/octet-stream")

    @app.get("/api/geometry/batch")
    def geometry_batch(request: Request, ids: str = "") -> Response:
        """Batch geometry endpoint: serves many meshes in one binary response.

        Query: ``ids=id1,id2,...,idN``, comma-separated node ids, at most ``MAX_BATCH_IDS`` of
        them (413 above). A repeated id is answered once, in the place it first appears. An id
        that is unknown, or not an id at all, gets the no-mesh marker, as a part without a mesh
        does: the single route answers both with 404.

        Framing, all integers little-endian::

            uint32   count
            repeat count times:
              uint32  byte length of the node id
              bytes   the node id (utf-8)
              uint32  byte length of the mesh payload, or 0xFFFFFFFF when there is none
              bytes   the mesh payload

        Binary rather than JSON because the meshes are float data: for the 4,500-part
        benchmark they are 26 MB, which as JSON with base64 became 35 MB the browser had
        to parse as text and then decode -- about 14 s of the 23.6 s it took to open that
        project. Slicing the ArrayBuffer skips that entirely.

        The framing describes the body. It is sent as it is to a client on loopback, as the
        desktop host is, and gzipped (``GZIP_LEVEL``) to a remote client that accepts gzip;
        see ``SkipGzipOnLoopback``.

        ``_batch_frame`` here and ``readBatch`` in ``web/src/api/client.ts`` are two halves
        of one format; change them together.
        """
        check(request)
        wanted = [nid for nid in (part.strip() for part in ids.split(",")) if nid]
        if len(wanted) > MAX_BATCH_IDS:
            raise HTTPException(status_code=413, detail=f"at most {MAX_BATCH_IDS} ids per request")
        unique = list(dict.fromkeys(wanted))
        store = store_of()  # sync route: a worker thread, not the loop
        frames = [struct.pack("<I", len(unique))]
        # **Cache files are read in parallel**: a cold part costs one file open, which on Windows is
        # file-system latency (about 11 ms each with the virus scanner), not CPU, so eight readers
        # overlap it. `transport` is safe to call from several threads (the store claims each part
        # before reading it) and `map` keeps the ids in order.
        with ThreadPoolExecutor(max_workers=BATCH_READERS) as pool:
            payloads = list(pool.map(store.transport, unique))
        frames.extend(
            _batch_frame(nid, payload) for nid, payload in zip(unique, payloads, strict=True)
        )
        return Response(
            content=b"".join(frames),
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/geometry/{name}")
    def geometry(name: str, request: Request) -> Response:
        check(request)
        if name == "demo":
            from stokes.geometry.sample import sample_part
            from stokes.server.geometry import pack_meshes

            return Response(pack_meshes(sample_part()), media_type="application/octet-stream")
        payload = store_of().transport(name)  # sync route: a worker thread, not the loop
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown geometry")
        return Response(payload, media_type="application/octet-stream")


def add_upload_routes(
    app: FastAPI,
    session_of: Callable[[], Session],
    check: Callable[[Request], None],
) -> None:
    """Register ``POST /api/upload`` (G1): a browser hands us a CAD file, we hand back a path.

    ``geometry.import {path}`` takes a path on the **server**, which a browser cannot produce. The
    bytes are streamed to ``<imports>/<name>`` and the reply carries the absolute path, its size and
    its sha256, so the client calls ``geometry.import`` with exactly what this route answered.

    Query ``name`` is the client's file name and is untrusted: it is sanitised to
    ``[A-Za-z0-9._-]``, stripped of every path part and of a leading dot, and the **resolved final
    path is checked to be inside the imports folder**, so a name can never escape it. A name already
    taken gets ``-2``, ``-3`` ... before its extension.

    **Two different refusals, because a client acts on them differently**: a name with nothing
    usable left (``""``, ``".."``, ``"///"``) is a 400, while a name that is merely missing a CAD
    suffix (``notes.txt``, ``noextension``, ``a/``) is a 415. Emptiness is decided first. The
    imports folder itself is refused when it is a symlink or not a directory (400), since a link
    there would move every upload out of the project with the name check still passing. The suffix
    must be one ``stokes.geometry.formats.FORMATS`` lists, case-insensitive.

    The body is never held in memory: it is read in chunks and written to a ``.part`` file, which is
    ``replace()``d only when it is complete, so an aborted upload leaves nothing importable. The cap
    is ``MAX_UPLOAD_BYTES``, checked against ``Content-Length`` first (413 without reading a byte)
    and then against the running total, because a client can lie about the header.

    ``check(request)`` first, as every route here does: 401 without the token. ``Cache-Control:
    no-store``.

    Async, unlike the other routes: the body arrives on the event loop and is streamed to disk in
    small writes, which do not block it the way reading a 1 GiB body into memory would.
    """
    import hashlib

    from fastapi.responses import JSONResponse

    @app.post("/api/upload")
    async def upload(request: Request, name: str = "") -> Response:
        """One CAD file, streamed to the imports folder of the current project."""
        check(request)
        from stokes.geometry.formats import FORMATS

        # Bare, because the split below has already taken the dot off the name.
        allowed = {suffix.lstrip(".") for fmt in FORMATS for suffix in fmt.suffixes}
        target = _upload_name(name, allowed)  # 400 or 415 here, before a byte is read

        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                raise HTTPException(status_code=400, detail="a bad Content-Length") from None
            if length > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"at most {MAX_UPLOAD_BYTES} bytes per upload",
                )

        folder = imports_dir(session_of())
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"cannot use {folder}: {exc}") from exc
        # **A symlinked `imports/` would move every upload out of the project** while the name check
        # below still passed, because the target helper resolves the folder first and the link's
        # target becomes the root. Security (CLAUDE.md 4): "refuse symlinks that escape". The
        # folder is Stokes's own, so a link there is never something a user meant.
        if folder.is_symlink() or not folder.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"{folder} is not a plain folder; remove it and upload again",
            )

        # **The name is RESERVED, not merely chosen** (Opus review item 9). The old helper asked
        # `exists()` and returned a free name, which cannot hold it: two uploads of `part.step` in
        # flight together both picked `part.step` - measured - and the second write replaced the
        # first, so the first reply's `path` and `sha256` named other bytes and its import read the
        # wrong geometry. `os.link` fails when the name exists, so the reserve and the check are one
        # operation and a retry picks the next name.
        digest = hashlib.sha256()
        total = 0
        # A `.part` name in the same folder, so the rename is atomic within one filesystem.
        # `mkstemp` gives a file nothing else can be reading; the descriptor is closed straight
        # away and the path reopened in the `with` below, so one `with` owns the handle.
        fd, part_name = tempfile.mkstemp(dir=folder, prefix=f".{target}.", suffix=".part")
        os.close(fd)
        part = Path(part_name)
        try:
            total = await _receive_to_part(request, part, digest)
            # **Link, then unlink: the reserve is atomic.** `os.link` refuses a name that exists, so
            # two uploads cannot both take one - the loser retries with the next name while the
            # winner's bytes are already safe. A `replace` here would overwrite silently, which is
            # the defect. Only after the link does the `.part` go, so a crash in between leaves the
            # final file intact and an orphan `.part`, never a half-written import.
            final = await asyncio.to_thread(_link_into_place, part, folder, target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return JSONResponse(
            {"path": str(final), "size": total, "sha256": digest.hexdigest()},
            headers={"Cache-Control": "no-store"},
        )


def _upload_name(name: str, allowed: set[str]) -> str:
    """The file name an upload is stored under; 415 when its suffix is not a CAD one.

    The name is untrusted. It is first reduced to its **last path component**, so no separator and
    no ``..`` segment survives (``../../x.stl`` becomes ``x.stl``); then every character outside
    ``[A-Za-z0-9._-]`` becomes ``_``; then a leading dot is dropped, which refuses dot-files and an
    empty stem. Emptiness is decided **before** the suffix, so a name of pure punctuation is the
    400 ``the name has no usable characters`` rather than a 415, and ``notes.txt`` is the 415.

    ``allowed`` is a set of bare suffixes without the dot (``{"stl", ...}``), built from
    ``stokes.geometry.formats.FORMATS`` so the allow-list cannot drift from the importers'.
    """
    from fastapi import HTTPException

    # Trailing separators go first, so `a/` reads as the name `a` (a suffix fault, 415) rather
    # than as an empty leaf (a name fault, 400).
    leaf = name.replace("\\", "/").rstrip("/").rpartition("/")[2]
    cleaned = "".join(c if (c.isascii() and (c.isalnum() or c in "._-")) else "_" for c in leaf)
    cleaned = cleaned.lstrip(".").rstrip(".")
    # Emptiness first: a name of punctuation is a 400, a name that merely lacks a CAD suffix is a
    # 415. The two are different faults and a client acts on them differently.
    if not cleaned:
        raise HTTPException(status_code=400, detail="the name has no usable characters")
    stem, dot, suffix = cleaned.rpartition(".")
    if not dot or not stem or not suffix:
        raise HTTPException(status_code=415, detail="the file name has no known CAD suffix")
    # **A cap, so a long name is a 400 rather than an unhandled 500** (Opus item 17). Measured: a
    # 400-character stem reached `mkstemp` and raised `ENAMETOOLONG`, which the route does not catch -
    # so the client got `Internal Server Error`, which says the server is broken rather than that the
    # name is too long. A paste can produce this; it is not a hostile input.
    #
    # **The limit is the FILESYSTEM's, not ours.** `NAME_MAX` is 255 bytes on ext4 and most others, and
    # the stored name is the stem plus `.stl` plus, at worst, `-999` - so the stem is capped well
    # inside it. 100 is the report's number and leaves room for all three.
    if len(stem) > MAX_UPLOAD_STEM_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"the file name is longer than {MAX_UPLOAD_STEM_CHARS} characters",
        )
    if suffix.lower() not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"cannot import a .{suffix} file; "
            f"known: {', '.join('.' + s for s in sorted(allowed))}",
        )
    return f"{stem}.{suffix.lower()}"


def imports_dir(session: Session) -> Path:
    """Where an upload lands: ``<project folder>/imports``, or the session's scratch ``imports``.

    This is the rule :func:`stokes.run.store.runs_dir` follows: **the project folder when the
    project has been saved**, so the imports travel with it, and otherwise a per-session
    ``tempfile.mkdtemp`` created once and kept on the session (`_upload_scratch`), so every upload
    of an unsaved project shares one folder rather than leaking a directory per request.

    **One difference from `runs_dir`, stated rather than hidden**: the scratch folder is its own,
    not the run store's, because `runs_dir`'s docstring is explicit that "the scratch folder is
    created per store, not shared" — two unsaved projects in one process must not see each other's
    files. A first `project_saved` copies the *geometry* scratch (B-reps, surfaces, fields) but not
    this one, so an upload made before the first save stays in the scratch and the model refers to
    it by absolute path; a file the user picked is not silently relocated under them.
    """
    # **One answer, because a project always has a folder** (claude-win2, reading 1, binding). The
    # per-session `tempfile.mkdtemp` that used to serve an unsaved project put uploads in `/tmp`,
    # where they did not travel with the project on Save as and were not obviously the user's.
    if session.project_path is None:
        from stokes.errors import StokesError

        raise StokesError(
            "this session has no project, so it has nowhere to put imports; "
            "open or create a project first",
            code="geometry.no_project",
        )
    return Path(session.project_path) / "imports"


@contextlib.contextmanager
def _answering_busy_and_broken() -> Iterator[None]:
    """Turn the two store failures into their answers, **in one place rather than per route**.

    * **`SliceBusy` -> 503 with `Retry-After`**: the server is at its cut limit.
    * **`ArtifactError` -> 500**: an artifact that does not match its digest is the HOST's or the
      transport's failure, not the client's, so a 4xx would send a caller to fix a fine request.

    Both were once unhandled on at least one route, and a third route's copy is what this replaces -
    three chances to forget one is three too many.
    """
    try:
        yield
    except SliceBusy as exc:
        raise _slice_busy(exc) from exc
    except ArtifactError as exc:
        raise _artifact_failed(exc) from exc


def _artifact_failed(exc: Exception) -> HTTPException:
    """**500, and deliberately not a 4xx** (Opus L5). An artifact that does not match its digest, or
    arrives short, is the HOST's or the TRANSPORT's failure - not the client's. A 4xx would tell a
    caller to fix a request that was fine.

    It was previously **unhandled**, so it fell through to FastAPI's default: a 500 with a traceback
    rather than a stated cause. The code is carried through in the detail so a caller can branch on
    it without matching on a message.
    """
    code = getattr(exc, "code", "run.artifact")
    return HTTPException(status_code=500, detail=f"{code}: {exc}")


def _plane_query(request: Request) -> str | None:
    """The raw `?plane=` value, or **None when the parameter is not there at all**.

    FastAPI cannot make this distinction with a `str = ""` default: `?plane=` and no parameter both
    arrive as `""`. Reading the query directly is what lets an empty value be a 400 (Opus L5) while
    an absent one still means "the run's default cut".
    """
    return request.query_params.get("plane")


def _plane_of(raw: str | None, target: str | None = None) -> dict[str, Any] | None:
    """`?plane=` as a canonical plane, or None when the parameter is ABSENT.

    **Absent is not the same as empty and not the same as invalid** (Opus L5). No parameter means
    "the run's own default cut", which is what every client sent before planes existed; an empty
    or malformed one is a client fault and a **400**, because silently serving the default answers a
    question nobody asked.

    **`None` is the absent case, not `""`.** With a default of `""` FastAPI gives the SAME value for
    `?plane=` and for no parameter at all, so the distinction this docstring promises could not be
    made - measured: both returned None, and `?plane=` quietly served the default. The route passes
    the raw query value, or None when it is not there.

    `target` is checked here rather than in the route: **a plane applies to the slice and to
    nothing else.** On a named patch it is a request for a patch that is not a patch, and
    ignoring it would look exactly like a plane that did nothing.
    """
    from stokes.run.planes import parse_plane

    if raw is None:
        return None
    if raw.strip() == "":
        # **Present but empty**, which is a client fault rather than "use the default".
        raise HTTPException(
            status_code=400,
            detail="?plane= was given with no value; omit it for the run's default cut",
        )
    if target is not None and target != "slice":
        raise HTTPException(
            status_code=400, detail="a plane applies to the slice; a patch has its own geometry"
        )
    try:
        return parse_plane(raw)
    except MeshFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _slice_busy(exc: Exception) -> HTTPException:
    """`SliceBusy` as a **503 with `Retry-After`** (Opus M4).

    **503, not 500 and not 404.** The request is not malformed and the plane is not missing - the
    server is at its cut limit, and `Retry-After` is the header that says so. A client told `500`
    retries blindly; one told `404 slice_empty` moves the plane, which is the wrong action entirely.
    """
    return HTTPException(
        status_code=503,
        detail={
            "code": "results.busy",
            "message": str(exc) or "the server is busy cutting planes",
        },
        headers={"Retry-After": str(getattr(exc, "retry_after", 5))},
    )


def _slice_missing(store: Any, run_id: str, plane: dict[str, Any] | None) -> HTTPException:
    """The right 404 for a plane that could not be served, with the contract's code.

    **Three different answers, and a client acts on them differently**: a plane that misses the
    mesh is worth moving, one whose host is gone will not improve by retrying, and a run with no
    fields at all is not about the plane.
    """
    # **`slice_state` is asked WITHOUT cutting again** (Opus M4). It used to call
    # `slice_artifact_for`, which cuts on demand - so a request that had just failed to fetch paid
    # for a SECOND cut, each waiting `CUT_SLICE_TIMEOUT_S` = 300 s against a slow host. One request,
    # two cuts, 600 s of a held worker thread. The route has already tried; this only asks what the
    # store knows.
    state = store.slice_state(run_id, plane, cut=False)
    detail = {
        "empty": ("results.slice_empty", "the plane misses the model"),
        "no_host": (
            "results.host_unavailable",
            "the plane is not cached and the run's host cannot be reached to cut it",
        ),
        "ok": ("results.slice_empty", "this run has no slice"),
    }.get(state, ("results.slice_empty", "this run has no slice"))
    return HTTPException(status_code=404, detail={"code": detail[0], "message": detail[1]})


def checked_run_id(raw: str) -> str:
    """`raw` as a run id, or a 400. **The one rule** every results route applies to that parameter."""
    from stokes.model.ids import ID_RE

    if not ID_RE.match(raw):
        raise HTTPException(status_code=400, detail=f"{raw!r} is not a run id")
    return raw


def _report_bytes(store: object, run_id: str, record: object, project: str) -> bytes:
    """The run's report as an HTML file, through the same builder `run.report` uses.

    **One builder, two ways to reach it**: the command writes the file beside the cache for a client on
    the host, and this route hands the same bytes to a browser. A second builder would be a second
    answer to "what does the report say", which is the defect the iteration count already taught.
    """
    from stokes.post.report import report_from_run, report_html

    status = store.fields_status(run_id)  # type: ignore[attr-defined]
    report = report_from_run(
        # **The caller's project name**: `RunStore` knows runs, not the project, and a `getattr`
        # fallback that says "Stokes" put a placeholder in the report's first row.
        project=project,
        run_id=run_id,
        record=record,
        fields={"fields": status.get("fields") or []},
        residuals=store.residuals(run_id),  # type: ignore[attr-defined]
    )
    return report_html(report).encode("utf-8")


def _derived_payload(
    derived: Callable[[str, str, str, dict[str, Any] | None], tuple[Any, str]] | None,
    run_id: str,
    target: str,
    field: str,
    plane: dict[str, Any] | None,
) -> bytes | None:
    """A field function or an expression, in the same codec, for a name the cache lacks.

    **Its own range, not the run's**: a derived field has no global range in the header, so
    the payload's ends are its values' on this surface. None when there is no calculator.
    """
    if derived is None:
        return None
    import numpy as np

    from stokes.errors import StokesError
    from stokes.run.fields import field_payload

    try:
        values, units = derived(run_id, target, field, plane)
    except StokesError as exc:
        raise HTTPException(status_code=400, detail=exc.message or str(exc)) from exc
    finite = values[np.isfinite(values)]
    low = float(finite.min()) if finite.size else 0.0
    high = float(finite.max()) if finite.size else 0.0
    clean = np.where(np.isfinite(values), values, low).astype(np.float32)
    return field_payload(clean, field, units, low, high)


def add_results_routes(
    app: FastAPI,
    store_of: Callable[[], RunStore],
    check: Callable[[Request], None],
    derived: Callable[[str, str, str, dict[str, Any] | None], tuple[Any, str]] | None = None,
) -> None:
    """Register the three results routes, most specific first (T-216).

    ``/api/results/field/{run_id}/{target}?field=p`` is one ``(patch, field)`` in the client's field
    codec; ``target`` is a ``mesh_patch`` node id or the word ``slice``. ``/api/results/slice/{id}``
    is the slice as transport version 2 and ``/api/results/slice-edges/{id}`` its cell edges in the
    T-210 edge codec — the same two payload shapes the mesh routes serve, so the viewport has one
    reader for patches and slices alike.

    **Every path parameter is validated against ``ID_RE`` (or the literal ``slice``) before any
    lookup**, which is what keeps a path parameter out of a filesystem path; then the store's own
    ``fields_path`` validates the id again on the way to the file. 404 is "this run or patch is not
    here" (unknown id, unknown patch node, or a missing cache file); **400 is a request that cannot
    be answered as stated**, namely an unknown field name — a 404 there would read as "this run has
    no fields", which is a different and false claim.

    Every route calls ``check(request)`` first, is a plain ``def`` (Starlette runs it on a worker
    thread, so packing a payload never touches the event loop), and answers ``Cache-Control:
    no-store``.
    """
    from stokes.model.ids import ID_RE
    from stokes.run.fields import RunFieldsInvalid

    no_store = {"Cache-Control": "no-store"}

    # **The id rule is module level** so the report route shares it: a path parameter must never
    # reach a filesystem path unvalidated, and two copies of a regex is how the two drift apart.
    _run_id = checked_run_id

    def _target(raw: str) -> str:
        if raw == "slice" or ID_RE.match(raw):
            return raw
        raise HTTPException(status_code=400, detail=f"{raw!r} is not a patch id")

    @app.get("/api/results/field/{run_id}/{target}")
    def results_field(run_id: str, target: str, request: Request, field: str = "") -> Response:
        """One field of one patch, or of the slice, in the client's field codec.

        The payload is ``u32 header length``, JSON ``{"version", "field", "units", "components",
        "count", "min", "max"}`` padded with spaces to a multiple of 4, then ``f32[components x
        count]``. **404 when the run, the patch or the cache file is not there; 400 for an unknown
        field name or a malformed path parameter.**
        """
        check(request)
        if not field:
            raise HTTPException(status_code=400, detail="a field name is required")
        found = _run_id(run_id)
        named = _target(target)
        wanted = _plane_of(_plane_query(request), named)
        # **`SliceBusy` is caught here too** (Opus recheck item 3). `_slice_missing` calls
        # `slice_state`, and a cut that cannot get a slot raises - so the busy answer arrives from
        # BOTH the fetch and the missing-answer path. Catching it around only the first is what left
        # this route answering **500 instead of 503 `results.busy`**, and the client asks for the
        # slice and its edges together, so one busy plane looked like a server fault.
        try:
            try:
                payload = store_of().field_payload_for(found, named, field, wanted)
            except RunFieldsInvalid as exc:
                # Not a field the run wrote: a field function or an expression, when it is one.
                payload = _derived_payload(derived, found, named, field, wanted)
                if payload is None:
                    raise HTTPException(status_code=400, detail=exc.message or str(exc)) from exc
            if payload is None:
                raise (
                    _slice_missing(store_of(), found, wanted)
                    if wanted is not None
                    else HTTPException(status_code=404, detail="unknown run or patch")
                )
        except SliceBusy as exc:
            raise _slice_busy(exc) from exc
        return Response(payload, media_type="application/octet-stream", headers=no_store)

    @app.get("/api/results/slice/{run_id}")
    def results_slice(run_id: str, request: Request) -> Response:
        """The run's slice as transport version 2. 404 when the run has no slice.

        `?plane=ox,oy,oz,nx,ny,nz` asks for a cut the artifact may not hold, which the host computes
        once and the cache keeps. Omitting it is the run's own default mid-plane.
        """
        check(request)
        wanted = _plane_of(_plane_query(request))
        found = _run_id(run_id)
        with _answering_busy_and_broken():
            payload = store_of().slice_transport_for(found, wanted)
            if payload is None:
                raise _slice_missing(store_of(), found, wanted)
            return Response(payload, media_type="application/octet-stream", headers=no_store)

    @app.get("/api/results/slice-edges/{run_id}")
    def results_slice_edges(run_id: str, request: Request) -> Response:
        """The run's slice cell edges, in the same codec as ``/api/mesh/edges/{id}``."""
        check(request)
        wanted = _plane_of(_plane_query(request))
        found = _run_id(run_id)
        # **`SliceBusy` is caught here, as on the other two routes** (Opus recheck item 3). This one
        # had no handler at all, so a busy server answered **500** - and the client fetches the slice
        # and its edges together, so half a request looked like a crash.
        with _answering_busy_and_broken():
            payload = store_of().slice_edges_for(found, wanted)
            if payload is None:
                raise _slice_missing(store_of(), found, wanted)
        return Response(payload, media_type="application/octet-stream", headers=no_store)


#: The longest `?field=` an iso request may carry; a field name is a short identifier.
MAX_ISO_FIELD_TEXT = 32


def add_iso_route(
    app: FastAPI,
    store_of: Callable[[], RunStore],
    check: Callable[[Request], None],
) -> None:
    """Register ``GET /api/results/iso/{run_id}?field=p&value=4.5`` (ADR-0026 decision 3).

    **The iso-surface as transport version 2**, the payload ``/api/results/slice/{id}`` serves, so
    the viewport draws it with the reader it already has. The value is a JSON-style number (SI) or
    quantity text (``300 [K]``), checked by the same ``stokes.run.iso.check_request``
    ``run.iso_surface`` uses, so the route and the command refuse the same requests.

    Its own registrar, like the report's, because ``add_results_routes`` is at its statement
    budget. A plain ``def``: a cache miss cuts on the host from Starlette's worker thread, as a
    plane does, and never on the event loop.

    Answers: **401** without the token; **414** for a ``field`` or ``value`` past its length limit;
    **400** for a malformed run id, an unknown field or a value that is not a quantity, with the
    error's code; **422** ``results.iso_out_of_range``; **413** ``results.iso_too_large``;
    **404** for an unknown run, a run with no field data, an empty surface or a host that cannot
    cut it; **503** while the cut slots are full.
    """
    from stokes.run.errors import RunNotFound
    from stokes.run.iso import MAX_VALUE_TEXT, IsoInvalid, IsoOutOfRange, IsoTooLarge

    no_store = {"Cache-Control": "no-store"}

    def refused(status: int, exc: Exception) -> HTTPException:
        code = getattr(exc, "code", "results.iso_invalid")
        return HTTPException(status_code=status, detail={"code": code, "message": str(exc)})

    @app.get("/api/results/iso/{run_id}")
    def results_iso(run_id: str, request: Request) -> Response:
        """One run's iso-surface as transport version 2."""
        check(request)
        found = checked_run_id(run_id)
        field = request.query_params.get("field") or ""
        value = request.query_params.get("value") or ""
        if len(field) > MAX_ISO_FIELD_TEXT or len(value) > MAX_VALUE_TEXT:
            raise HTTPException(status_code=414, detail="the field or the value is too long")
        if not field or not value:
            raise HTTPException(status_code=400, detail="a field and a value are required")
        store = store_of()
        try:
            status = store.fields_status(found)
        except RunNotFound as exc:
            raise refused(404, exc) from exc
        if status.get("surfaces") != "ok":
            raise HTTPException(
                status_code=404,
                detail={"code": "results.no_fields", "message": "this run has no field data"},
            )
        from stokes.run.iso import check_request

        try:
            wanted = check_request(status, field, value)
        except IsoOutOfRange as exc:
            raise refused(422, exc) from exc
        except IsoInvalid as exc:
            raise refused(400, exc) from exc
        with _answering_busy_and_broken():
            try:
                summary = store.iso_summary(wanted)
            except IsoTooLarge as exc:
                raise refused(413, exc) from exc
            if summary is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "results.host_unavailable",
                        "message": "the surface is not cached and the run's host cannot cut it",
                    },
                )
            payload = store.slice_transport_of(store.iso_artifact_for(wanted, cut=False))
            if payload is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "results.iso_empty", "message": "the field never takes it"},
                )
        return Response(payload, media_type="application/octet-stream", headers=no_store)


def add_report_route(
    app: FastAPI,
    store_of: Callable[[], RunStore],
    check: Callable[[Request], None],
    run_id_of: Callable[[str], str],
    no_store: dict[str, str],
    project_name: Callable[[], str],
) -> None:
    """Register the results report download (results item 5).

    **Its own registrar** rather than a fourth route in `add_results_routes`: the report is not a
    field, a surface or an edge - it is a document about the run - and adding it there pushed that
    function past the statement budget, which the gate then said plainly.

    **A download, not a page**: `Content-Disposition: attachment` keeps generated content out of the
    app's own origin. A project name becomes part of the report's DOM, and rendering it inside Stokes's
    page would put user text in the application's document.
    """

    @app.get("/api/results/report/{run_id}")
    def results_report(run_id: str, request: Request) -> Response:
        """The run's report as an HTML file, built on demand and offered as a download."""
        check(request)
        found = run_id_of(run_id)
        store = store_of()
        record = store.record(found)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no run {found}")
        html = _report_bytes(store, found, record, project_name())
        return Response(
            html,
            media_type="text/html; charset=utf-8",
            headers={
                **no_store,
                "Content-Disposition": f'attachment; filename="{found}-report.html"',
            },
        )


def create_app(
    session: Session,
    *,
    token: str,
    dist_dir: Path | None = DEFAULT_DIST,
    extra_files: dict[str, tuple[bytes, str]] | None = None,
    compress_loopback: bool = False,
) -> FastAPI:
    """Build the app. ``extra_files`` maps a URL path to (bytes, media type), e.g. the Qt bridge script.

    Replies are gzipped for a remote client that accepts it and not for a loopback one (see
    ``GZIP_LEVEL``). ``compress_loopback`` compresses for loopback clients too: a browser behind an
    SSH tunnel arrives from 127.0.0.1 although its bytes cross a network, and
    ``tools/bench_viewport.py`` measures the compressed transfer with it.
    """
    app = FastAPI(
        title="Stokes", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
    )
    # Mesh payloads are float arrays, so they compress about nine to one (see GZIP_LEVEL), and
    # without it the transfer to a remote browser was the slowest part of opening a project. Browsers
    # decode it themselves; nothing in the client has to change. Middleware added last runs first,
    # so the loopback filter sees the request before the gzip middleware does.
    app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=GZIP_LEVEL)
    if not compress_loopback:
        app.add_middleware(SkipGzipOnLoopback)
    hub = RpcHub(session)
    extras = dict(extra_files or {})

    def geometry_store() -> GeometryStore:
        # Loaded on first use: the store pulls in numpy, which the start-up path does without.
        from stokes.geometry.store import GeometryStore

        return GeometryStore.of(session)

    def mesh_service() -> MeshService:
        # Loaded on first use, like the geometry store: the mesh package pulls in the driver.
        from stokes.mesh.service import MeshService

        return MeshService.of(session)

    def run_store() -> RunStore:
        # Loaded on first use: the run store pulls in the field reader, which pulls in numpy.
        from stokes.run.store import RunStore

        return RunStore.of(session)

    def check(request: Request) -> None:
        supplied = request.query_params.get("token") or request.headers.get("x-stokes-token")
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    @app.get("/api/health")
    def health(request: Request) -> dict[str, object]:
        """Liveness, and **which build is answering**.

        **`version` alone could not answer that** (Codex's review): it is a static string that changes
        only at a release, so two servers weeks apart reported the same one. `commit` and `dirty` come
        from the checkout the process started in, read once - so a reviewer can tell what they are
        looking at instead of inferring it from a working directory.
        """
        check(request)
        return {"status": "ok", "version": __version__, "build": build_info().as_dict()}

    _add_geometry_routes(app, geometry_store, check)
    add_mesh_routes(app, mesh_service, check)  # after the geometry routes (ADR-0024 5.5)

    def derived(
        run_id: str, target: str, field: str, plane: dict[str, Any] | None
    ) -> tuple[Any, str]:  # Any: a numpy array
        # Field functions and expressions colour the model like any field (results calculator).
        from stokes.api.commands.results import field_values

        return field_values(session, run_id, target, field, plane)

    add_results_routes(app, run_store, check, derived)  # after the mesh routes (T-216)
    add_iso_route(app, run_store, check)
    # **The same id rule the results routes use**: a path parameter becomes a filename inside the
    # cache, so it is checked against `ID_RE` before any lookup - here through a closure over the
    # module's own check rather than a second, subtly different regex.
    add_report_route(
        app,
        run_store,
        check,
        checked_run_id,
        {"Cache-Control": "no-store"},
        lambda: session.require_project().name,
    )
    add_project_routes(app, check)  # the start screen's thumbnail
    add_upload_routes(app, lambda: session, check)  # G1: a browser's CAD file becomes a path
    # **No start-up sweep any more, because there is nothing to sweep** (claude-win2, reading 1).
    # The sweep existed for scratch folders under `/tmp` and the `.part` files inside them, and
    # **the measurement that justified it is the argument for removing the cause instead**: on this
    # machine **127 imports folders and 5662 runs folders had accumulated**, because a session that
    # never saved left its whole scratch directory behind and nothing owned it. Everything is written
    # inside the project folder now, where the user can see it and Save as carries it, so the class
    # of orphan is gone rather than tidied.
    #
    # **A `.part` file can still be left by a crash mid-upload**, and it is now inside the project's
    # own `imports/` - so it travels with the project and a later upload of the same name does not
    # collide with it, because `mkstemp` names it uniquely.

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        supplied = websocket.query_params.get("token", "")
        if not secrets.compare_digest(supplied, token):
            await websocket.close(code=4401)
            return
        loop = asyncio.get_running_loop()
        store = await asyncio.to_thread(geometry_store)
        # Imports build their groups of parts here, between commands, not on their own thread.
        store.bind(loop.call_soon_threadsafe, threading.get_ident())

        async def activate() -> None:
            # Only once this client is registered with the hub: what the restore reports first
            # (cached meshes, the read of an import that has just started) reaches nobody earlier.
            await asyncio.to_thread(store.activate)

        await hub.serve(websocket, registered=activate)

    for url_path, (payload, media_type) in extras.items():

        def serve_extra(payload: bytes = payload, media_type: str = media_type) -> Response:
            return Response(payload, media_type=media_type)

        app.get(url_path)(serve_extra)

    if dist_dir is not None and (dist_dir / "index.html").is_file():
        index = dist_dir / "index.html"

        @app.get("/")
        def root() -> FileResponse:
            return FileResponse(index)

        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="web")

    app.state.hub = hub
    app.state.geometry_store = geometry_store
    app.state.mesh_service = mesh_service
    app.state.token = token
    return app


def _write_blocks(handle: IO[bytes], blocks: list[bytes]) -> None:
    """Write a batch of already-read blocks. **Called from a worker thread.**

    The blocks are in memory by the time this runs - the request drain produced them - so the only
    work here is the write itself, which is the part that must not be on the loop.
    """
    for block in blocks:
        handle.write(block)


def _sync_handle(handle: IO[bytes]) -> None:
    """Flush and `fsync`. **Called from a worker thread**, and on a network filesystem this is the
    slowest part of an upload - the report's own scenario is the project folder on a NAS, where the
    fsync alone can stall for seconds.

    `os` is imported here rather than at module scope, matching the route that already imports it
    locally: this module's import cost is on the server's start path.
    """

    handle.flush()
    os.fsync(handle.fileno())


async def _receive_to_part(request: Request, part: Path, digest: HashLike) -> int:
    """Stream the request body into ``part``, returning the bytes received.

    **Every disk operation runs OFF the event loop** (Opus review item 8). The handler is `async def`,
    but `handle.write`, the final `flush` and the `fsync` are blocking calls - and they ran on the
    loop, so a large upload froze **every** WebSocket listener for as long as the write took.
    Measured: 512 MB of this exact work is 3.88 s in a thread, during which this loop would have
    served no residuals and answered no ping.

    **One `to_thread` per BATCH of blocks, not per block.** A thread hop for every 64 KiB costs more
    than the write it carries, so blocks are collected up to `WRITE_BATCH_BYTES` and handed over
    together.

    **The drain stays on the loop.** `request.stream()` is an async generator and cannot be consumed
    from a thread - I wrote that version first and it cannot work: the drain is what yields control,
    so it has to stay where the loop is. What crosses to the thread is the bytes, already in memory.
    """
    total = 0
    with part.open("wb") as handle:
        pending: list[bytes] = []
        pending_bytes = 0
        async for block in request.stream():
            if not block:
                continue
            total += len(block)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"at most {MAX_UPLOAD_BYTES} bytes per upload",
                )
            digest.update(block)
            pending.append(block)
            pending_bytes += len(block)
            if pending_bytes >= WRITE_BATCH_BYTES:
                await asyncio.to_thread(_write_blocks, handle, pending)
                pending = []
                pending_bytes = 0
        if pending:
            await asyncio.to_thread(_write_blocks, handle, pending)
        # The flush and the fsync are the slowest part on a network filesystem, and they are the
        # part that must not hold the loop.
        await asyncio.to_thread(_sync_handle, handle)
    return total


def _link_into_place(part: Path, folder: Path, name: str) -> Path:
    """Hard-link ``part`` to a free final name and remove ``part``; returns the final path.

    **Called from a worker thread.** The free name is found with `os.link` itself rather than by
    asking `exists()` first: asking cannot hold the name, and two uploads that both asked took the
    same one (Opus item 9). `FileExistsError` is the reservation failing, which is the signal to try
    the next name.

    A bounded number of attempts, because a directory with thousands of `part-N.step` files must not
    spin: past the limit the caller gets a real answer rather than a hang.
    """

    root = folder.resolve()
    stem, _, suffix = name.rpartition(".")
    for index in range(1, LINK_ATTEMPTS + 1):
        candidate = root / (name if index == 1 else f"{stem}-{index}.{suffix}")
        # **The containment check moved here with the reservation** (Opus item 9). It lived in
        # `_upload_target`, which the route no longer calls - and a guard that stays behind in a dead
        # function is a guard that is not guarding. `name` is sanitised already; this proves the
        # resolved result is inside `folder`, so a bug in the sanitiser becomes a refusal rather than
        # a write outside the imports folder.
        if not candidate.resolve().is_relative_to(root):
            raise HTTPException(status_code=400, detail="the name escapes the imports folder")
        try:
            os.link(part, candidate)
        except FileExistsError:
            continue
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"cannot store the upload: {exc}") from exc
        # The final name exists and is whole; the `.part` is now redundant. Its removal is not
        # allowed to fail the request - the upload succeeded the moment the link did.
        with contextlib.suppress(OSError):
            part.unlink()
        return candidate
    raise HTTPException(
        status_code=409,
        detail=f"too many files named {name!r} in the imports folder",
    )


def add_project_routes(app: FastAPI, check: Callable[[Request], None]) -> None:
    """The route a start screen needs that is not a command (T-179).

    One route, because a thumbnail is **binary and cacheable** while everything else about the start
    screen is a command. Serving it as a command would mean base64 in a JSON reply and no `ETag`,
    which for an image that changes only when a project is saved is the wrong trade.
    """

    @app.get("/api/project/preview")
    def project_preview(request: Request, path: str = "") -> Response:
        """The thumbnail of one project, or 404 `project.no_preview`.

        **This must not become an arbitrary file read**, and that is the whole design of this
        handler. Three checks, in order, and each is load-bearing:

        1. **the path must name a project we already know about** - one in the recent list, or
           inside a browse root. A caller cannot ask for any file on the machine, only for the
           thumbnail of a project the app has already offered it;
        2. **it must resolve inside that project folder** and `preview.png` must be a regular file
           that is not a symlink;
        3. **the size is capped** so a file swapped in by hand cannot be served whole.

        `ETag` and `Last-Modified` come from the file's own stamp, so a thumbnail that has not
        changed is answered `304` and the browser keeps what it has.
        """
        check(request)
        folder = _known_project(path)
        if folder is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "project.no_preview", "message": "no project at that path"},
            )
        from stokes.model.preview import MAX_BYTES, PREVIEW_FILE

        target = folder / PREVIEW_FILE
        try:
            # **`is_file` follows links, so `is_symlink` is checked too**: a link named
            # `preview.png` pointing at a private key would otherwise be served as one.
            if target.is_symlink() or not target.is_file():
                raise FileNotFoundError(target)
            info = target.stat()
        except OSError:
            raise HTTPException(
                status_code=404,
                detail={"code": "project.no_preview", "message": "this project has no preview"},
            ) from None
        if info.st_size > MAX_BYTES:
            raise HTTPException(
                status_code=404,
                detail={"code": "project.no_preview", "message": "the preview is too large"},
            )
        etag = f'"{int(info.st_mtime_ns):x}-{info.st_size:x}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            target.read_bytes(),
            media_type="image/png",
            headers={
                "ETag": etag,
                "Last-Modified": format_datetime(datetime.fromtimestamp(info.st_mtime, UTC)),
                # **Cacheable, unlike every other route here.** A thumbnail changes only when the
                # project is saved, and re-sending a picture on every redraw of a start screen is
                # exactly what an `ETag` exists to avoid.
                "Cache-Control": "private, max-age=0, must-revalidate",
            },
        )


def _known_project(raw: str) -> Path | None:
    """The project folder `raw` names, when it is one we have offered.

    **The check is membership, not containment alone.** Resolving inside a root would still let a
    caller ask for any folder that happens to hold a `preview.png`; requiring the path to be a
    project **in the recent list or under a browse root** means the route can only answer about
    projects the app itself put on screen.

    None for everything else, and the caller answers 404 - a refusal that said which of the two
    reasons it was would tell a caller about folders it was never shown.
    """
    if not raw:
        return None
    from stokes.model.browse import PROJECT_FILE, roots
    from stokes.model.recent import read_recent

    try:
        wanted = Path(raw).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    known: set[Path] = set()
    for entry in read_recent():
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            known.add(Path(entry.path).resolve())
    inside = any(wanted.is_relative_to(root) for root in roots())
    if wanted not in known and not inside:
        return None
    # **It must still be a project**: a folder under a root with a `preview.png` in it but no
    # `project.json` is not one, and serving its picture would be a file read by another name.
    if not (wanted / PROJECT_FILE).is_file():
        return None
    return wanted
