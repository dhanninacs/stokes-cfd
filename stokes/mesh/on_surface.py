"""Which triangles lie on a surface made of other triangles, whatever either's tessellation, and
what is left of them off it.

The multi-region snapshot (`stokes.mesh.conjugate`) must not stage a boundary triangle on a zone
surface (it makes the shared wall an adiabatic baffle), and must not leave a hole in the outer
skin either (the mesh leaks out through it). Two bodies tessellated independently (a STEP
assembly, or two bodies meshed apart) share a wall with no triangle centre in common, so a match
of centres finds only exact copies. Here a triangle is on the surface when it lies in the plane of
one of the surface's triangles, within a small distance, near parallel, and overlaps it: a skin
triangle meeting the surface at a rim (the slivers the collar is for) is across it, not along it,
and is kept.

**Cut, not dropped** (2026-11-24, third pass): a skin triangle can lie partly on a zone and partly
off it, where a channel's wall crosses the face of the block it runs through. Dropping it whole
when its centre was on the zone left a hole in the true outer skin just outside the mouth
(0.3 to 1.1 mm2 per mouth, with a second solid making the block a zone); keeping it whole left
it on the zone. `off_surface` cuts each such triangle along the zone triangles' edges, in its own
plane, and keeps the part off the zone.

**Headless and numpy only**: the surface's triangles are binned on a grid by their boxes; the few
triangles partly on the surface are cut in plain Python, convex polygon by convex triangle.
"""

from __future__ import annotations

from typing import Any

#: The two triangles are near parallel when their normals agree to this cosine (about 18 degrees).
PARALLEL = 0.95

#: The most grid cells one surface triangle is binned into before the grid is made coarser.
MAX_BINS = 64

#: A piece of a cut triangle smaller than this share of it is float noise along an edge the two
#: tessellations share, and is left out; a remainder within this share of the whole is the whole.
SLIVER = 1e-9

#: How many triangles are looked up at once (bounds the pairs held in memory).
CHUNK = 20_000


def on_surface(corners: Any, surface: Any, gap: float) -> Any:  # Any: numpy arrays
    """Which of ``corners`` (T, 3, 3) lie on ``surface`` (S, 3, 3): a mask over T.

    A triangle is on it when its centre is within ``gap`` of the plane of a surface triangle and
    projects inside it, and the two normals are within `PARALLEL` of each other either way round.
    """
    import numpy as np

    corners = np.asarray(corners, dtype="float64").reshape(-1, 3, 3)
    surface = np.asarray(surface, dtype="float64").reshape(-1, 3, 3)
    found = np.zeros(len(corners), dtype=bool)
    if len(corners) == 0 or len(surface) == 0:
        return found
    centre = corners.mean(axis=1)
    grid = _Grid(surface, gap, centre)
    rows, cols = grid.pairs(centre, centre)
    if rows.size == 0:
        return found
    normal = _unit_normals(corners)
    parallel = np.abs(np.einsum("ij,ij->i", normal[rows], grid.normal[cols])) >= PARALLEL
    rows, cols = rows[parallel], cols[parallel]
    a, b, c = surface[cols, 0], surface[cols, 1], surface[cols, 2]
    p = centre[rows]
    near = np.abs(np.einsum("ij,ij->i", p - a, grid.normal[cols])) <= gap
    hit = near & _projects_inside(p, a, b, c)
    found[rows[hit]] = True
    return found


