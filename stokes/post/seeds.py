"""Where streamlines start: pure seed generation (ADR-0030).

Four ways to produce a list of `[x, y, z]` points, none of which needs a solver or a host:
a **line** (a rake between two points), a **plane** anywhere (a square grid in a plane), a
**sphere** (a point cloud on a shell, evenly spread) and a **patch** (area-weighted points on a
surface's triangles, moved a little into the fluid, which is how a trace starts at an inlet).

Everything here is deterministic: the same arguments give the same points, so a journal replays
and a cached trace is keyed by its arguments. The count is capped at `MAX_SEEDS`, the most one
trace may draw.

Import rules: numpy only. Headless.
"""

from __future__ import annotations

import math

import numpy as np

#: The most seeds one request may produce; the tracer refuses more
#: (`stokes.post.streamlines.MAX_LINES`).
MAX_SEEDS = 200


class SeedsInvalid(ValueError):
    """A seed request that cannot be answered as asked."""

    code = "post.seeds_invalid"


def _count(n: int) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or n < 1 or n > MAX_SEEDS:
        raise SeedsInvalid(f"the number of seeds must be 1 to {MAX_SEEDS}; got {n!r}")
    return n


def _vec(value: object, what: str) -> np.ndarray:
    try:
        v = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise SeedsInvalid(f"{what} must be three numbers") from exc
    if v.shape != (3,) or not np.all(np.isfinite(v)):
        raise SeedsInvalid(f"{what} must be three finite numbers")
    return v


def line_seeds(a: object, b: object, n: int) -> np.ndarray:
    """`n` points evenly spaced from `a` to `b`, both ends included (one point is the midpoint)."""
    n = _count(n)
    p, q = _vec(a, "the first point"), _vec(b, "the second point")
    if n == 1:
        return np.asarray(((p + q) / 2.0)[None, :], dtype=float)
    return np.asarray(
        p[None, :] + np.linspace(0.0, 1.0, n)[:, None] * (q - p)[None, :], dtype=float
    )


def _plane_axes(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors that span the plane with this normal."""
    n = normal / np.linalg.norm(normal)
    helper = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(n, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def plane_seeds(origin: object, normal: object, size: float, n: int) -> np.ndarray:
    """A square grid of about `n` points in the plane through `origin`, `size` metres on a side.

    The grid is `k x k` with `k = floor(sqrt(n))`, so the count is at most `n` and never over
    the cap.
    """
    n = _count(n)
    o, nrm = _vec(origin, "the origin"), _vec(normal, "the normal")
    if not (isinstance(size, (int, float)) and math.isfinite(size) and size > 0):
        raise SeedsInvalid("the plane's size must be a positive number")
    if np.linalg.norm(nrm) == 0:
        raise SeedsInvalid("the plane's normal must not be zero")
    k = max(1, math.isqrt(n))
    u, v = _plane_axes(nrm)
    offsets = np.zeros(1) if k == 1 else np.linspace(-size / 2.0, size / 2.0, k)
    grid_u, grid_v = np.meshgrid(offsets, offsets)
    return np.asarray(
        o[None, :] + grid_u.reshape(-1, 1) * u[None, :] + grid_v.reshape(-1, 1) * v[None, :],
        dtype=float,
    )


def sphere_seeds(center: object, radius: float, n: int) -> np.ndarray:
    """`n` points spread evenly over a sphere's surface (a Fibonacci spiral), `radius` metres
    from `center`."""
    n = _count(n)
    c = _vec(center, "the centre")
    if not (isinstance(radius, (int, float)) and math.isfinite(radius) and radius > 0):
        raise SeedsInvalid("the sphere's radius must be a positive number")
    i = np.arange(n, dtype=float) + 0.5
    z = 1.0 - 2.0 * i / n
    ring = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    angle = math.pi * (1.0 + 5.0**0.5) * i
    unit = np.stack([ring * np.cos(angle), ring * np.sin(angle), z], axis=1)
    return np.asarray(c[None, :] + radius * unit, dtype=float)


def patch_seeds(
    positions: np.ndarray, triangles: np.ndarray, n: int, inward: float = 0.0
) -> np.ndarray:
    """`n` points on a surface's triangles, area-weighted, each moved `inward` metres against
    its normal.

    **How a trace starts at an inlet**: the patch's faces carry outward normals, so moving
    against them
    puts the seed inside the fluid, where the field is defined. The points are the centroids of the
    triangles picked by an area-weighted quantile (deterministic, no random numbers), and a
    triangle is
    picked more than once when it is large. Degenerate triangles are never picked.
    """
    n = _count(n)
    pts = np.asarray(positions, dtype=float)
    tri = np.asarray(triangles, dtype=int)
    if pts.ndim != 2 or pts.shape[1] != 3 or tri.ndim != 2 or tri.shape[1] != 3 or tri.size == 0:
        raise SeedsInvalid("a patch needs (n, 3) positions and (m, 3) triangles")
    if tri.min() < 0 or tri.max() >= pts.shape[0]:
        raise SeedsInvalid("the triangles name points the patch does not have")
    a, b, c = pts[tri[:, 0]], pts[tri[:, 1]], pts[tri[:, 2]]
    cross = np.cross(b - a, c - a)
    area2 = np.linalg.norm(cross, axis=1)
    alive = area2 > 0
    if not np.any(alive):
        raise SeedsInvalid("the patch has no triangle with an area")
    cum = np.cumsum(np.where(alive, area2, 0.0))
    quantiles = (np.arange(n) + 0.5) / n * cum[-1]
    picked = np.minimum(np.searchsorted(cum, quantiles), tri.shape[0] - 1)
    centroids = (a[picked] + b[picked] + c[picked]) / 3.0
    normals = cross[picked] / np.maximum(area2[picked], 1e-300)[:, None]
    return np.asarray(centroids - float(inward) * normals, dtype=float)
