"""The Fast mode routes (ADR-0047 decision 8): the shown plane of the running preview.

``GET /api/fast/slice`` is the plane's triangles in transport version 2 (one ``mesh`` group, every
face id 0, metres), byte for byte the shape of a results slice, so the viewport decodes it with the
reader it has. ``GET /api/fast/field?field=U`` is one of its values in the client's field codec
(``stokes.run.fields.field_payload``): ``U``, ``Ux``, ``Uy``, ``Uz`` (m/s) or ``p`` (kinematic,
m2/s2, as an incompressible run's). Both come from the same last frame and carry its key in
``X-Fast-Key`` (``<geometry version>:<axis>:<plane>``): a client holding a mesh of another key fetches
the mesh again, so values never land on the wrong vertices.

**204 (no content) when no preview is running or its plane holds no fluid**: a client that asks
for the plane as the preview stops is not at fault, and a 404 there put an error in the browser's
console on every Stop (seen in the app, 2026-11-22). 400 for an unknown field. Every route
calls ``check(request)`` first, is a plain ``def`` (a worker thread packs the payload), and answers
``Cache-Control: no-store``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.fast.runner import Frame

UNITS = {"U": "m/s", "Ux": "m/s", "Uy": "m/s", "Uz": "m/s", "p": "m2/s2", "T": "K"}


def add_fast_routes(app: FastAPI, session: Session, check: Callable[[Request], None]) -> None:
    """Register the two Fast routes."""

    def frame_or_none() -> Frame | None:
        from stokes.api.fast_state import require_frame
        from stokes.errors import StokesError

        try:
            frame: Frame = require_frame(session)
        except StokesError:
            return None
        return frame if frame.triangles.shape[0] > 0 else None

    nothing = Response(status_code=204, headers={"Cache-Control": "no-store"})

    def headers(frame: Frame, field: str) -> dict[str, str]:
        # The temperature has a mesh of its own (it covers the solids), so the key names the mesh.
        return {
            "Cache-Control": "no-store",
            "X-Fast-Key": f"{frame.geometry_version}:{frame.axis}:{frame.plane}:{frame.mesh_of(field)[0]}",
            "Access-Control-Expose-Headers": "X-Fast-Key",
        }

    @app.get("/api/fast/slice")
    def fast_slice(request: Request, field: str = "U") -> Response:
        """The shown plane as transport version 2."""
        check(request)
        import numpy as np

        from stokes.geometry.mesh import TriMesh, vertex_normals
        from stokes.geometry.transport import pack_meshes

        frame = frame_or_none()
        if frame is None:
            return nothing
        _key, positions, triangles = frame.mesh_of(field)
        if triangles.shape[0] == 0:
            return nothing
        mesh = TriMesh(
            name="fast",
            kind="mesh",
            color=(0.6, 0.64, 0.68),
            positions=positions,
            normals=vertex_normals(positions, triangles),
            indices=triangles,
            face_ids=np.zeros(triangles.shape[0], dtype=np.uint32),
            faces=1,
            face_base=0,
            quality="fine",
        )
        payload = pack_meshes([mesh], version=2, units="m")
        return Response(
            payload, media_type="application/octet-stream", headers=headers(frame, field)
        )

    @app.get("/api/fast/field")
    def fast_field(request: Request, field: str = "U") -> Response:
        """One value of the shown plane, in the field codec."""
        check(request)
        from stokes.run.fields import field_payload

        if field not in UNITS:
            raise HTTPException(status_code=400, detail=f"{field!r} is not one of {sorted(UNITS)}")
        frame = frame_or_none()
        if frame is None:
            return nothing
        if field not in frame.values or frame.values[field].size == 0:
            return nothing  # the temperature, on a case that does not solve it
        values = frame.values[field]
        payload = field_payload(
            values, field, UNITS[field], float(values.min()), float(values.max())
        )
        return Response(
            payload, media_type="application/octet-stream", headers=headers(frame, field)
        )
