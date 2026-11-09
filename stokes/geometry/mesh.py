"""Display meshes: one :class:`TriMesh` per solid, grouped into a :class:`PartMesh` per part node.

numpy only (ADR-0014). Positions are float32 metres, normals float32 unit vectors, indices
uint32 triangles. ``face_ids`` (uint32, one per triangle, or None for synthetic meshes such
as the sample part) names the B-rep face each triangle came from, so the viewport can pick and
highlight faces while drawing one mesh per solid. Face ids are numbered across the whole part:
a solid's ids run from ``face_base`` to ``face_base + faces - 1``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.files import SourceStamp

F32 = NDArray[np.float32]
U32 = NDArray[np.uint32]
Vec3 = tuple[float, float, float]
Bbox = tuple[Vec3, Vec3]
"""(minimum corner, maximum corner)"""

DEFAULT_COLOR: Vec3 = (0.78, 0.8, 0.82)


@dataclass(frozen=True)
class TriMesh:
    """One drawable group: a solid of a part, or a patch of the sample part."""

    name: str
    kind: str
    color: Vec3
    positions: F32
    normals: F32
    indices: U32
    face_ids: U32 | None = None
    faces: int = 0
    face_base: int = 0
    quality: str = ""

    @property
    def vertex_count(self) -> int:
        return int(self.positions.shape[0])

    @property
    def triangle_count(self) -> int:
        return int(self.indices.shape[0])

    @property
    def bbox(self) -> Bbox | None:
        return bbox_of(self.positions)


@dataclass(frozen=True)
class PartMesh:
    """The display mesh of one part node: its solids in file order, positions in metres."""

    solids: tuple[TriMesh, ...]
    units: str = "m"
    source: SourceStamp | None = None
    """
    The B-rep version these triangles were made from, when it is known
    (`fingerprints.basis_for`).

    **The source stamp alone cannot say whether a mesh is current** (Claude's defect 1): the stamp
    is the file's name, size and mtime, and a part's units can be changed without touching the file
    — `parameter.set node=… name=units value=inch` changes the geometry a mesh is derived from while
    the stamp stays identical. The cache then served the millimetre mesh for an inch model, so the
    drawn geometry and the measured edges disagreed.

    Cached with the bytes and compared on load, so "current" means the same source file **and** the
    same version of it. `None` for a mesh with no version to record (an operation's output before
    it has a basis, the synthetic sample), which is treated as "cannot be vouched for".
    """
    basis: str | None = None

    @property
    def faces(self) -> int:
        return sum(s.faces for s in self.solids)

    @property
    def triangles(self) -> int:
        return sum(s.triangle_count for s in self.solids)

    @property
    def vertices(self) -> int:
        return sum(s.vertex_count for s in self.solids)

    @property
    def bbox(self) -> Bbox | None:
        return bbox_union(s.bbox for s in self.solids)


def bbox_of(positions: F32) -> Bbox | None:
    if positions.shape[0] == 0:
        return None
    low = positions.min(axis=0)
    high = positions.max(axis=0)
    return (
        (float(low[0]), float(low[1]), float(low[2])),
        (float(high[0]), float(high[1]), float(high[2])),
    )


def bbox_union(boxes: Iterable[Bbox | None]) -> Bbox | None:
    result: Bbox | None = None
    for box in boxes:
        if box is None:
            continue
        if result is None:
            result = box
            continue
        (a0, a1), (b0, b1) = result, box
        result = (
            (min(a0[0], b0[0]), min(a0[1], b0[1]), min(a0[2], b0[2])),
            (max(a1[0], b1[0]), max(a1[1], b1[1]), max(a1[2], b1[2])),
        )
    return result


def bbox_list(box: Bbox | None, digits: int = 9) -> list[float]:
    """``[xmin, ymin, zmin, xmax, ymax, zmax]`` rounded to ``digits`` places; empty for None."""
    if box is None:
        return []
    return [round(v, digits) for v in (*box[0], *box[1])]


def vertex_normals(positions: F32, indices: U32) -> F32:
    """Area-weighted vertex normals, unit length; follows the triangles' winding.

    Vertices are shared only inside a face, so shading is smooth across a face and sharp at
    the edges between faces, which is how CAD geometry should look.
    """
    count = positions.shape[0]
    if count == 0:
        return np.zeros((0, 3), dtype=np.float32)
    p = positions.astype(np.float64)
    tri = indices.astype(np.intp)
    a, b, c = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
    weights = np.repeat(np.cross(b - a, c - a), 3, axis=0)
    corners = tri.ravel()
    acc = np.empty((count, 3), dtype=np.float64)
    for axis in range(3):
        acc[:, axis] = np.bincount(corners, weights=weights[:, axis], minlength=count)
    length = np.linalg.norm(acc, axis=1)
    lonely = length == 0.0
    acc[lonely] = (0.0, 0.0, 1.0)
    length[lonely] = 1.0
    return (acc / length[:, None]).astype(np.float32)


def weld(corners: F32, corner_keys: U32) -> tuple[F32, U32]:
    """Merge triangle corners with equal key and bit-equal position; returns (positions, indices).

    ``corners`` holds three rows per triangle; ``corner_keys`` one key per corner (the face id),
    so corners on the edge between two faces stay separate and keep their own normals.
    """
    count = corners.shape[0]
    if count == 0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.uint32)
    # Adding zero folds -0.0 into 0.0, so the bit patterns of equal coordinates agree.
    xyz = np.ascontiguousarray(corners, dtype=np.float32) + np.float32(0.0)
    keys = np.empty((count, 4), dtype=np.uint32)
    keys[:, 0] = corner_keys
    keys[:, 1:] = xyz.view(np.uint32)
    rows = keys.view(np.dtype((np.void, 16))).ravel()
    _, first, inverse = np.unique(rows, return_index=True, return_inverse=True)
    positions = np.ascontiguousarray(xyz[first])
    indices = np.ascontiguousarray(inverse.reshape(-1, 3), dtype=np.uint32)
    return positions, indices
