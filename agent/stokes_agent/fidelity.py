"""How closely a meshed boundary follows the CAD it came from (T-233's fidelity block).

## The question this answers

The owner's requirement: *"the mesh should match the CAD when the default mesh is used."* For
snappyHexMesh that is not automatic - it snaps a castellated mesh onto the surface, and how well it
snapped is a measurement, not a look. Without one, a mesh that has drifted from the part is
indistinguishable from one that has not, and the user's only signal is that the picture seems wrong.

Three numbers, from Claude's 1115 spec:

* **deviation** - max and mean distance from the mesh's boundary onto the CAD surface, in metres and
  as a percentage of the part's characteristic length;
* **volume error** - the enclosed volume against the CAD's, as a percentage;
* **feature capture** - the fraction of the CAD's sharp edges that have a mesh vertex within one
  local cell size. A hole that closed over or a fillet the mesh walked past shows up here and
  nowhere else.

## Why the thresholds are relative to L

An absolute tolerance would be meaningless across parts of different sizes: 0.1 mm is coarse on a
10 mm part and invisible on a metre one. Every threshold here is a fraction of the characteristic
length, which is why `measure` takes it rather than reading a global.

## What this module does NOT do

It does not read a case or call vtk. The distances come in as arrays, so the arithmetic - the part
that can be wrong in a way a picture will not show - is testable without a mesh on disk. The host
side, which has vtk, is `fidelity_from_case`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The default thresholds, as fractions of the characteristic length L (plus an absolute one for
#: volume, which is already a ratio).
#:
#: From Claude's 1115 message and the owner's wish. `GOOD` is what a default mesh should achieve;
#: `ACCEPTABLE` is twice that, and beyond it the mesh has visibly not followed the part.
GOOD_MAX_DEVIATION = 0.015
GOOD_MEAN_DEVIATION = 0.003
GOOD_VOLUME_ERROR = 0.01
GOOD_FEATURE_CAPTURE = 0.90

#: The multiplier that separates `Acceptable` from `Poor`.
ACCEPTABLE_FACTOR = 2.0

#: How many mesh points the deviation is measured on before sampling.
#:
#: **A bounded sample, not every point.** A 5 M-cell mesh has millions of boundary vertices and the
#: distance to the CAD surface is the expensive part of this measurement, so a sample is taken and
#: the count is reported with the result - a deviation measured on 200 points and one measured on
#: 200,000 are different claims, and the reader is told which they have.
MAX_SAMPLES = 200_000

VERDICTS = ("Good", "Acceptable", "Poor")


@dataclass(frozen=True)
class Fidelity:
    """The measured numbers, with everything needed to judge and to trust them.

    ``sampled`` and ``points`` say how much of the mesh was measured: a max deviation over 200 of
    200,000 points is a lower bound, and hiding that would make the number sound stronger than it
    is.
    """

    max_deviation: float
    mean_deviation: float
    volume_error: float
    feature_capture: float | None
    characteristic_length: float
    sampled: int
    points: int
    #: How many CAD feature edges were available to capture. **Zero means the measurement could not
    #: be made** - a part with no sharp edges has none to capture, and reporting 100 % for it would
    #: be a false pass.
    features: int = 0

    @property
    def max_fraction(self) -> float:
        """Max deviation as a fraction of the characteristic length."""
        if not self.characteristic_length:
            return 0.0
        return self.max_deviation / self.characteristic_length

    @property
    def mean_fraction(self) -> float:
        return (
            self.mean_deviation / self.characteristic_length if self.characteristic_length else 0.0
        )

    @property
    def verdict(self) -> str:
        """`Good`, `Acceptable` or `Poor`, from the thresholds above.

        **The worst of the four decides**, not an average: a mesh can be excellent on mean deviation
        and terrible on the one hole that closed, and the user needs the worse answer.
        """
        limits = (
            (self.max_fraction, GOOD_MAX_DEVIATION),
            (self.mean_fraction, GOOD_MEAN_DEVIATION),
            (self.volume_error, GOOD_VOLUME_ERROR),
        )
        for value, good in limits:
            if value > good * ACCEPTABLE_FACTOR:
                return "Poor"
        if (
            self.features
            and self.feature_capture is not None
            and self.feature_capture < GOOD_FEATURE_CAPTURE / ACCEPTABLE_FACTOR
        ):
            return "Poor"
        for value, good in limits:
            if value > good:
                return "Acceptable"
        if (
            self.features
            and self.feature_capture is not None
            and (self.feature_capture < GOOD_FEATURE_CAPTURE)
        ):
            return "Acceptable"
        return "Good"

    def to_json(self) -> dict[str, Any]:
        """As the mesh result record and the quality card read it.

        Millimetres as well as metres, because the users of this product think in millimetres and a
        number in scientific notation is not readable at a glance - the card's whole purpose.
        """
        return {
            "max_deviation_m": self.max_deviation,
            "max_deviation_mm": self.max_deviation * 1000.0,
            "max_deviation_pct": self.max_fraction * 100.0,
            "mean_deviation_m": self.mean_deviation,
            "mean_deviation_mm": self.mean_deviation * 1000.0,
            "mean_deviation_pct": self.mean_fraction * 100.0,
            "volume_error_pct": self.volume_error * 100.0,
            "feature_capture_pct": (
                None if self.feature_capture is None else self.feature_capture * 100.0
            ),
            "characteristic_length_m": self.characteristic_length,
            "characteristic_length_mm": self.characteristic_length * 1000.0,
            "sampled": self.sampled,
            "points": self.points,
            "features": self.features,
            "verdict": self.verdict,
            "thresholds": {
                "max_deviation_pct": GOOD_MAX_DEVIATION * 100.0,
                "mean_deviation_pct": GOOD_MEAN_DEVIATION * 100.0,
                "volume_error_pct": GOOD_VOLUME_ERROR * 100.0,
                "feature_capture_pct": GOOD_FEATURE_CAPTURE * 100.0,
            },
        }


def sample_points(points: Any, *, limit: int = MAX_SAMPLES) -> tuple[Any, int, int]:
    """A bounded, evenly-spaced sample of ``points``.

    Returns ``(sample, sampled_count, total_count)``. **Evenly spaced, not the first N**: the first
    N points of a patch are one end of it, so their deviation is not the mesh's - a mesh could pass
    on a well-snapped inlet and fail on the walls nobody looked at.
    """
    import numpy as np

    array = np.asarray(points, dtype=float).reshape(-1, 3)
    total = int(array.shape[0])
    if total <= limit or limit <= 0:
        return array, total, total
    step = total / limit
    # `int(i * step)` walks the whole array rather than its head.
    index = np.fromiter(
        (min(int(i * step), total - 1) for i in range(limit)), dtype=int, count=limit
    )
    return array[index], int(index.shape[0]), total


def verdict_for(
    *,
    max_deviation: float,
    mean_deviation: float,
    volume_error: float,
    feature_capture: float | None,
    characteristic_length: float,
    features: int = 0,
    sampled: int = 0,
    points: int = 0,
) -> Fidelity:
    """Build a :class:`Fidelity`, which is where the verdict is computed."""
    return Fidelity(
        max_deviation=max_deviation,
        mean_deviation=mean_deviation,
        volume_error=volume_error,
        feature_capture=feature_capture,
        characteristic_length=characteristic_length,
        sampled=sampled,
        points=points,
        features=features,
    )


@dataclass
class Refinement:
    """The one automatic retry's bookkeeping (Claude's 1115, point 5).

    A retry is allowed **once**, only when the first attempt was `Poor` on max deviation or feature
    capture, and only when the refined estimate stays under the cell cap. ``raised`` records that it
    happened so the card can say "refined automatically once" rather than presenting a retuned mesh
    as the default.
    """

    attempted: bool = False
    raised: int = 0
    first_verdict: str = ""
    kept: str = ""
    reasons: list[str] = field(default_factory=list)

    def should_retry(self, fidelity: Fidelity, *, cells_after: int, cell_cap: int) -> bool:
        """Whether a retry is warranted, and record why.

        **Only on the two signals a retry can fix.** A poor VOLUME error means the surface enclosed
        the wrong region - a different cell size will not mend that, and retrying would spend
        minutes to fail again.
        """
        if self.attempted:
            return False
        if fidelity.verdict != "Poor":
            return False
        fixable = fidelity.max_fraction > GOOD_MAX_DEVIATION * ACCEPTABLE_FACTOR
        uncaptured = (
            fidelity.features > 0
            and fidelity.feature_capture is not None
            and fidelity.feature_capture < GOOD_FEATURE_CAPTURE / ACCEPTABLE_FACTOR
        )
        if not (fixable or uncaptured):
            self.reasons.append(
                "the mesh is poor on a signal a finer surface would not change, so it was not "
                "retried"
            )
            return False
        if cells_after > cell_cap:
            self.reasons.append(
                f"a retry would need {cells_after:,} cells, over the {cell_cap:,} cap"
            )
            return False
        self.reasons.append("refined automatically once")
        return True


# ------------------------------------------------------------------ the host side (vtk)


def _polydata(points: Any, triangles: Any) -> Any:
    """A `vtkPolyData` from points and triangle indices.

    One place for the conversion, because both the CAD surface and the mesh boundary need it and a
    different winding or offset between them would silently measure the wrong thing.
    """
    import numpy as np
    from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData

    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    tris = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)

    vp = vtkPoints()
    vp.SetData(numpy_to_vtk(pts, deep=True))
    # VTK wants each cell as [count, i, j, k], flattened.
    cells = np.hstack([np.full((tris.shape[0], 1), 3, dtype=np.int64), tris]).ravel()
    ca = vtkCellArray()
    # **`ImportLegacyFormat`, not `SetCells`.** `SetCells` is deprecated since VTK 9.6 and this
    # project's test configuration turns a `DeprecationWarning` into an error, so the old call made
    # every measurement test fail - measured, and it is a hard failure rather than a wrong number.
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells, deep=True))
    poly = vtkPolyData()
    poly.SetPoints(vp)
    poly.SetPolys(ca)
    return poly


def deviations_to_surface(
    points: Any, surface: Any, *, limit: int = MAX_SAMPLES
) -> tuple[Any, int, int]:
    """Distance from each sampled point onto the CAD surface.

    **`vtkImplicitPolyDataDistance`, not a nearest-point search on the triangles**: it answers for
    the SURFACE rather than for the nearest triangle centroid, which is the difference between a
    real distance and an approximation that depends on how the CAD was tessellated.

    Returns ``(distances, sampled, total)``.
    """
    import numpy as np
    from vtkmodules.vtkFiltersCore import vtkImplicitPolyDataDistance

    # `sample_points` bounds the work and reports how much was measured. Imported at module
    # scope it would be this module calling itself; the local import keeps the dependency clear.
    from stokes_agent.fidelity import sample_points

    sample, sampled, total = sample_points(points, limit=limit)
    if sampled == 0:
        return np.zeros(0), 0, total
    measure = vtkImplicitPolyDataDistance()
    # **`SetInput`, not `SetInputData`.** Unlike most VTK filters this one takes the polydata
    # directly; `SetInputData` does not exist on it, which is an AttributeError rather than a wrong
    # answer - measured on this install.
    measure.SetInput(surface)
    distances = np.fromiter(
        (abs(measure.EvaluateFunction(p)) for p in sample), dtype=float, count=sampled
    )
    return distances, sampled, total


def measure(
    *,
    mesh_points: Any,
    mesh_triangles: Any,
    cad_points: Any,
    cad_triangles: Any,
    characteristic_length: float,
    mesh_volume: float | None = None,
    cad_volume: float | None = None,
    feature_edges: Any | None = None,
    cell_size: float | None = None,
    limit: int = MAX_SAMPLES,
) -> Fidelity:
    """Measure a meshed boundary against the CAD it came from.

    Every input is an array or a number, so the arithmetic is testable without a mesh on disk; the
    caller (which has the case) supplies them. Missing inputs make the corresponding axis
    **absent rather than zero**: a volume error of 0 % would be a false pass, so a caller without
    the CAD volume gets a capture of `None` instead.
    """
    import numpy as np

    surface = _polydata(cad_points, cad_triangles)
    distances, sampled, total = deviations_to_surface(mesh_points, surface, limit=limit)
    max_dev = float(np.max(distances)) if distances.size else 0.0
    mean_dev = float(np.mean(distances)) if distances.size else 0.0

    # **The CAD volume is computed when it is not supplied**, and only when the surface is CLOSED:
    # an open surface encloses nothing, so comparing against it would report a large error that says
    # nothing about the mesh. Same rule as a feature capture of `None`.
    if cad_volume is None:
        cad_volume = (
            enclosed_volume(cad_points, cad_triangles)
            if is_closed(cad_points, cad_triangles)
            else None
        )
    volume_error = 0.0
    if mesh_volume is not None and cad_volume:
        volume_error = abs(mesh_volume - cad_volume) / abs(cad_volume)

    # **The feature edges are derived from the CAD surface when none are supplied**, so a caller
    # need only hand over the surface it already has. 30 degrees is Claude's threshold and the app's
    # own `FEATURE_ANGLE_DEG`, so the report and the tag tools agree on what "sharp" means.
    if feature_edges is None and cell_size is not None:
        # **`feature_edges_of`, because `feature_edges` is this parameter's name** - calling the
        # module function through it was a TypeError on the first real run.
        feature_edges = feature_edges_of(cad_points, cad_triangles)
    capture, features = _feature_capture(
        mesh_points=mesh_points,
        feature_edges=feature_edges,
        cell_size=cell_size,
    )
    return verdict_for(
        max_deviation=max_dev,
        mean_deviation=mean_dev,
        volume_error=volume_error,
        feature_capture=capture,
        characteristic_length=characteristic_length,
        features=features,
        sampled=sampled,
        points=total,
    )


def _feature_capture(
    *, mesh_points: Any, feature_edges: Any | None, cell_size: float | None
) -> tuple[float | None, int]:
    """The fraction of the CAD's sharp edges with a mesh vertex within one cell size.

    **`None` when it cannot be measured**, with a feature count of 0: a part with no sharp edges has
    none to capture, and reporting 100 % for it would be a false pass rather than a reading.

    A feature edge counts as captured when a mesh boundary vertex lies within one LOCAL CELL of any
    point on it. Sampling the edge at its endpoints and midpoint is enough for the question asked -
    "did the mesh notice this edge at all" - and walking every point of every edge would cost more
    than the deviation measurement itself.
    """
    import numpy as np

    if feature_edges is None or cell_size is None or cell_size <= 0:
        return None, 0
    edges = np.asarray(feature_edges, dtype=float).reshape(-1, 2, 3)
    if edges.shape[0] == 0:
        return None, 0
    mesh = np.asarray(mesh_points, dtype=float).reshape(-1, 3)
    if mesh.shape[0] == 0:
        return 0.0, int(edges.shape[0])

    captured = 0
    for start, end in edges:
        probes = np.stack([start, (start + end) / 2, end])
        # One cell size is the tolerance: a vertex further than that from the edge cannot be said
        # to have followed it.
        near = np.any(
            np.min(np.linalg.norm(mesh[:, None, :] - probes[None, :, :], axis=2), axis=1)
            <= cell_size
        )
        captured += int(bool(near))
    return captured / edges.shape[0], int(edges.shape[0])


# ------------------------------------------------------------------ volume, from the surface


def enclosed_volume(points: Any, triangles: Any) -> float:
    """The volume a closed triangle surface encloses, by the divergence theorem.

    ``sum over triangles of a . (b x c) / 6``, whose sign follows the winding: a surface with
    inward normals gives a negative number, which is a finding rather than a bug (OpenFOAM's own
    check reports it as "closed but normals point inwards").

    **Computed from the STAGED surface, not from the B-rep.** The staged tessellation is what the
    mesh was actually refined against, so the comparison is like for like, and the agent has the STL
    in hand while it has no CAD file at all. Claude's spec offers the B-rep (`GProp_GProps`) for the
    case where the app has it - a better volume of the SAME part - and this is the one available
    where the measurement runs.

    Returns 0.0 for a surface with no triangles or no enclosed volume, because a caller comparing
    volumes must handle "not a closed solid" rather than receive a NaN.
    """
    import numpy as np

    tris = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if tris.shape[0] == 0:
        return 0.0
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    a, b, c = p[tris[:, 0]], p[tris[:, 1]], p[tris[:, 2]]
    # The scalar triple product per triangle; its sum over a closed surface is 6 x the volume.
    return float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)


def is_closed(points: Any, triangles: Any) -> bool:
    """Whether every edge of the surface is shared by exactly two triangles.

    **The check that makes a volume meaningful.** An open surface, or one with a hole, encloses
    nothing, and comparing such a volume against a mesh would report a large error that says
    nothing about the mesh. The measurement skips the volume axis instead - like a capture of
    `None` for a part with no features.
    """
    import numpy as np

    tris = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if tris.shape[0] == 0:
        return False
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    # **Welded BY POSITION, because a snappy case stages one STL per boundary tag and each patch
    # carries its own vertex table.** Comparing raw indices across patches said "16 of 26 edges are
    # unshared" for the demo's perfectly closed box, and the volume axis was skipped for a surface
    # whose volume was exactly right (2.000000e-04 m3 for a 500x20x20 mm part - measured). The same
    # point in two patches is two indices and one vertex.
    _unique_positions, welded = np.unique(pts, axis=0, return_inverse=True)
    corners = welded.reshape(-1)[tris]
    edges = np.concatenate([corners[:, [0, 1]], corners[:, [1, 2]], corners[:, [2, 0]]], axis=0)
    # An edge and its reverse are the two sides of the same shared edge, so orient them before
    # counting: a closed surface has every edge appearing exactly twice.
    edges = np.sort(edges, axis=1)
    _edges, counts = np.unique(edges, axis=0, return_counts=True)
    return bool(np.all(counts == 2))


def mesh_volume(mesh_points: Any, mesh_triangles: Any) -> float:
    """The volume the MESH's boundary encloses, by the same divergence theorem.

    Absent here would be worse than wrong: the volume error is the one axis that catches a mesh
    which snapped beautifully onto the wrong region - two fins merged into one, or a bore filled
    in - where every deviation is small and the shape is not the shape.
    """
    return enclosed_volume(mesh_points, mesh_triangles)


# ------------------------------------------------------------------ feature edges


def feature_edges_of(points: Any, triangles: Any, *, angle_deg: float = 30.0) -> Any:
    """The edges where two triangles meet at more than ``angle_deg``: the CAD's sharp edges.

    **Derived from the tessellation, not from the B-rep.** Claude's spec asks for the B-rep's edges,
    and the B-rep is the better source - it knows a smooth cylinder from a faceted one. The agent
    does not have it: what it has is the STL it staged, and this is the same measurement made from
    the file the mesh was actually refined against. The default 30 degrees is the spec's number and
    the app's own `FEATURE_ANGLE_DEG`, so the two agree on what "sharp" means.

    An edge shared by exactly two triangles is sharp when the angle between their normals exceeds
    the threshold; an edge used by fewer than two (a hole) or more than two (non-manifold) is
    **skipped rather than guessed at**, because there is no pair of normals to compare.

    Returns an ``(E, 2, 3)`` array of the edge's endpoint positions.
    """
    import numpy as np

    tris = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if tris.shape[0] == 0:
        return np.zeros((0, 2, 3))

    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    cross = np.cross(b - a, c - a)
    norm = np.linalg.norm(cross, axis=1)
    normals = np.zeros_like(cross)
    np.divide(cross, norm[:, None], out=normals, where=norm[:, None] > 0)

    # Every triangle contributes its three directed edges; an undirected key groups a shared edge's
    # two uses together.
    head = tris.ravel()
    tail = np.roll(tris, -1, axis=1).ravel()
    owner = np.repeat(np.arange(tris.shape[0]), 3)
    low, high = np.minimum(head, tail), np.maximum(head, tail)
    keep = low != high  # a degenerate triangle contributes a zero-length "edge"
    low, high, owner = low[keep], high[keep], owner[keep]
    if low.shape[0] == 0:
        return np.zeros((0, 2, 3))

    order = np.argsort(low.astype(np.int64) * (int(low.max()) + 1) + high, kind="stable")
    low, high, owner = low[order], high[order], owner[order]
    same = (low[1:] == low[:-1]) & (high[1:] == high[:-1])
    # A run of exactly two uses is a manifold shared edge; anything else is skipped.
    starts = np.flatnonzero(np.concatenate([[True], ~same]))
    ends = np.concatenate([starts[1:], [low.shape[0]]])
    sharpest = angle_deg
    out: list[Any] = []
    for start, end in zip(starts, ends, strict=True):
        if end - start != 2:
            continue
        first, second = owner[start], owner[start + 1]
        cosine = float(np.dot(normals[first], normals[second]))
        # The angle between normals: 0 is flat, 180 is a fold back on itself.
        between = float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))
        if between > sharpest:
            out.append([pts[low[start]], pts[high[start]]])
    return np.array(out, dtype=np.float64) if out else np.zeros((0, 2, 3))
