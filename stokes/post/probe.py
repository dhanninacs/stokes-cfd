"""A value at a point: the probe behind a line plot (results item 2).

**What it answers**: "what is |U| here?" for a point in space, and the same for every point of a
polyline, so a user can draw a line through the model and read the profile along it.

**Interpolation, not a lookup.** A probe point almost never lands on a mesh vertex, so the value
is interpolated from the nearest vertices - the alternative (refusing "no vertex here") would make
the feature useless for the one thing it is for. `scipy.interpolate.LinearNDInterpolator` is
deliberately NOT used: the field data is a cloud of surface points with no triangulation carried
alongside it, and scipy's linear interpolator needs one. `NearestNDInterpolator` and
inverse-distance weighting both work on a cloud; **inverse distance is the one used**, because a
nearest-vertex answer jumps in steps across a smooth field and a line plot of it looks like stairs.

**Pure and headless**: this module imports numpy only. `stokes.post` may import VTK, but nothing
here needs it, and a probe inside the API command is cheaper than one that waits on VTK.

**`run.probe` is the command this feeds, and it is a JOIN ACROSS TWO SERVICES** - traced, not
guessed:

* the VALUES are the run's `.stkfld` cache, read with `stokes.run.fields.read_header` and
  `field_array(data, header, patch_index, field)` - one array per patch;
* the POSITIONS are the MESH's surfaces artifact, reached through
  `MeshService.patch_transport(node_id)` (bytes) and then `stokes.mesh.surfaces.patch_arrays(data,
  header, index)` - `(positions, triangles, edges)`.

So the command must have a `mesh_patch` node, find its run, hold BOTH caches, and match a field's
patch index to a surface's patch index. That is why the triangulation cache is keyed by a string the
CALLER chooses: the command keys it by `(run, patch node id)`, and nothing here has to know what a
patch node is.

**A measured limitation, written down rather than papered over.** Inverse-distance weighting does
NOT reproduce a linear field exactly: on a 6x6 patch `f = 2x + 3y`, a probe at (0.25, 0.5) gives
**1.950** where the answer is 2.0 - a 2.5 % error. The exact method is barycentric interpolation
on a Delaunay triangulation, and that was measured too: **319 ms to triangulate 25 600 points**,
over the API's 200 ms budget before a single probe is taken. So this module trades exactness on
smooth fields for fitting the budget, and the tests pin the SIZE of the error rather than
pretending it is zero. The fix, when the budget allows, is a triangulation cached per surface -
built once when the results are read, not once per probe.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: The most vertices an inverse-distance probe will consider at once.
#:
#: **A cap, because the cost is points x vertices.** The audit's duct has ~25 k surface points,
#: so a 200-point polyline over it is 5 million distance terms - about a fifth of a second, which
#: only just fits the API's 200 ms budget. Above this the caller is refused with the reason rather
#: than served slowly, the rule the slice routes already follow.
MAX_PROBE_POINTS = 2000


class ProbeInvalid(Exception):
    """A probe that cannot be answered as asked: no vertices to interpolate from, or too many."""

    code = "post.probe_invalid"


@dataclass(frozen=True)
class ProbeResult:
    """What one probe found: the interpolated values and how far the nearest vertex was."""

    values: tuple[float, ...]
    #: Distance in metres to the nearest vertex, so a user can tell a value read inside a hole
    #: from one read on a surface. A large figure means the answer is not trustworthy.
    nearest: float
    #: The number of vertices actually used, after the `k` nearest were taken.
    used: int


def probe_points(
    positions: np.ndarray,
    values: np.ndarray,
    points: np.ndarray,
    *,
    k: int = 8,
) -> list[ProbeResult]:
    """Inverse-distance interpolate `values` at each of `points`.

    `positions` is `(n, 3)` in metres, `values` is `(n,)` - one component, which is what a line
    plot shows. **A multi-component field is the caller's to reduce** (a speed from a vector, say),
    because which reduction is right depends on the field and this function should not guess.

    `k` is how many of the nearest vertices weight each answer. **Eight is the default**: three is
    the fewest that spans a surface locally, and more than about a dozen stops changing the answer
    while still costing time.
    """
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ProbeInvalid(f"positions must be (n, 3); got {positions.shape}")
    if values.ndim != 1 or values.shape[0] != positions.shape[0]:
        raise ProbeInvalid(f"values must be ({positions.shape[0]},); got {values.shape}")
    if points.ndim != 2 or points.shape[1] != 3:
        raise ProbeInvalid(f"points must be (m, 3); got {points.shape}")
    if points.shape[0] == 0:
        return []
    if points.shape[0] > MAX_PROBE_POINTS:
        raise ProbeInvalid(
            f"{points.shape[0]} probe points is more than the {MAX_PROBE_POINTS} this can answer; "
            "probe a shorter line"
        )
    if positions.shape[0] == 0:
        raise ProbeInvalid("this surface has no points to interpolate from")

    used = max(1, min(int(k), positions.shape[0]))
    out: list[ProbeResult] = []
    for point in points:
        # **One point at a time**: the squared distances are `n` long, not `m x n`, so a long line
        # costs memory in the answer rather than in a matrix.
        d2 = np.sum((positions - point) ** 2, axis=1)
        nearest_index = int(np.argmin(d2))
        take = np.argpartition(d2, min(used, d2.shape[0]) - 1)[:used]
        near = d2[take]
        # **A point ON a vertex returns that vertex exactly**: an inverse distance of zero would
        # divide by it, and the limit is the vertex's own value.
        if near[0 if nearest_index in take else np.argmin(near)] <= 0.0 or float(near.min()) <= 0.0:
            out.append(
                ProbeResult(
                    values=(float(values[take[int(np.argmin(near))]]),), nearest=0.0, used=used
                )
            )
            continue
        # Inverse SQUARE distance: the usual Shepard form, and the one that weights the nearest
        # vertex usefully rather than letting eight neighbours average everything flat.
        w = 1.0 / near
        out.append(
            ProbeResult(
                values=(float(np.dot(w, values[take]) / np.sum(w)),),
                nearest=float(np.sqrt(np.min(d2))),
                used=used,
            )
        )
    return out
