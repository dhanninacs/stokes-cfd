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
from typing import Any

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
    import numpy as np

    verts = np.asarray(vertices, dtype=float)
    idx = np.asarray(faces, dtype=int).reshape(-1, 3)
    if verts.ndim != 2 or verts.shape[1] != 3:
        raise ValueError(f"vertices must be an (n, 3) array, got shape {verts.shape}")
    if len(idx) == 0:
        return InsideResult(point=None, tested=0)
    tris = verts[idx]  # (n, 3, 3): triangle, corner, coordinate

    x0, y0, z0, x1, y1, z1 = bbox
    steps = max(2, lattice)
    # Inset by half a step at each end so no candidate lands on the box face.
    span = (x1 - x0, y1 - y0, z1 - z0)
    lo = (x0 + span[0] / (2 * steps), y0 + span[1] / (2 * steps), z0 + span[2] / (2 * steps))
    step = (span[0] / steps, span[1] / steps, span[2] / steps)
    coords = tuple([lo[a] + n * step[a] for n in range(steps)] for a in range(3))

    # **One projection per lattice LINE, not per candidate** (measured 2026-11-19: 343 candidates
    # x 3 rays x 14 k triangles in a Python loop took 10 s on the session thread after every
    # edit, 21 s on a busy machine). The candidates along an axis share their other two
    # coordinates, so the triangles a ray from any of them can cross are found once for the line;
    # each candidate then only counts the crossings ahead of it. The arithmetic per triangle is
    # `_crossings_along`'s, term for term, so the votes are the ones `is_inside` gives.
    odd = np.zeros((3, steps, steps, steps), dtype=bool)  # [axis, i, j, k]
    for axis in range(3):
        u, v = ((1, 2), (0, 2), (0, 1))[axis]
        for p, at_u in enumerate(coords[u]):
            for q, at_v in enumerate(coords[v]):
                heights = _line_heights(tris, at_u, at_v, axis)
                along = np.asarray(coords[axis])
                crossed = (heights[None, :] > along[:, None]).sum(axis=1) % 2 == 1
                where: list[Any] = [slice(None)] * 3
                where[u], where[v] = p, q
                odd[(axis, *where)] = crossed
    inside_votes = odd.sum(axis=0) >= 2

    # **Clearance is to the nearest triangle CENTROID, not to the true surface.** The exact
    # point-to-triangle distance is a projection per triangle and buys nothing here: the value
    # only RANKS candidates, and a centroid distance ranks them the same way except for triangles
    # far larger than the lattice spacing, where the difference is not decisive.
    stride = max(1, len(tris) // BIG_MESH_TRIANGLES)
    centroids = tris[::stride].sum(axis=1) / 3
    best: tuple[float, float, float] | None = None
    best_clear = -1.0
    tested = 0
    for i in range(steps):
        for j in range(steps):
            for k in range(steps):
                cand = (coords[0][i], coords[1][j], coords[2][k])
                tested += 1
                if bool(inside_votes[i, j, k]) == outside:
                    continue  # `outside` asks for the complement
                gap = centroids - np.asarray(cand)
                clear = math.sqrt(float((gap * gap).sum(axis=1).min()))
                if clear > best_clear:
                    best, best_clear = cand, clear
    if best is None:
        return InsideResult(point=None, tested=tested)
    return InsideResult(point=best, tested=tested, clearance=best_clear)


def _line_heights(tris: Any, at_u: float, at_v: float, axis: int) -> Any:  # Any: numpy arrays
    """Where the line through (``at_u``, ``at_v``) along ``axis`` meets the triangles.

    The heights (the ``axis`` coordinate) of every triangle whose projection holds the line, by
    the same signed areas and barycentric weights as :func:`_crossings_along`: a ray from a point
    on the line crosses exactly the triangles whose height is above it.
    """
    import numpy as np

    u, v = ((1, 2), (0, 2), (0, 1))[axis]
    a_u, a_v = tris[:, 0, u] - at_u, tris[:, 0, v] - at_v
    b_u, b_v = tris[:, 1, u] - at_u, tris[:, 1, v] - at_v
    c_u, c_v = tris[:, 2, u] - at_u, tris[:, 2, v] - at_v
    d1 = b_u * a_v - a_u * b_v
    d2 = c_u * b_v - b_u * c_v
    d3 = a_u * c_v - c_u * a_v
    has_neg = (d1 < 0) | (d2 < 0) | (d3 < 0)
    has_pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
    area = d1 + d2 + d3
    hit = ~(has_neg & has_pos) & (area != 0.0)
    if not hit.any():
        return np.empty(0)
    area = area[hit]
    return (
        d2[hit] / area * tris[hit, 0, axis]
        + d3[hit] / area * tris[hit, 1, axis]
        + d1[hit] / area * tris[hit, 2, axis]
    )


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


#: A triangle over more squares than this each way is handled by itself in `Columns`: below it,
#: every triangle that reaches a given offset is tested in one pass.
_COLUMN_SPAN = 24


class Columns:
    """A closed surface as the heights it crosses on a grid of parallel lines (ADR-0042).

    The lines run along the axis the surface is shortest along, one through the middle of every
    square of side ``pitch`` across the other two. A point is inside when an odd number of
    crossings on the line of its square lie above it, so the answer is the answer for the point
    moved sideways onto that line: right for any point further than ``0.71 * pitch`` from the
    surface. Building it costs about one test per square a triangle covers, and asking it nothing
    but a search, which is what makes a million triangle centres against a million triangles a
    matter of a second instead of minutes (measured: testing each point against the triangles
    over it took 205 s on a gyroid core).
    """

    def __init__(  # noqa: PLR0915 - one pass over arrays every step shares
        self, vertices: object, faces: object, pitch: float
    ) -> None:
        import numpy as np

        pos = np.asarray(vertices, dtype="float64").reshape(-1, 3)
        tris = pos[np.asarray(faces, dtype="int64").reshape(-1, 3)]
        self.empty = len(tris) == 0
        if self.empty:
            return
        self.low, self.high = pos.min(axis=0), pos.max(axis=0)
        self.axis = int(np.argmin(self.high - self.low))
        self.u, self.v = ((1, 2), (0, 2), (0, 1))[self.axis]
        u, v, axis = self.u, self.v, self.axis
        self.pitch = float(pitch)
        # Line centres sit a hair off round numbers, by unrelated amounts each way, so a line
        # never runs exactly along an edge two triangles share.
        self.origin = np.array([self.low[u] - 0.5012345 * pitch, self.low[v] - 0.5027183 * pitch])
        extent = np.array([self.high[u], self.high[v]]) - self.origin
        self.shape = np.maximum(1, np.ceil(extent / pitch).astype("int64") + 1)
        lo = np.floor((tris[:, :, (u, v)].min(axis=1) - self.origin) / pitch - 0.5).astype("int64")
        hi = np.ceil((tris[:, :, (u, v)].max(axis=1) - self.origin) / pitch - 0.5).astype("int64")
        lo = np.clip(lo, 0, self.shape - 1)
        hi = np.clip(hi, 0, self.shape - 1)
        span = hi - lo
        keys: list[Any] = []
        heights: list[Any] = []

        def cross(rows: Any, iu: Any, iv: Any) -> None:
            """Keep the lines (``iu``, ``iv``) that pass through the triangles ``rows``."""
            t = tris[rows]
            at_u = self.origin[0] + (iu + 0.5) * pitch
            at_v = self.origin[1] + (iv + 0.5) * pitch
            a_u, a_v = t[:, 0, u] - at_u, t[:, 0, v] - at_v
            b_u, b_v = t[:, 1, u] - at_u, t[:, 1, v] - at_v
            c_u, c_v = t[:, 2, u] - at_u, t[:, 2, v] - at_v
            d1 = b_u * a_v - a_u * b_v
            d2 = c_u * b_v - b_u * c_v
            d3 = a_u * c_v - c_u * a_v
            area = d1 + d2 + d3
            hit = ~(((d1 < 0) | (d2 < 0) | (d3 < 0)) & ((d1 > 0) | (d2 > 0) | (d3 > 0)))
            hit &= area != 0.0
            if not hit.any():
                return
            height = (
                d2[hit] * t[hit, 0, axis] + d3[hit] * t[hit, 1, axis] + d1[hit] * t[hit, 2, axis]
            ) / area[hit]
            keys.append(iu[hit] * self.shape[1] + iv[hit])
            heights.append(height)

        wide = (span > _COLUMN_SPAN).any(axis=1)
        narrow = np.flatnonzero(~wide)
        for du in range(int(span[narrow, 0].max(initial=-1)) + 1):
            across = narrow[span[narrow, 0] >= du]
            for dv in range(int(span[across, 1].max(initial=-1)) + 1):
                rows = across[span[across, 1] >= dv]
                cross(rows, lo[rows, 0] + du, lo[rows, 1] + dv)
        # A triangle over many squares takes every line of its own box in one pass.
        for row in np.flatnonzero(wide):
            iu, iv = np.meshgrid(
                np.arange(lo[row, 0], hi[row, 0] + 1),
                np.arange(lo[row, 1], hi[row, 1] + 1),
                indexing="ij",
            )
            cross(np.full(iu.size, row, dtype="int64"), iu.ravel(), iv.ravel())
        key = np.concatenate(keys) if keys else np.zeros(0, dtype="int64")
        height = np.concatenate(heights) if heights else np.zeros(0)
        order = np.lexsort((height, key))
        self.key, self.height = key[order], height[order]
        # One number per crossing that sorts by line first and height second.
        self.floor = float(self.low[axis])
        self.scale = 0.5 / max(float(self.high[axis] - self.low[axis]), 1e-300)
        self.rank = self.key + (self.height - self.floor) * self.scale
        self.ends = np.searchsorted(self.key, np.arange(int(self.shape[0] * self.shape[1])) + 1)

    def inside(self, points: object) -> Any:  # Any: a numpy bool array
        """Which of ``points`` are inside, each judged on the line through its own square."""
        import numpy as np

        pts = np.asarray(points, dtype="float64").reshape(-1, 3)
        out = np.zeros(len(pts), dtype=bool)
        if self.empty or len(pts) == 0:
            return out
        near = np.flatnonzero(np.all((pts >= self.low) & (pts <= self.high), axis=1))
        if near.size == 0:
            return out
        sub = pts[near]
        index = np.floor((sub[:, (self.u, self.v)] - self.origin) / self.pitch).astype("int64")
        index = np.clip(index, 0, self.shape - 1)
        line = index[:, 0] * self.shape[1] + index[:, 1]
        rank = line + (sub[:, self.axis] - self.floor) * self.scale
        above = self.ends[line] - np.searchsorted(self.rank, rank, side="right")
        out[near] = above % 2 == 1
        return out
