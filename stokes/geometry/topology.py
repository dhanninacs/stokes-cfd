"""Welded topology of part meshes: facet geometry, shared edges and face adjacency (ADR-0020).

A part's display mesh keeps the vertices of each B-rep face apart (:func:`stokes.geometry.mesh.
weld` merges corners per face, so shading stays sharp at face boundaries), so its index buffer
does not say which triangles meet across a face boundary. Selection growth and the mesh checks
need exactly that, so this module merges vertices again, by bit-equal position, over each whole
part. OCCT meshes a B-rep edge once and the faces on both sides take its nodes, so the corners on
a shared edge are bit-identical after the float32 conversion; a mesh whose seams do not meet
exactly shows free edges, which is what the checks should then report.

Parts are processed in batches: :class:`Surface` concatenates them, with an owner (part index) per
triangle and a face offset per part, because numpy's fixed cost per call dominates small parts
(4,500 pins of 130 triangles take one sort, not 4,500). Face indices in a batch are
``face_offset[part] + face id``.

Import rule: numpy only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.mesh import PartMesh

F32 = NDArray[np.float32]
F64 = NDArray[np.float64]
I64 = NDArray[np.int64]
Mask = NDArray[np.bool_]


@dataclass(frozen=True)
class Surface:
    """The triangles of one or more parts in single arrays (positions in metres)."""

    positions: F32
    """(V, 3) the parts' vertices, one part after another"""
    triangles: I64
    """(T, 3) indices into ``positions``"""
    face: I64
    """(T,) batch face index of each triangle; -1 for a mesh without face ids"""
    owner: I64
    """(T,) index of the part each triangle belongs to"""
    vertex_offset: I64
    """(P + 1,) first vertex of each part; the last entry is V"""
    face_offset: I64
    """(P + 1,) first batch face index of each part; the last entry is the face total"""

    @property
    def parts(self) -> int:
        return int(self.face_offset.shape[0]) - 1

    @property
    def face_total(self) -> int:
        return int(self.face_offset[-1])

    @property
    def triangle_count(self) -> int:
        return int(self.triangles.shape[0])

    def faces_of(self, part: int) -> int:
        return int(self.face_offset[part + 1] - self.face_offset[part])


def part_face_count(mesh: PartMesh) -> int:
    """Faces of a part: its declared count, or more when a face id says so."""
    count = mesh.faces
    for solid in mesh.solids:
        if solid.face_ids is not None and solid.face_ids.shape[0]:
            count = max(count, int(solid.face_ids.max()) + 1)
    return count


def surface_of(meshes: Sequence[PartMesh]) -> Surface:
    """Concatenate parts (each possibly several solids) into one :class:`Surface`."""
    positions: list[F32] = []
    triangles: list[I64] = []
    faces: list[I64] = []
    owners: list[I64] = []
    vertex_offset = [0]
    face_offset = [0]
    vertices = 0
    for index, mesh in enumerate(meshes):
        for solid in mesh.solids:
            count = solid.triangle_count
            positions.append(solid.positions)
            triangles.append(solid.indices.astype(np.int64) + vertices)
            if solid.face_ids is None:
                faces.append(np.full(count, -1, dtype=np.int64))
            else:
                faces.append(solid.face_ids.astype(np.int64) + face_offset[-1])
            owners.append(np.full(count, index, dtype=np.int64))
            vertices += solid.vertex_count
        vertex_offset.append(vertices)
        face_offset.append(face_offset[-1] + part_face_count(mesh))
    return Surface(
        np.concatenate(positions).astype(np.float32, copy=False)
        if positions
        else np.zeros((0, 3), dtype=np.float32),
        np.concatenate(triangles) if triangles else np.zeros((0, 3), dtype=np.int64),
        np.concatenate(faces) if faces else np.zeros(0, dtype=np.int64),
        np.concatenate(owners) if owners else np.zeros(0, dtype=np.int64),
        np.asarray(vertex_offset, dtype=np.int64),
        np.asarray(face_offset, dtype=np.int64),
    )


@dataclass(frozen=True)
class Facets:
    """Per-triangle geometry in float64."""

    normal: F64
    """(T, 3) unit normal following the winding; zero for a triangle without area"""
    area: F64
    """(T,) area, square metres"""
    centroid: F64
    """(T, 3)"""
    edge2: F64
    """(T, 3) squared length of side k (corner k to corner k + 1)"""
    signed_volume: F64
    """(T,) a . (b x c) / 6, whose sum over a closed surface is its enclosed volume"""


def facets_of(surface: Surface) -> Facets:
    p = surface.positions.astype(np.float64)
    tri = surface.triangles
    a, b, c = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
    cross = np.cross(b - a, c - a)
    twice = np.linalg.norm(cross, axis=1)
    normal = np.zeros_like(cross)
    np.divide(cross, twice[:, None], out=normal, where=twice[:, None] > 0.0)
    sides = np.stack([b - a, c - b, a - c], axis=1)
    return Facets(
        normal,
        0.5 * twice,
        (a + b + c) / 3.0,
        np.einsum("tki,tki->tk", sides, sides),
        np.einsum("ti,ti->t", a, np.cross(b, c)) / 6.0,
    )


def vertex_owner(surface: Surface) -> I64:
    return np.repeat(np.arange(surface.parts, dtype=np.int64), np.diff(surface.vertex_offset))