def off_surface(corners: Any, surface: Any, gap: float) -> Any:  # Any: numpy arrays
    """What of ``corners`` (T, 3, 3) is off ``surface`` (S, 3, 3), as triangles (N, 3, 3).

    A triangle lying wholly on the surface is left out, one clear of it is kept as it is, and one
    partly on it is cut along the surface triangles' edges in its own plane into the triangles of
    the part off it, wound as it was. A surface triangle counts where it is near parallel to the
    triangle (`PARALLEL`, either way round), one of the two centres is within ``gap`` of the
    other's plane, and their boxes (grown by ``gap``) meet.
    """
    import numpy as np

    corners = np.asarray(corners, dtype="float64").reshape(-1, 3, 3)
    surface = np.asarray(surface, dtype="float64").reshape(-1, 3, 3)
    if len(corners) == 0 or len(surface) == 0:
        return corners
    grid = _Grid(surface, gap, corners.reshape(-1, 3))
    normal = _unit_normals(corners)
    centre = corners.mean(axis=1)
    s_centre = surface.mean(axis=1)
    keep = np.ones(len(corners), dtype=bool)
    cut: dict[int, list[int]] = {}
    for start in range(0, len(corners), CHUNK):
        chunk = slice(start, start + CHUNK)
        rows, cols = grid.pairs(corners[chunk].min(axis=1), corners[chunk].max(axis=1))
        if rows.size == 0:
            continue
        rows = rows + start
        low = np.maximum(corners[rows].min(axis=1), surface[cols].min(axis=1))
        high = np.minimum(corners[rows].max(axis=1), surface[cols].max(axis=1))
        meet = (high - low >= -gap).all(axis=1)
        parallel = np.abs(np.einsum("ij,ij->i", normal[rows], grid.normal[cols])) >= PARALLEL
        own = np.abs(np.einsum("ij,ij->i", centre[rows] - surface[cols, 0], grid.normal[cols]))
        their = np.abs(np.einsum("ij,ij->i", s_centre[cols] - corners[rows, 0], normal[rows]))
        chosen = meet & parallel & ((own <= gap) | (their <= gap))
        rows, cols = rows[chosen], cols[chosen]
        if rows.size == 0:
            continue
        a, b, c = surface[cols, 0], surface[cols, 1], surface[cols, 2]
        # Wholly inside one surface triangle: off the surface entirely, no cut needed.
        inside = np.ones(len(rows), dtype=bool)
        for k in range(3):
            inside &= _projects_inside(corners[rows, k], a, b, c)
        keep[rows[inside]] = False
        for row, col in zip(rows.tolist(), cols.tolist(), strict=True):
            if keep[row]:
                cut.setdefault(row, []).append(col)
    pieces: list[Any] = []
    for row, cols in cut.items():
        if not keep[row]:
            continue
        rest = _cut(corners[row], surface[cols])
        if rest is None:
            continue
        keep[row] = False
        if len(rest):
            pieces.append(rest)
    return np.concatenate([corners[keep], *pieces]) if pieces else corners[keep]


class _Grid:
    """A surface's triangles binned on a grid by their boxes grown by ``gap``: a cell about as
    wide as the typical triangle, wider when one triangle would fill too many cells. ``reach``
    is the points the grid must also number (its origin and size cover them)."""

    def __init__(self, surface: Any, gap: float, reach: Any) -> None:  # Any: numpy arrays
        import numpy as np

        self.normal = _unit_normals(surface)
        low = surface.min(axis=1) - gap
        high = surface.max(axis=1) + gap
        extent = float(np.max(high - low, axis=1).max())
        self.origin = np.minimum(low.min(axis=0), reach.min(axis=0))
        self.width = max(float(np.median(np.max(high - low, axis=1))), 4.0 * gap, extent / MAX_BINS)
        first = self._cell(low)
        last = self._cell(high)
        size = int(np.max(last.max(axis=0), initial=0)) + 2
        self.size = max(size, int(self._cell(reach).max()) + 2)
        owner, cell = _spans(first, last)
        keys = self._key(cell)
        order = np.argsort(keys, kind="stable")
        self.keys, self.owner = keys[order], owner[order]

    def _cell(self, at: Any) -> Any:  # Any: numpy arrays
        import numpy as np

        return np.floor((at - self.origin) / self.width).astype("int64")

    def _key(self, cell: Any) -> Any:  # Any: numpy arrays
        return (cell[:, 0] * self.size + cell[:, 1]) * self.size + cell[:, 2]

    def pairs(self, low: Any, high: Any) -> tuple[Any, Any]:  # Any: numpy arrays
        """Every (query, surface triangle) pair sharing a cell, for the boxes ``low``..``high``
        (Q, 3) each; each pair once."""
        import numpy as np

        first = np.clip(self._cell(low), 0, self.size - 1)
        last = np.clip(self._cell(high), 0, self.size - 1)
        query, cell = _spans(first, last)
        wanted = self._key(cell)
        start = np.searchsorted(self.keys, wanted, side="left")
        stop = np.searchsorted(self.keys, wanted, side="right")
        many = stop - start
        rows = np.repeat(query, many)
        if rows.size == 0:
            return rows, rows
        at = np.arange(int(many.sum())) - np.repeat(np.cumsum(many) - many, many)
        cols = self.owner[np.repeat(start, many) + at]
        if (last > first).any():
            both = np.unique(rows * len(self.normal) + cols)
            rows, cols = both // len(self.normal), both % len(self.normal)
        return rows, cols


