"""A project with a finished mesh, made without OpenFOAM, to capture the mesh look (T-211).

    python tools/mesh_demo_project.py <folder>
    python -m stokes.app --project <folder>.stokes --screenshot out.png --setup mesh-look.js

(``project.new`` adds the ``.stokes`` suffix; the output names the project. The setup script of the
T-211 captures is ``.mailbox/shots/mesh-look.js``.)

Builds the end-to-end duct (``tools.e2e_support.duct_project``: a 500 x 20 x 20 mm box, three tags,
conditions, blockMesh settings at 2 mm), then writes the result a real run would leave: a ``done``
``mesh_result`` whose basis matches the current snapshot (so it is not stale), one ``mesh_patch``
per patch blockMesh would write, and the surface cache file the routes read. The surfaces are the
cell faces blockMesh would make (250 x 10 x 10 cells for the duct), triangulated and edged by the
agent's own writer (``stokes_agent.extract``: ``triangulate_fan``, ``unique_edges`` and
``pack_artifact``), so the desktop's reader and the browser's parser see the real format. Nothing
here runs a mesher: the quality figures are those of a perfectly orthogonal hex block, and say
nothing about any other case.

**A development tool, not a feature.** It reaches into ``MeshService`` (``_replace`` and
``_finish_with``), the one place a result reaches its final state; a real run goes the same way
through ``_finish_from_job``. Do not copy this into product code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.mesh.snapshot import MeshSnapshot

#: The OpenFOAM the result records it was made with; part of the basis a stale check compares.
ENGINE = "org-14"

Axis = tuple[float, float, float]


def _face_grid(origin: Axis, along_u: Axis, along_v: Axis, nu: int, nv: int) -> tuple[Any, Any]:
    """The points ``((nu + 1) * (nv + 1), 3)`` and quads ``(nu * nv, 4)`` of one rectangular face.

    ``along_u`` and ``along_v`` are the whole sides, so a cell's size is the side over the count.
    The quads run round each cell in order, which ``unique_edges`` and ``triangulate_fan`` read.
    """
    iu, iv = np.meshgrid(np.arange(nu + 1), np.arange(nv + 1), indexing="ij")
    points = (
        np.asarray(origin, dtype="float64")
        + iu[..., None] * (np.asarray(along_u, dtype="float64") / nu)
        + iv[..., None] * (np.asarray(along_v, dtype="float64") / nv)
    ).reshape(-1, 3)
    ci, cj = np.meshgrid(np.arange(nu), np.arange(nv), indexing="ij")

    def at(i: Any, j: Any) -> Any:
        return i * (nv + 1) + j

    quads = np.stack(
        [at(ci, cj), at(ci + 1, cj), at(ci + 1, cj + 1), at(ci, cj + 1)], axis=-1
    ).reshape(-1, 4)
    return points, quads


def side_grid(side: str, bbox: tuple[float, ...], counts: tuple[int, int, int]) -> tuple[Any, Any]:
    """The cell faces of one side of the block, as ``_face_grid`` makes them."""
    x0, y0, z0, x1, y1, z1 = bbox
    nx, ny, nz = counts
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    faces = {
        "xmin": ((x0, y0, z0), (0, dy, 0), (0, 0, dz), ny, nz),
        "xmax": ((x1, y0, z0), (0, dy, 0), (0, 0, dz), ny, nz),
        "ymin": ((x0, y0, z0), (dx, 0, 0), (0, 0, dz), nx, nz),
        "ymax": ((x0, y1, z0), (dx, 0, 0), (0, 0, dz), nx, nz),
        "zmin": ((x0, y0, z0), (dx, 0, 0), (0, dy, 0), nx, ny),
        "zmax": ((x0, y0, z1), (dx, 0, 0), (0, dy, 0), nx, ny),
    }
    origin, along_u, along_v, nu, nv = faces[side]
    return _face_grid(origin, along_u, along_v, nu, nv)


def patch_surface(
    name: str,
    kind: str,
    sides: list[str],
    bbox: tuple[float, ...],
    counts: tuple[int, int, int],
) -> dict[str, Any]:
    """One patch as the agent's writer takes it: points, triangles, cell edges and face count.

    The sides' points are merged where they meet (a corner of the box is one point of a ``walls``
    patch), as in a real mesh.
    """
    from stokes_agent.extract import triangulate_fan, unique_edges

    point_blocks: list[Any] = []
    quad_blocks: list[Any] = []
    base = 0
    for side in sides:
        points, quads = side_grid(side, bbox, counts)
        point_blocks.append(points)
        quad_blocks.append(quads + base)
        base += len(points)
    merged, inverse = np.unique(
        np.round(np.concatenate(point_blocks), 9), axis=0, return_inverse=True
    )
    quads = np.concatenate(quad_blocks)
    connectivity = np.asarray(inverse).reshape(-1)[quads.reshape(-1)]
    offsets = np.arange(0, quads.size + 1, 4)
    return {
        "name": name,
        "patch_type": kind,
        "points": merged.astype("float32"),
        "triangles": triangulate_fan(offsets, connectivity),
        "edges": unique_edges(offsets, connectivity),
        "faces": int(quads.shape[0]),
    }


def quality_report(counts: tuple[int, int, int], boundary_faces: int) -> dict[str, Any]:
    """The checkMesh numbers of an orthogonal hex block of ``counts`` cells."""
    nx, ny, nz = counts
    internal = (nx - 1) * ny * nz + nx * (ny - 1) * nz + nx * ny * (nz - 1)
    return {
        "cells": nx * ny * nz,
        "points": (nx + 1) * (ny + 1) * (nz + 1),
        "faces": internal + boundary_faces,
        "internal_faces": internal,
        "boundary_patches": 3,
        "max_non_ortho": 0.0,
        "avg_non_ortho": 0.0,
        "max_skewness": 0.0,
        "max_aspect_ratio": 1.0,
        "failed_checks": 0,
        "mesh_ok": True,
    }


def write_result(session: Any, snap: MeshSnapshot) -> dict[str, Any]:
    """Make the ``done`` result with its patches and write the surface cache file."""
    from stokes.mesh.basis import mesh_basis
    from stokes.mesh.drivers import blockmesh
    from stokes.mesh.service import MeshService, _Surfaces
    from stokes_agent.extract import pack_artifact

    project = session.require_project()
    counts = blockmesh.cells(snap)
    bbox = tuple(snap.bbox)
    surfaces = [
        patch_surface(name, kind, sides, bbox, counts)
        for name, (kind, sides) in blockmesh.patches(snap).items()
    ]
    data, header = pack_artifact(surfaces)
    boundary = sum(int(patch["faces"]) for patch in surfaces)

    service = MeshService.of(session)
    basis = mesh_basis(snap, blockmesh.DRIVER_VERSION, ENGINE)
    result = service._replace(project, snap, "local", basis, ENGINE, None)
    service._reset(result, snap, ENGINE, basis)
    path = service._surface_path(str(result.id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    service._finish_with(
        project,
        result,
        "done",
        report=quality_report(counts, boundary),
        seconds=3.2,
        surfaces=_Surfaces(patches=tuple(header["patches"])),
    )
    return {
        "result": str(result.id),
        "cells": counts,
        "patches": {str(p["name"]): int(p["faces"]) for p in surfaces},
        "surface_file": str(path),
        "bytes": len(data),
    }


def main(folder: str | Path) -> int:
    """Build the duct and its synthetic mesh under ``folder``; return 0 on success."""
    from stokes.api import Session
    from stokes.mesh.model import SETTINGS_PATH
    from stokes.mesh.snapshot import build_snapshot
    from tools.e2e_support import duct_project

    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    session = Session()
    ids = duct_project(session, target)
    settings = session.require_project().find(SETTINGS_PATH)
    if settings is None:
        sys.stderr.write("the duct project has no mesh settings\n")
        return 1
    snap = build_snapshot(session, settings)
    made = write_result(session, snap)
    session.execute("project.save")
    sys.stdout.write(
        json.dumps({"project": str(session.project_path), "ids": ids, **made}, indent=1) + "\n"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="mesh_demo_project")
    parser.add_argument("folder", help="where to create the project")
    sys.exit(main(parser.parse_args().folder))
