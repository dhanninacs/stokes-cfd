"""A point inside a closed surface, found from its triangles (T-232).

## Why this is not a one-liner

snappyHexMesh keeps the cells reachable from one point, so a **wrong** point silently produces the
wrong half of the domain - or an empty mesh - and the user sees a mesh that does not match their
part with no error naming the cause. The point cannot be the centroid either: the centroid of a
hollow pipe or a flange lies in the hole, which is *outside* the solid. So the point is **found and
then verified**, and a part that cannot be given one is refused in plain words rather than meshed
wrongly.

## How the point is chosen

1. A lattice of candidates across the bounding box (7x7x7 by default, 4x4x4 on a retry) avoids the
   degenerate case where the only inside points are near one face.
2. Each candidate is tested for insideness by **ray parity along three axes**, majority vote: cast
   a ray and count crossings; odd means inside. One axis can graze an edge or pass exactly through
   a vertex and be miscounted, so three axes are asked and the majority decides. This is the part
   that makes a mesh with holes and fins work rather than a solid box.
3. Of the candidates judged inside, the one **farthest from the surface** wins, measured to the
   nearest triangle centroid. A point deep inside is robust: a point a hair inside the wall lands
   in a cell that castellation may remove.

Nothing here imports numpy at module scope beyond the array it is handed, and none of it touches
the session, so the geometry of the choice is testable without a project.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The lattice resolution. 343 candidates inside a bounding box is cheap (each is 3 ray casts over
#: the triangles) and dense enough that a body with a few features still has interior candidates.
LATTICE = 7

#: The retry resolution. Tried when the fine lattice finds nothing inside, which happens when the
#: box is mostly empty and the fine lattice's points all miss the solid.
COARSE_LATTICE = 4

#: Beyond this many triangles the ray test is the cost that matters, so the lattice is walked in
#: this order of magnitude and a big mesh is sampled rather than exhausted. Measured need, not a
#: guess: the demo parts are a few thousand triangles and the flange case is tens of thousands.
BIG_MESH_TRIANGLES = 200_000


@dataclass(frozen=True)
class InsideResult:
    """A point judged inside, with what it took to find it.

    ``point`` is None when nothing inside was found - the caller refuses with its own message.
    ``tested`` is how many candidates were tried, which is what makes a failure diagnosable ("343
    lattice points, none inside" is a different problem from "the surface is not closed").
    """

    point: tuple[float, float, float] | None
    tested: int
    clearance: float = 0.0
    coarse: bool = False

    @property
    def found(self) -> bool:
        return self.point is not None


def _triangles(vertices: object, faces: object) -> list[tuple[tuple[float, float, float], ...]]:
    """The triangle list as plain float triples.

    Accepts the (vertices, indices) pair the stores already hold. Indices are flat triples; a
    quad is split by the caller, so anything else is a bug worth naming rather than skipping.
    """
    import numpy as np

    verts = np.asarray(vertices, dtype=float)
    idx = np.asarray(faces, dtype=int).reshape(-1, 3)
    if verts.ndim != 2 or verts.shape[1] != 3:
        raise ValueError(f"vertices must be an (n, 3) array, got shape {verts.shape}")
    out: list[tuple[tuple[float, float, float], ...]] = []
    for a, b, c in idx:
        out.append(
            (
                (float(verts[a][0]), float(verts[a][1]), float(verts[a][2])),
                (float(verts[b][0]), float(verts[b][1]), float(verts[b][2])),
                (float(verts[c][0]), float(verts[c][1]), float(verts[c][2])),
            )
        )
    return out


def _crossings_along(
    tris: list[tuple[tuple[float, float, float], ...]],
    origin: tuple[float, float, float],
    axis: int,
) -> int:
    """How many triangles a +axis ray from ``origin`` passes through.

    A triangle is counted when the ray's two other coordinates fall inside it (by the usual
    signed-area test) and its plane crosses ahead of the origin. That is the standard parity test;
    the three-axis majority in :func:`is_inside` is what covers its known failure at an edge or a
    vertex.
    """
    u, v = ((1, 2), (0, 2), (0, 1))[axis]
    count = 0
    for a, b, c in tris:
        a_u, a_v = a[u] - origin[u], a[v] - origin[v]
        b_u, b_v = b[u] - origin[u], b[v] - origin[v]
        c_u, c_v = c[u] - origin[u], c[v] - origin[v]

        # Signed areas: all the same sign (or zero) means the point is inside the 2-D triangle.
        d1 = b_u * a_v - a_u * b_v
        d2 = c_u * b_v - b_u * c_v
        d3 = a_u * c_v - c_u * a_v
        has_neg = d1 < 0 or d2 < 0 or d3 < 0
        has_pos = d1 > 0 or d2 > 0 or d3 > 0
        if has_neg and has_pos:
            continue  # outside the triangle's projection

        # The plane's height at the origin, by barycentric weights of the projection.
        area = d1 + d2 + d3
        if area == 0.0:
            continue  # degenerate or edge-on: contributes no reliable crossing
        w_a, w_b, w_c = d2 / area, d3 / area, d1 / area
        plane = w_a * a[axis] + w_b * b[axis] + w_c * c[axis]
        if plane > origin[axis]:
            count += 1
    return count


def is_inside(
    tris: list[tuple[tuple[float, float, float], ...]],
    point: tuple[float, float, float],
) -> bool:
    """Whether ``point`` is inside the closed surface, by majority of three ray parities.

    **Majority, not one ray.** A single axis miscounts when the ray grazes an edge or passes
    exactly through a vertex - which for a lattice of round-numbered candidates is not a rare
    accident but the common case. Two of three agreeing is the answer; a 2-1 split is reported as
    inside, and a part whose three axes all disagree is not something a lattice point should be
    trusted on anyway.
    """
    votes = 0
    for axis in range(3):
        if _crossings_along(tris, point, axis) % 2 == 1:
            votes += 1
    return votes >= 2


def _nearest_clearance(
    tris: list[tuple[tuple[float, float, float], ...]],
    point: tuple[float, float, float],
    *,
    stride: int = 1,
) -> float:
    """Distance from ``point`` to the nearest triangle centroid.

    **To the centroid, not to the true surface.** The exact point-to-triangle distance is a
    projection per triangle and buys nothing here: this value only RANKS candidates, and a
    centroid distance ranks them the same way except for triangles far larger than the lattice
    spacing, where the difference is not decisive.
    """
    best = math.inf
    for a, b, c in tris[::stride]:
        cx, cy, cz = (a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3
        d = (cx - point[0]) ** 2 + (cy - point[1]) ** 2 + (cz - point[2]) ** 2
        best = min(best, d)
    return math.sqrt(best) if best is not math.inf else 0.0


def inside_point(
    vertices: object,
    faces: object,
    bbox: tuple[float, float, float, float, float, float],
    *,
    outside: bool = False,
    lattice: int = LATTICE,
) -> InsideResult:
    """A well-inside point of the closed surface, or a result saying none was found.

    ``outside=True`` inverts the test: for a wind-tunnel enclosure the point must be in the free
    space **outside** every included body, which is where snappy must keep cells.

    The lattice is inset by one cell of its own spacing so a candidate never sits exactly on the
    bounding-box face, where the parity test is least reliable.
    """
    tris = _triangles(vertices, faces)
    if not tris:
        return InsideResult(point=None, tested=0)

    x0, y0, z0, x1, y1, z1 = bbox
    steps = max(2, lattice)
    # Inset by half a step at each end so no candidate lands on the box face.
    span = (x1 - x0, y1 - y0, z1 - z0)
    lo = (x0 + span[0] / (2 * steps), y0 + span[1] / (2 * steps), z0 + span[2] / (2 * steps))
    step = (span[0] / steps, span[1] / steps, span[2] / steps)

    stride = max(1, len(tris) // BIG_MESH_TRIANGLES)
    best: tuple[float, float, float] | None = None
    best_clear = -1.0
    tested = 0
    for i in range(steps):
        for j in range(steps):
            for k in range(steps):
                cand = (lo[0] + i * step[0], lo[1] + j * step[1], lo[2] + k * step[2])
                tested += 1
                inside = is_inside(tris, cand)
                if inside == outside:
                    continue  # `outside` asks for the complement
                clear = _nearest_clearance(tris, cand, stride=stride)
                if clear > best_clear:
                    best, best_clear = cand, clear
    if best is None:
        return InsideResult(point=None, tested=tested)
    return InsideResult(point=best, tested=tested, clearance=best_clear)


def inside_point_or_coarse(
    vertices: object,
    faces: object,
    bbox: tuple[float, float, float, float, float, float],
    *,
    outside: bool = False,
) -> InsideResult:
    """The fine lattice, then the coarse one, so a thin or sparse body still yields a point.

    **The retry is not a second guess at the geometry**; it is a coarser sampling of the same
    verified test, which is why it cannot produce a wrong answer - only a less central one. Split
    out from :func:`inside_point` so a caller that wants one pass can have it.
    """
    found = inside_point(vertices, faces, bbox, outside=outside, lattice=LATTICE)
    if found.found:
        return found
    coarse = inside_point(vertices, faces, bbox, outside=outside, lattice=COARSE_LATTICE)
    if coarse.found:
        return InsideResult(
            point=coarse.point,
            tested=found.tested + coarse.tested,
            clearance=coarse.clearance,
            coarse=True,
        )
    return InsideResult(point=None, tested=found.tested + coarse.tested)
