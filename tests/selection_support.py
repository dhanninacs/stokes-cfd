"""Meshes and sessions for the named-tag, check and export tests (ADR-0020 slice 1).

Synthetic cubes are built in numpy, with their vertices kept apart per face as the importer's
weld keeps them, so these tests run without OCP. The OCP helpers tessellate fixture shapes in
this process and are used only by tests that skipped themselves when OCP is missing. Parts are
added as ``part`` nodes and their meshes handed to the session's geometry store with its public
``put``, the way a finished import leaves them.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from stokes.api import Session
from stokes.geometry.files import SourceStamp
from stokes.geometry.mesh import PartMesh, TriMesh, vertex_normals
from stokes.geometry.store import GeometryStore, MeshEntry

STAMP = SourceStamp("parts.step", 4096, 1_700_000_000_000_000_000)
"""A source stamp for synthetic meshes, so that they have a B-rep version (basis)."""


def cube(
    size: float = 1.0,
    at: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    name: str = "cube",
    inward: bool = False,
) -> TriMesh:
    """An axis-aligned cube of six faces, two triangles each, wound outwards (or inwards).

    Face ids: 0 x-min, 1 x-max, 2 y-min, 3 y-max, 4 z-min, 5 z-max.
    """
    corners: list[np.ndarray[Any, Any]] = []
    for axis in range(3):
        for side in (0.0, 1.0):
            u, v = (axis + 1) % 3, (axis + 2) % 3
            quad = []
            for a, b in ((0, 0), (1, 0), (1, 1), (0, 1)):
                point = np.zeros(3)
                point[axis], point[u], point[v] = side, a, b
                quad.append(point)
            normal = np.zeros(3)
            normal[axis] = 1.0 if side else -1.0
            for tri in ((0, 1, 2), (0, 2, 3)):
                p = [quad[i] for i in tri]
                if np.dot(np.cross(p[1] - p[0], p[2] - p[0]), normal) < 0.0:
                    p[1], p[2] = p[2], p[1]
                if inward:
                    p[1], p[2] = p[2], p[1]
                corners.append(np.array(p))
    return mesh_from_corners(
        np.array(corners) * size + np.array(at), np.repeat(np.arange(6), 2), name=name
    )


def mesh_from_corners(
    corners: np.ndarray[Any, Any],
    face_ids: Sequence[int] | np.ndarray[Any, Any],
    *,
    name: str = "solid",
    faces: int | None = None,
    quality: str = "fine",
) -> TriMesh:
    """A TriMesh from (T, 3, 3) corners, with vertices shared only inside a face."""
    ids = np.asarray(face_ids, dtype=np.uint32)
    flat = np.asarray(corners, dtype=np.float32).reshape(-1, 3)
    keys = np.repeat(ids, 3)
    rows = np.column_stack([keys.astype(np.float64), flat.astype(np.float64)])
    _, first, inverse = np.unique(rows, axis=0, return_index=True, return_inverse=True)
    positions = np.ascontiguousarray(flat[first])
    indices = np.ascontiguousarray(inverse.reshape(-1, 3), dtype=np.uint32)
    count = int(ids.max()) + 1 if ids.size else 0
    return TriMesh(
        name,
        "solid",
        (0.7, 0.7, 0.7),
        positions,
        vertex_normals(positions, indices),
        indices,
        ids,
        count if faces is None else faces,
        0,
        quality,
    )


def corners_of(mesh: TriMesh) -> np.ndarray[Any, Any]:
    return mesh.positions[mesh.indices].astype(np.float64)


def part(*solids: TriMesh, source: SourceStamp | None = STAMP) -> PartMesh:
    return PartMesh(tuple(solids), "m", source)


def permuted(mesh: TriMesh, order: Sequence[int]) -> TriMesh:
    """The same triangles with face ``f`` renumbered ``order[f]`` (a B-rep read differently)."""
    assert mesh.face_ids is not None
    mapping = np.asarray(order, dtype=np.uint32)
    return mesh_from_corners(corners_of(mesh), mapping[mesh.face_ids], name=mesh.name)


# ---------------------------------------------------------------- sessions


def new_session(tmp_path: Path, name: str = "demo") -> Session:
    session = Session(journal_path=tmp_path / f"{name}.jsonl")
    session.execute("project.new", name=name, path=str(tmp_path / name))
    return session


def add_part(
    session: Session,
    name: str,
    mesh: PartMesh | None,
    *,
    parent: str = "/geometry/parts",
    node_id: str | None = None,
) -> str:
    """A ``part`` node (and ``/geometry/parts`` when missing) holding ``mesh``."""
    project = session.require_project()
    path = ""
    for name_part in parent.strip("/").split("/"):
        if project.find(f"{path}/{name_part}") is None:
            session.execute("node.add", parent=path or "/", type="group", name=name_part)
        path = f"{path}/{name_part}"
    params: dict[str, Any] = {}
    if mesh is not None:
        params = {"faces": mesh.faces, "triangles": mesh.triangles}
    created = session.execute(
        "node.add", parent=parent, type="part", name=name, params=params, node_id=node_id
    )
    if mesh is not None:
        put_mesh(session, created["id"], mesh)
    return str(created["id"])


def put_mesh(session: Session, part_id: str, mesh: PartMesh) -> None:
    GeometryStore.of(session).put(part_id, MeshEntry(mesh, complete=True))


def events_of(session: Session) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []
    session.events.subscribe("*", lambda event, data: seen.append((event, data)))
    return seen


# ---------------------------------------------------------------- OCP (callers skip without it)


def tessellated(shape: Any, quality: str = "fine", *, name: str = "solid") -> TriMesh:  # Any: OCP
    from stokes.geometry import step

    chosen = step.FINE if quality == "fine" else step.COARSE
    linear, angular = step.deflections(shape, chosen)
    return step.tessellate(shape, linear, angular, name=name, quality=quality)


def split_cylinder(radius: float, height: float, cuts: int, at: Any = (0, 0, 0)) -> Any:  # Any: OCP
    """A cylinder whose side is ``cuts + 1`` faces, as many CAD exporters write it."""
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed

    from tests import geometry_fixtures as fx

    divide = ShapeUpgrade_ShapeDivideClosed(fx.cylinder(radius, height, at))
    divide.SetNbSplitPoints(cuts)
    divide.Perform()
    return divide.Result()
