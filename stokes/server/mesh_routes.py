"""The mesh routes: the patches of a mesh result, served to the viewport (T-210, ADR-0024 5.5).

``GET /api/mesh/{node_id}`` is one ``mesh_patch`` node's triangles in the existing transport
version 2 (one group, ``kind: "mesh"``, every face id 0); ``GET /api/mesh/edges/{node_id}`` is its
cell edges in their own small codec (:mod:`stokes.mesh.surfaces`); ``GET /api/mesh/batch?ids=...``
frames many patches in one reply, byte for byte like ``/api/geometry/batch`` so the client's
``readBatch`` decodes both. A node that is unknown, is not a patch, or whose surfaces are not in
the cache is a 404 (a batch marks it with the no-mesh length instead).

Every route calls ``check(request)`` first, the session token, and answers ``Cache-Control:
no-store``. They are plain ``def`` routes, which Starlette runs on worker threads: reading a cache
file and packing a patch must not run on the event loop.

``/api/geometry/batch`` keeps reading only the geometry store. A mesh patch is not a part.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

if TYPE_CHECKING:
    from stokes.mesh.service import MeshService

NO_STORE = {"Cache-Control": "no-store"}


def add_mesh_routes(
    app: FastAPI,
    service_of: Callable[[], MeshService],
    check: Callable[[Request], None],
) -> None:
    """Register the three mesh routes, most specific first, so ``batch`` and ``edges`` are not read
    as node ids."""
    # The framing and its cap are the geometry batch's own; shared, not copied, so the two cannot
    # drift. Imported here because ``app`` imports this module.
    from stokes.server.app import MAX_BATCH_IDS, _batch_frame

    @app.get("/api/mesh/batch")
    def mesh_batch(request: Request, ids: str = "") -> Response:
        """Many patches in one binary reply.

        Query: ``ids=id1,id2,...`` (node ids, at most ``MAX_BATCH_IDS``, 413 above). A repeated id
        is answered once, where it first appears; an id that is unknown, or whose surfaces are
        missing, gets the ``0xFFFFFFFF`` no-mesh marker. The framing is that of
        ``/api/geometry/batch``: ``u32 count``, then per id ``u32 id length, id, u32 payload
        length (or 0xFFFFFFFF), payload``, little-endian.
        """
        check(request)
        wanted = [nid for nid in (part.strip() for part in ids.split(",")) if nid]
        if len(wanted) > MAX_BATCH_IDS:
            raise HTTPException(status_code=413, detail=f"at most {MAX_BATCH_IDS} ids per request")
        unique = list(dict.fromkeys(wanted))
        service = service_of()
        frames = [struct.pack("<I", len(unique))]
        frames.extend(_batch_frame(nid, service.patch_transport(nid)) for nid in unique)
        return Response(
            content=b"".join(frames), media_type="application/octet-stream", headers=NO_STORE
        )

    @app.get("/api/mesh/edges/{node_id}")
    def mesh_edges(node_id: str, request: Request) -> Response:
        """One patch's cell edges: ``u32`` header length, a JSON header
        ``{"version": 1, "units": "m", "segments": E}`` padded to a multiple of 4, then
        ``f32[6E]``. 404 when the node is unknown or its surfaces are missing."""
        check(request)
        payload = service_of().patch_edges(node_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown mesh patch")
        return Response(payload, media_type="application/octet-stream", headers=NO_STORE)

    @app.get("/api/mesh/{node_id}")
    def mesh_patch(node_id: str, request: Request) -> Response:
        """One patch as transport version 2. 404 when the node is unknown or its surfaces are
        missing (a reopened project whose cache file is gone)."""
        check(request)
        payload = service_of().patch_transport(node_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown mesh patch")
        return Response(payload, media_type="application/octet-stream", headers=NO_STORE)
