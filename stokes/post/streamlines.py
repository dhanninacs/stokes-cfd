"""Streamlines through a surface's field: the direction a contour cannot show.

**Why this exists** (claude-win2's results item 4): a cut coloured by a scalar shows how FAST the
flow is and nothing about WHERE it goes. Streamlines answer the second question, and they are what
a CFD user reaches for next.

**Computed in the Python core from the field data already served** - the constraint claude-win2
set: no new OpenFOAM tool, no agent allow-list change. VTK does the integration (`vtkStreamTracer`,
already a dependency of `stokes.post`) and numpy carries the arrays.

**A surface, not a volume.** The field cache holds values on the patches, so a streamline follows
the flow ON a surface. A volume streamline would need the cell-centred field, a different cache.

**And that limits where it can show anything, measured twice on the audit's duct**:

* its three PATCHES are the inlet and outlet planes - perpendicular to the flow, so a line leaves at
  once - and the walls, where |U| is **exactly zero** by no-slip. No patch can carry a streamline.
* a SLICE works, but only briefly: the duct's flow has a vertical component (**`Uy` from -0.0073
  to +0.0073 m/s**), so a line on a horizontal slice walks off a strip 20 mm tall after about
  **10 mm** - and VTK says so, `ReasonForTermination = 1 = OUT_OF_DOMAIN`, which is the truth
  rather than a fault.

**So a streamline on a 2-D surface is honest only where the flow is parallel to it.**
For a duct's internal flow that means the streamlines belong in the VOLUME, which needs
**two** things this module does not have: the cell-centred field (a different cache) **and
a tracer that does not flatten** - `_trace` projects onto two dominant axes, which is right
for a slice and destroys a volume. **Measured**: a synthetic 25-cubed volume (15 625
samples) traced **0 lines** in 2.5 s, with 626 ms of that in the triangulation. **Stated
here so the next person reads "no lines" as the geometry talking, not the code.**
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

#: The most points a single streamline may hold.
#:
#: **A cap on the ANSWER, not on the work**: a streamline that doubles back in a recirculation can
#: run for thousands of steps, and a polyline of 100 000 points is one a browser cannot draw or a
#: user cannot read. Refused above this rather than truncated, because a truncated line claims an
#: end it did not reach.
MAX_POINTS = 5000

#: The most streamlines one call may ask for.
MAX_LINES = 200

#: The most integration steps one line may take: a guard against a runaway, not the length limit.
MAX_STEPS = 20000


class StreamlinesInvalid(Exception):
    """A streamline request that cannot be answered as asked."""

    code = "post.streamlines_invalid"


@dataclass(frozen=True)
class Streamline:
    """One streamline: its points in metres, and the speed at each."""

    points: tuple[tuple[float, float, float], ...]
    speeds: tuple[float, ...]


def streamlines(
    positions: np.ndarray,
    vectors: np.ndarray,
    seeds: np.ndarray,
    *,
    max_length: float = 1.0,
    step: float = 0.005,
    direction: str = "forward",
) -> list[Streamline]:
    """Integrate the flow `vectors` over the surface `positions`, starting at each of `seeds`.

    `vectors` is `(n, 3)` - the flow itself, not a speed: a streamline needs a direction. The
    caller reduces a velocity field to this, and `speeds` in the answer is its magnitude, so a
    plot can colour a line by how fast it goes.

    **`direction`** is `forward`, `backward` or `both`; "both" traces each way from the seed, which
    is what a user means by "from here in both directions".
    """
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise StreamlinesInvalid(f"positions must be (n, 3); got {positions.shape}")
    if vectors.shape != positions.shape:
        raise StreamlinesInvalid(f"vectors must be {positions.shape}; got {vectors.shape}")
    if seeds.ndim != 2 or seeds.shape[1] != 3:
        raise StreamlinesInvalid(f"seeds must be (m, 3); got {seeds.shape}")
    if seeds.shape[0] == 0:
        return []
    if seeds.shape[0] > MAX_LINES:
        raise StreamlinesInvalid(
            f"{seeds.shape[0]} seeds is more than the {MAX_LINES} this draws; use fewer"
        )
    if direction not in ("forward", "backward", "both"):
        raise StreamlinesInvalid(f"direction must be forward, backward or both; got {direction!r}")
    return _trace(positions, vectors, seeds, max_length=max_length, step=step, direction=direction)


def _tracer(
    grid: object, seeds_poly: object, *, max_length: float, step: float, direction: str
) -> Any:
    """The configured `vtkStreamTracer`.

    **Its own function** because the configure-call sequence is a dozen statements that say one
    thing - "trace from these seeds through this field" - and keeping them beside the array work
    made the parent read as a list of VTK calls.

    **`Any`, not a type**: `vtkStreamTracer` has no stubs (the pyproject override names VTK as
    untyped), so `object` would be a lie that forbids calling its own methods.
    """
    from vtkmodules.vtkFiltersFlowPaths import vtkStreamTracer

    tracer = vtkStreamTracer()
    tracer.SetInputData(grid)
    tracer.SetInputArrayToProcess(0, 0, 0, 0, "U")
    tracer.SetSourceData(seeds_poly)
    tracer.SetMaximumPropagation(float(max_length))
    # **`0` is LENGTH** (`1` CELL_LENGTH, `2` ARBITRARY). Measured: the first version passed 2,
    # which is ARBITRARY - every line stopped at **3 cm** whatever `step` or `max_length` was,
    # and VTK reported `ReasonForTermination = 1` for a trace that had plainly run. CELL_LENGTH
    # gave no lines at all on a flattened grid whose "cells" are Delaunay triangles rather than
    # duct cells, so LENGTH is the unit a caller can reason about: `step` is a distance in metres.
    tracer.SetIntegrationStepUnit(0)
    tracer.SetInitialIntegrationStep(float(step))
    # **Runge-Kutta 4-5 with its adaptive step, and the step cap raised** (ADR-0030). VTK stops a
    # line after `MaximumNumberOfSteps` whatever its length, and the default is 2,000: with a step
    # of 5 mm that is a line 7 cm long on a pipe half a metre long, which is what the Flanged
    # pipe's lines were. The length (`max_length`) is the limit the caller asked for; the step
    # count only guards a runaway.
    tracer.SetIntegratorTypeToRungeKutta45()
    tracer.SetMaximumNumberOfSteps(MAX_STEPS)
    # **Three real branches**, not a conditional expression used as a statement: the first version
    # was `... if direction == "forward" else None`, which sets nothing for "backward" and "both".
    if direction == "backward":
        tracer.SetIntegrationDirectionToBackward()
    elif direction == "both":
        tracer.SetIntegrationDirectionToBoth()
    else:
        tracer.SetIntegrationDirectionToForward()
    return tracer


def _trace(
    positions: np.ndarray,
    vectors: np.ndarray,
    seeds: np.ndarray,
    *,
    max_length: float,
    step: float,
    direction: str,
) -> list[Streamline]:
    """The VTK integration, with the arrays prepared the way it wants them."""
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonCore import vtkDoubleArray, vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkPolyData

    # **A SURFACE is flattened; a VOLUME is not.** The cache gives points and values, not cells, and
    # a tracer needs cells to interpolate within. For a slice that means throwing away the constant
    # axis and triangulating the other two (`vtkDelaunay2D`); for a volume it means tetrahedralising
    # all three (`vtkDelaunay3D`). Measured: flattening a 25-cubed volume produced **0 lines in
    # 2.5 s** - the samples collapsed into a plane and the trace left the domain at once.
    grid, axes = _grid_of(positions)
    if grid.GetNumberOfPoints() < 4:
        raise StreamlinesInvalid("this surface has too few points to trace")

    # **The vectors travel with the surface**: they are the same points, in the same order.
    vec = vtkDoubleArray()
    vec.SetNumberOfComponents(3)
    vec.SetName("U")
    for vx, vy, vz in vectors:
        vec.InsertNextTuple3(float(vx), float(vy), float(vz))
    grid.GetPointData().SetVectors(vec)

    # **The seeds follow the GRID's axes.** Two bugs live here, both measured: the original
    # flattened
    # the seeds when the grid was flattened too (a seed at z = 0.0105 sat off a surface at z = 0,
    # and the tracer answered "no lines" for a slice full of flow); and when the grid learned to
    # tetrahedralise a VOLUME the seeds still went through `_dominant`, so every volume line came
    # back as one stationary point at (0.5, 0.5, 0).
    # **One rule: whatever axes the grid used, the seeds use.**
    seeds_poly = vtkPolyData()
    seed_points = vtkPoints()
    if len(axes) == 3:
        for s in seeds:
            seed_points.InsertNextPoint(float(s[0]), float(s[1]), float(s[2]))
    else:
        for s in seeds:
            seed_points.InsertNextPoint(float(s[axes[0]]), float(s[axes[1]]), 0.0)
    seeds_poly.SetPoints(seed_points)

    tracer = _tracer(grid, seeds_poly, max_length=max_length, step=step, direction=direction)
    tracer.Update()
    out = tracer.GetOutput()
    if out is None or out.GetNumberOfPoints() == 0:
        return []

    # **VTK's converters are untyped** (the pyproject override says so): the annotation is
    # what says the shape, and the ignore says the library is the gap.
    coords: np.ndarray = vtk_to_numpy(out.GetPoints().GetData())  # type: ignore[no-untyped-call]
    lines = out.GetLines()
    # **VTK's converters are untyped** (the pyproject override says so): the annotation is
    # what says the shape, and the ignore says the library is the gap.
    offsets: np.ndarray = vtk_to_numpy(lines.GetOffsetsArray())  # type: ignore[no-untyped-call]
    # **VTK's converters are untyped** (the pyproject override says so): the annotation says the
    # shape, and the ignore names the gap.
    connectivity: np.ndarray = vtk_to_numpy(  # type: ignore[no-untyped-call]
        lines.GetConnectivityArray()
    )
    # **The speed at each line point is the tracer's own interpolation of `U`**: VTK carries the
    # input arrays onto the points it makes. The first version borrowed the nearest surface
    # point's speed, which is O(points x surface) per line and an approximation the tracer does
    # not need.
    carried = out.GetPointData().GetArray("U")
    speed_along: np.ndarray | None = (
        np.linalg.norm(vtk_to_numpy(carried), axis=1)  # type: ignore[no-untyped-call]
        if carried is not None
        else None
    )
    speed = np.linalg.norm(vectors, axis=1)
    found: list[Streamline] = []
    for i in range(lines.GetNumberOfCells()):
        start, end = int(offsets[i]), int(offsets[i + 1])
        ids = connectivity[start:end]
        if ids.size > MAX_POINTS:
            # **Resampled, not refused**: a line that takes its full steps is long, and the
            # picture does not need every step. The ends are kept, and the points between are
            # evenly spaced by index.
            ids = ids[np.unique(np.linspace(0, ids.size - 1, MAX_POINTS).round().astype(int))]
        # **The trace happened on a FLATTENED surface, so its third coordinate is 0.** Measured: a
        # slice at z = 0.0105 came back with z in [-5e-05, 0], which drew every line 10.5 mm below
        # the model - invisible beside the geometry. The plane's own constant is put back here, so a
        # line comes out where the surface actually is.
        # **The trace happened on the grid's own axes**, so a flattened surface gets its constant
        # axis back and a volume - which used all three - is left exactly where it is.
        pts = coords[ids].copy()
        for axis in _flattened_axes(axes):
            pts[:, axis] = float(np.median(positions[:, axis]))
        # **The line's own points carry no speed**, so each borrows from the nearest surface point:
        # the tracer moves along cells, not vertices. That is honest for a line drawn ON a surface,
        # where the distance is a fraction of a cell.
        if speed_along is not None:
            speeds = [float(v) for v in speed_along[ids]]
        else:
            speeds = []
            for p in pts:
                d2 = np.sum((positions - p) ** 2, axis=1)
                speeds.append(float(speed[int(np.argmin(d2))]))
        found.append(
            Streamline(
                points=tuple((float(p[0]), float(p[1]), float(p[2])) for p in pts),
                speeds=tuple(speeds),
            )
        )
    return found


#: Below this fraction of a surface's largest extent, an axis counts as flat.
#:
#: **A ratio, not a distance**: the same test has to hold for a 20 mm strip and a 2 m duct. A
#: slice carries a constant third coordinate, so its extent there is exactly 0; a volume's smallest
#: extent is a cell, which on any mesh worth tracing is far above this.
FLAT_AXIS_RATIO = 1e-6


def _flattened_axes(axes: tuple[int, ...]) -> tuple[int, ...]:
    """The axes a grid did NOT use - the ones a flattened surface has to be restored along."""
    return tuple(axis for axis in (0, 1, 2) if axis not in axes)


def _grid_of(positions: np.ndarray) -> tuple[Any, tuple[int, ...]]:
    """A grid a tracer can interpolate within, and the axes it was built on.

    **Three shapes, and each one is a measured decision**:

    * a **flat patch** is flattened and triangulated with `vtkDelaunay2D` - right for a slice;
    * a **uniform volume** - the case a decimated cell-centred field produces - is an `vtkImageData`
      with **no triangulation at all**;
    * any other **volume** is tetrahedralised with `vtkDelaunay3D`.

    **The image path is not a micro-optimisation**: measured on a 25-cubed volume, `vtkDelaunay3D`
    built **82 944 cells from 15 625 points in 4 259 ms**, while the same samples as an image build
    in
    **0 ms** with 13 824 cells and the trace takes **1 ms** instead of 4387. A structured grid is
    not
    something to tetrahedralise; it is something to describe.
    """
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkImageData, vtkPolyData
    from vtkmodules.vtkFiltersCore import vtkDelaunay2D, vtkDelaunay3D

    extents = positions.max(axis=0) - positions.min(axis=0)
    widest = float(extents.max()) if extents.size else 0.0
    flat = tuple(int(a) for a in range(3) if widest > 0 and extents[a] <= widest * FLAT_AXIS_RATIO)

    # **A uniform volume first**: if the samples ARE a structured grid, describing it is enough.
    image_shape = _uniform_shape(positions)
    if image_shape is not None:
        counts, origin, spacing = image_shape
        image = vtkImageData()
        image.SetDimensions(*counts)
        image.SetOrigin(*origin)
        image.SetSpacing(*spacing)
        return image, (0, 1, 2)

    src = vtkPoints()
    if len(flat) == 0:
        # **A volume: every axis is real**, so the points keep all three coordinates.
        for x, y, z in positions:
            src.InsertNextPoint(float(x), float(y), float(z))
        poly = vtkPolyData()
        poly.SetPoints(src)
        delaunay3 = vtkDelaunay3D()
        delaunay3.SetInputData(poly)
        delaunay3.Update()
        return delaunay3.GetOutput(), (0, 1, 2)

    # **A surface: drop the flat axis** (or the narrowest, where none is exactly flat - the same
    # `_dominant` rule the probe uses) and triangulate the other two.
    # **Exactly one flat axis is a slice**, and the other two are the plane. Where none is exactly
    # flat the surface is merely thin along one axis, and `_dominant` picks the two largest - the
    # rule the probe's triangulation already uses.
    axes = _flattened_axes(flat) if len(flat) == 1 else _dominant(positions)
    for point in positions:
        src.InsertNextPoint(float(point[axes[0]]), float(point[axes[1]]), 0.0)
    poly = vtkPolyData()
    poly.SetPoints(src)
    delaunay = vtkDelaunay2D()
    delaunay.SetInputData(poly)
    delaunay.Update()
    return delaunay.GetOutput(), axes


def _uniform_shape(
    positions: np.ndarray,
) -> tuple[tuple[int, int, int], tuple[float, float, float], tuple[float, float, float]] | None:
    """`(dimensions, origin, spacing)` if these points are a uniform 3-D grid, else None.

    **Deliberately strict**, because describing a grid that is not one gives a trace through cells
    that do not exist - worse than the slow path. The points must, on every axis, come in exactly
    `n^(1/3)` evenly spaced distinct values, and they must be ordered with **x fastest, then y, then
    z**
    (the order `numpy.meshgrid(..., indexing="ij").ravel()` produces and the one a rasterised sample
    uses). Anything else returns None and takes the tetrahedralisation.

    **The tolerance is relative**: a grid written as float32 and read back as float64 must still be
    recognised, and 1e-6 of the spacing is far above that error and far below a real irregularity.
    """
    count = positions.shape[0]
    if count < 8:
        return None
    side = round(count ** (1 / 3))
    # **Only a perfect cube of points can be a grid** whose fastest axis is x. A cuboid would work
    # with
    # the shape given, but then the caller has to tell us which of the three counts it is, and a
    # wrong
    # guess is the failure this function exists to avoid.
    if side**3 != count or side < 2:
        return None
    grid = positions.reshape(side, side, side, 3)
    origin = grid[0, 0, 0].astype(float)
    spacing = np.array(
        [grid[1, 0, 0, 0] - origin[0], grid[0, 1, 0, 1] - origin[1], grid[0, 0, 1, 2] - origin[2]]
    )
    if not np.all(spacing > 0):
        return None
    # Every axis advances by its own spacing and nothing else changes.
    tol = np.abs(spacing) * 1e-6
    for axis in range(3):
        along = np.arange(side, dtype=float) * spacing[axis]
        # **The coordinates are `origin + index * spacing` on each axis**, broadcast along the
        # others:
        # the first index (x) varies fastest, then y, then z.
        expected = origin[axis] + along.reshape(_axis_shape(side, axis))
        if np.abs(grid[..., axis] - expected).max() > tol[axis]:
            return None
    return (
        (side, side, side),
        (float(origin[0]), float(origin[1]), float(origin[2])),
        (
            float(spacing[0]),
            float(spacing[1]),
            float(spacing[2]),
        ),
    )


def _axis_shape(side: int, axis: int) -> tuple[int, int, int]:
    """`(side, side, side)` with `side` on `axis` and 1 on the others, for broadcasting."""
    # **`side` ON the axis and 1 off it**: the first version had the condition inverted, so a
    # coordinate
    # vector was reshaped to the whole cube and numpy refused it - caught the moment the detector
    # ran.
    return (side if axis == 0 else 1, side if axis == 1 else 1, side if axis == 2 else 1)


def _dominant(positions: np.ndarray) -> tuple[int, int]:
    """The two axes a patch is closest to lying in - the rule `triangulation.py` uses."""
    extents = positions.max(axis=0) - positions.min(axis=0)
    axes = sorted(np.argsort(extents)[-2:].tolist())
    return (int(axes[0]), int(axes[1]))