def _spans(first: Any, last: Any) -> tuple[Any, Any]:  # Any: numpy arrays
    """Every (box, cell) of boxes spanning ``first``..``last`` cells (inclusive), as the box's
    index and the cell."""
    import numpy as np

    span = last - first + 1
    count = span.prod(axis=1)
    owner = np.repeat(np.arange(len(first)), count)
    step = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
    sx, sy = span[owner, 0], span[owner, 1]
    cell = first[owner] + np.stack([step % sx, (step // sx) % sy, step // (sx * sy)], axis=1)
    return owner, cell


def _cut(triangle: Any, covers: Any) -> Any:  # Any: numpy arrays
    """``triangle`` (3, 3) less ``covers`` (K, 3, 3), each projected on its plane: the triangles
    (N, 3, 3) of what is left, wound as it was, or None when nothing is taken from it."""
    import numpy as np

    origin = triangle[0]
    u = triangle[1] - origin
    length = float(np.linalg.norm(u))
    normal = np.cross(u, triangle[2] - origin)
    area = float(np.linalg.norm(normal))
    if length == 0.0 or area == 0.0:
        return None
    u = u / length
    normal = normal / area
    v = np.cross(normal, u)

    def flat(points: Any) -> list[tuple[float, float]]:  # Any: numpy array
        moved = points - origin
        return list(zip((moved @ u).tolist(), (moved @ v).tolist(), strict=True))

    whole = 0.5 * area
    pieces = [flat(triangle)]
    for cover in covers:
        shape = flat(cover)
        signed = _area(shape)
        if abs(signed) <= SLIVER * whole:
            continue
        if signed < 0.0:
            shape.reverse()
        found: list[list[tuple[float, float]]] = []
        for piece in pieces:
            found += _minus(piece, shape, SLIVER * whole)
        pieces = found
        if not pieces:
            break
    left = sum(_area(piece) for piece in pieces)
    if left >= (1.0 - SLIVER) * whole:
        return None
    # A fan of each piece, less the triangles of no area a corner on a straight edge makes.
    out = [
        fan
        for piece in pieces
        for k in range(1, len(piece) - 1)
        if _area(fan := [piece[0], piece[k], piece[k + 1]]) > SLIVER * whole
    ]
    if not out:
        return np.zeros((0, 3, 3))
    flat_corners = np.asarray(out, dtype="float64")  # (N, 3, 2)
    return origin + flat_corners[..., :1] * u + flat_corners[..., 1:] * v


def _area(polygon: list[tuple[float, float]]) -> float:
    """The signed area of a polygon, positive anticlockwise."""
    total = 0.0
    for k in range(len(polygon)):
        x0, y0 = polygon[k - 1]
        x1, y1 = polygon[k]
        total += x0 * y1 - x1 * y0
    return 0.5 * total


def _minus(
    piece: list[tuple[float, float]], cover: list[tuple[float, float]], tiny: float
) -> list[list[tuple[float, float]]]:
    """The convex polygon ``piece`` less the anticlockwise triangle ``cover``, as convex pieces
    (each outside one of its edges and inside the ones before), none of area ``tiny`` or less."""
    found: list[list[tuple[float, float]]] = []
    rest = piece
    for k in range(3):
        a, b = cover[k], cover[(k + 1) % 3]
        outside = _clip(rest, b, a)
        if _area(outside) > tiny:
            found.append(outside)
        rest = _clip(rest, a, b)
        if _area(rest) <= tiny:
            break
    return found


def _clip(
    polygon: list[tuple[float, float]], a: tuple[float, float], b: tuple[float, float]
) -> list[tuple[float, float]]:
    """The part of a convex polygon to the left of the line from ``a`` to ``b``
    (Sutherland-Hodgman, one edge)."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    out: list[tuple[float, float]] = []
    count = len(polygon)
    for k in range(count):
        px, py = polygon[k - 1]
        qx, qy = polygon[k]
        sp = dx * (py - ay) - dy * (px - ax)
        sq = dx * (qy - ay) - dy * (qx - ax)
        if sq >= 0.0:
            if sp < 0.0:
                t = sp / (sp - sq)
                out.append((px + t * (qx - px), py + t * (qy - py)))
            out.append((qx, qy))
        elif sp >= 0.0:
            t = sp / (sp - sq)
            out.append((px + t * (qx - px), py + t * (qy - py)))
    return out


def _unit_normals(corners: Any) -> Any:  # Any: numpy arrays
    import numpy as np

    normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    length = np.linalg.norm(normal, axis=1)
    return normal / np.where(length > 0.0, length, 1.0)[:, None]


def _projects_inside(p: Any, a: Any, b: Any, c: Any) -> Any:  # Any: numpy arrays
    """Whether each point's projection on its triangle's plane is inside the triangle (edges
    included, to a part in a million of the triangle)."""
    import numpy as np

    ab, ac, ap = b - a, c - a, p - a
    d00 = np.einsum("ij,ij->i", ab, ab)
    d01 = np.einsum("ij,ij->i", ab, ac)
    d11 = np.einsum("ij,ij->i", ac, ac)
    d20 = np.einsum("ij,ij->i", ap, ab)
    d21 = np.einsum("ij,ij->i", ap, ac)
    denom = d00 * d11 - d01 * d01
    ok = denom > 0.0
    safe = np.where(ok, denom, 1.0)
    v = (d11 * d20 - d01 * d21) / safe
    w = (d00 * d21 - d01 * d20) / safe
    slack = -1e-6
    return np.asarray(ok & (v >= slack) & (w >= slack) & (v + w <= 1.0 - slack))