def weld_ids(surface: Surface) -> tuple[I64, int]:
    """One id per vertex, shared by the vertices of a part that sit at the same position."""
    count = int(surface.positions.shape[0])
    if count == 0:
        return np.zeros(0, dtype=np.int64), 0
    # Adding zero folds -0.0 into 0.0, so equal coordinates have equal bits (as mesh.weld does).
    xyz = np.ascontiguousarray(surface.positions, dtype=np.float32) + np.float32(0.0)
    keys = np.empty((count, 4), dtype=np.uint32)
    keys[:, 0] = vertex_owner(surface)
    keys[:, 1:] = xyz.view(np.uint32)
    rows = keys.view(np.dtype((np.void, 16))).ravel()
    _, inverse = np.unique(rows, return_inverse=True)
    ids = np.asarray(inverse, dtype=np.int64).reshape(-1)
    return ids, int(ids.max()) + 1


@dataclass(frozen=True)
class Edges:
    """Undirected edges between welded vertices and the triangle sides that lie on them.

    Side ``3 * t + k`` of triangle ``t`` runs from corner ``k`` to corner ``k + 1``. A side whose
    ends weld into one vertex (a collapsed triangle) lies on no edge; ``collapsed`` marks it.
    """

    side: I64
    """(S,) the sides on edges, grouped by edge"""
    start: I64
    """(E,) where each edge's sides begin in ``side``"""
    count: I64
    """(E,) sides on each edge: 1 for a free edge, 2 for a manifold one, more otherwise"""
    forward: Mask
    """(3T,) whether each side runs from the lower to the higher welded vertex id"""
    length: F64
    """(E,) edge length, metres"""
    owner: I64
    """(E,) part of each edge"""
    collapsed: Mask
    """(3T,) whether each side is collapsed"""

    @property
    def edge_count(self) -> int:
        return int(self.start.shape[0])


def edges_of(surface: Surface) -> Edges:
    welded, count = weld_ids(surface)
    corners = welded[surface.triangles] if surface.triangle_count else np.zeros((0, 3), np.int64)
    head = corners.reshape(-1)
    tail = np.roll(corners, -1, axis=1).reshape(-1)
    low, high = np.minimum(head, tail), np.maximum(head, tail)
    collapsed = low == high
    keys = low * max(count, 1) + high
    sides = np.flatnonzero(~collapsed)
    side = sides[np.argsort(keys[sides], kind="stable")]
    ordered = keys[side]
    if side.shape[0]:
        first = np.empty(side.shape[0], dtype=bool)
        first[0] = True
        np.not_equal(ordered[1:], ordered[:-1], out=first[1:])
        start = np.flatnonzero(first)
    else:
        start = np.zeros(0, dtype=np.int64)
    runs = np.diff(np.append(start, side.shape[0])).astype(np.int64)
    lead = side[start]
    tri, k = lead // 3, lead % 3
    p0 = surface.positions[surface.triangles[tri, k]].astype(np.float64)
    p1 = surface.positions[surface.triangles[tri, (k + 1) % 3]].astype(np.float64)
    return Edges(
        side,
        start.astype(np.int64),
        runs,
        head < tail,
        np.linalg.norm(p1 - p0, axis=1),
        surface.owner[tri],
        collapsed,
    )


@dataclass(frozen=True)
class Adjacency:
    """Pairs of batch faces that share at least one triangle edge, and how smoothly they meet."""

    pairs: I64
    """(Q, 2) lower then higher batch face index"""
    cos: F64
    """(Q,) mean cosine of the angle between the facet normals on either side of the shared
    edges, weighted by edge length; NaN when every shared edge borders a triangle without area"""


def adjacency_of(surface: Surface, facets: Facets, edges: Edges) -> Adjacency:
    """Face pairs over the welded edges. A non-manifold edge pairs each of its sides with its
    first one."""
    total = max(surface.face_total, 1)
    run_start = np.repeat(edges.start, edges.count)
    extra = np.arange(edges.side.shape[0]) != run_start
    s0, s1 = edges.side[run_start[extra]], edges.side[extra]
    t0, t1 = s0 // 3, s1 // 3
    f0, f1 = surface.face[t0], surface.face[t1]
    keep = (f0 != f1) & (f0 >= 0) & (f1 >= 0)
    if not keep.any():
        return Adjacency(np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.float64))
    edge = np.repeat(np.arange(edges.edge_count), edges.count)[extra][keep]
    t0, t1, f0, f1 = t0[keep], t1[keep], f0[keep], f1[keep]
    solid = (facets.area[t0] > 0.0) & (facets.area[t1] > 0.0)
    weight = edges.length[edge] * solid
    cos = np.einsum("ti,ti->t", facets.normal[t0], facets.normal[t1])
    key = np.minimum(f0, f1) * total + np.maximum(f0, f1)
    unique, inverse = np.unique(key, return_inverse=True)
    inverse = inverse.reshape(-1)
    weights = np.bincount(inverse, weights=weight, minlength=unique.shape[0])
    sums = np.bincount(inverse, weights=weight * cos, minlength=unique.shape[0])
    mean = np.full(unique.shape[0], np.nan)
    np.divide(sums, weights, out=mean, where=weights > 0.0)
    return Adjacency(np.stack([unique // total, unique % total], axis=1), mean)


def split_adjacency(surface: Surface, adjacency: Adjacency) -> list[Adjacency]:
    """Per-part adjacency in each part's own face ids (pairs never cross parts: the weld is per
    part, and the pairs come sorted by their lower face, so by part)."""
    owner = np.searchsorted(surface.face_offset, adjacency.pairs[:, 0], side="right") - 1
    bounds = np.searchsorted(owner, np.arange(surface.parts + 1), side="left")
    result = []
    for part in range(surface.parts):
        lo, hi = int(bounds[part]), int(bounds[part + 1])
        result.append(
            Adjacency(adjacency.pairs[lo:hi] - surface.face_offset[part], adjacency.cos[lo:hi])
        )
    return result
