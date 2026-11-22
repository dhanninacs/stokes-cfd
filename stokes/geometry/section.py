"""The solid a section plane cuts through a modeller body, filled exactly from the body's field.

The viewer caps a cut body with a stencil fill (`web/src/viewport/sectionCaps.ts`), which needs the
body's display mesh to be closed. A body built with the implicit modeller is meshed by dual
contouring, and the mesher drops the folded triangles that leaves (`native/implicit-mesh`
`unfold.rs`): on a gyroid plate that opens the surface, the stencil count goes wrong across the
lattice and the cut shows the hollow inside instead of the plate's section. The field itself is
exact, so the cut is computed from it: the field is sampled on a grid in the plane over the body's
box, each grid square is split in two triangles, and each triangle is clipped to where the field is
negative (inside) with the boundary placed by linear interpolation. Flat, in the plane, independent
of how the body was meshed.

Pure numpy and the implicit evaluator; positions come back in metres in the world frame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

#: Grid points across the larger side of the body's box in the plane.
SAMPLES = 360
#: The most grid points one cut evaluates (the gyroid graph costs about a microsecond a point).
MAX_POINTS = 250_000
MM_PER_M = 1000.0
AXES = {"x": 0, "y": 1, "z": 2}


def fill(
    values: npt.NDArray[np.float64], u: npt.NDArray[np.float64], v: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int64]]:
    """Where `values` (shape (len(u), len(v))) is negative - inside - as triangles.

    Answers `(points, triangles)`: points `(n, 2)` in (u, v) and triangles `(t, 3)` indices into
    them. The grid points come first (indices `i * len(v) + j`); the points on edges where the
    field crosses zero follow.
    """
    import numpy as np

    nu, nv = values.shape
    grid = np.stack(np.meshgrid(u, v, indexing="ij"), axis=-1).reshape(-1, 2)
    flat = values.reshape(-1)
    i, j = np.meshgrid(np.arange(nu - 1), np.arange(nv - 1), indexing="ij")
    a = (i * nv + j).reshape(-1)
    b = a + nv
    c = b + 1
    d = a + 1
    tris = np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])
    inside = flat[tris] < 0.0
    count = inside.sum(axis=1)
    keep = [tris[count == 3]]
    # Each clipped triangle names the grid edges its boundary crosses; the crossing points are made
    # once per edge, so neighbouring triangles share them and the fill is one connected surface.
    pieces: list[
        tuple[int, npt.NDArray[np.int64], npt.NDArray[np.int64], npt.NDArray[np.int64]]
    ] = []
    edges: list[npt.NDArray[np.int64]] = []
    for want in (1, 2):
        rows = tris[count == want]
        if rows.size == 0:
            continue
        flags = inside[count == want]
        # Rotate each triangle so its odd vertex (the one inside for 1, outside for 2) comes first,
        # which keeps the winding.
        odd = np.argmax(flags if want == 1 else ~flags, axis=1)
        order = (odd[:, None] + np.arange(3)[None, :]) % 3
        r = np.take_along_axis(rows, order, axis=1)
        pieces.append((want, r[:, 0], r[:, 1], r[:, 2]))
        edges += [
            np.sort(np.stack([r[:, 0], r[:, 1]], 1), 1),
            np.sort(np.stack([r[:, 0], r[:, 2]], 1), 1),
        ]
    if not pieces:
        return grid, (keep[0] if keep else np.zeros((0, 3))).astype(np.int64)
    unique, where = np.unique(np.concatenate(edges), axis=0, return_inverse=True)
    where = where.reshape(-1)
    fp, fq = flat[unique[:, 0]], flat[unique[:, 1]]
    t = np.clip(fp / np.where(fp == fq, 1.0, fp - fq), 0.0, 1.0)[:, None]
    crossings = grid[unique[:, 0]] + t * (grid[unique[:, 1]] - grid[unique[:, 0]])
    base = grid.shape[0]
    at = 0
    for want, p0, p1, p2 in pieces:
        n = len(p0)
        i01 = base + where[at : at + n]
        i02 = base + where[at + n : at + 2 * n]
        at += 2 * n
        if want == 1:
            keep.append(np.stack([p0, i01, i02], 1))
        else:
            keep.append(np.stack([i01, p1, p2], 1))
            keep.append(np.stack([i01, p2, i02], 1))
    points = np.concatenate([grid, crossings])
    return points, np.concatenate(keep).astype(np.int64)


def cut_body(body: Any, axis: str, offset: float) -> tuple[npt.NDArray[Any], npt.NDArray[Any]]:
    """The section of an implicit `Body` (millimetres) at `offset` metres along `axis`.

    Answers `(positions, triangles)`: float32 `(n, 3)` metres in the world frame and uint32
    `(t, 3)`, only the points the triangles use. Empty arrays where the plane misses the body.
    """
    import numpy as np

    from stokes.implicit.evaluate import evaluate

    normal = AXES[axis]
    low, high = body.bounded()
    at = offset * MM_PER_M
    empty = (np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.uint32))
    if not low[normal] <= at <= high[normal]:
        return empty
    across = [k for k in range(3) if k != normal]
    spans = [high[k] - low[k] for k in across]
    step = max(max(spans) / SAMPLES, (spans[0] * spans[1] / MAX_POINTS) ** 0.5, 1e-9)
    pad = step
    u = np.arange(low[across[0]] - pad, high[across[0]] + pad + step / 2, step)
    v = np.arange(low[across[1]] - pad, high[across[1]] + pad + step / 2, step)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    points = np.zeros((uu.size, 3))
    points[:, across[0]] = uu.reshape(-1)
    points[:, across[1]] = vv.reshape(-1)
    points[:, normal] = at
    values = evaluate(body.field(), points).reshape(uu.shape)
    plane, triangles = fill(values, u, v)
    if triangles.size == 0:
        return empty
    used, index = np.unique(triangles.reshape(-1), return_inverse=True)
    xyz = np.zeros((used.size, 3))
    xyz[:, across[0]] = plane[used, 0]
    xyz[:, across[1]] = plane[used, 1]
    xyz[:, normal] = at
    return (
        (xyz / MM_PER_M).astype(np.float32),
        index.reshape(-1, 3).astype(np.uint32),
    )
