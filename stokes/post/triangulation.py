"""A surface's triangulation, built ONCE and cached, for exact probe values.

**Why this module exists** (claude-win2's 2026-10-20 decision): *"a displayed probe value with
units must be right, not within 2.5 %"* - a user compares it with a hand calculation.
Inverse-distance weighting cannot reproduce a linear field (`stokes/post/probe.py` measured a
2.5 % error on `f = 2x + 3y`), and barycentric interpolation on a Delaunay triangulation can,
exactly.

**The cost is why it is cached, not why it is skipped**: triangulating 25 600 points took
**319 ms**, outside the API's 200 ms budget. So the triangulation is built **once per surface**,
lazily, in the same background path the slice cache uses - and a probe taken before it is ready
says **"preparing"** rather than returning a less accurate number silently.

**The surface is flattened onto its dominant plane** before triangulating. A surface patch is not
planar, but its points are close to one plane, and `Delaunay` in 3D would build a
tetrahedralisation instead of a surface - which is why the axis with the smallest extent is
dropped.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np


class TriangulationNotReady(Exception):
    """The surface has no triangulation yet: say "preparing" and let the user retry."""

    code = "post.triangulation_preparing"


class OutsideSurface(Exception):
    """The probe point lies outside the surface - **said plainly rather than extrapolated**."""

    code = "post.probe_outside"


@dataclass(frozen=True)
class Triangulation:
    """A surface's points, flattened onto two axes, and their Delaunay triangulation."""

    #: `(n, 2)` - the points in the plane they were triangulated in.
    flat: np.ndarray
    #: The two axes kept, so a probe point is flattened the same way: e.g. `(0, 2)` drops y.
    axes: tuple[int, int]
    simplices: np.ndarray
    #: `(t, 3)` corner indices of each triangle, the same vtkDelaunay2D produced.
    triangles: np.ndarray = field(repr=False)

    def _simplex_of(self, point: np.ndarray) -> int:
        """The triangle containing `point`, or `OutsideSurface` - **never the nearest instead**.

        **A half-plane test on all three edges**, which is exact for a point in a triangle and
        cannot pick the wrong one. A "nearest corner" answer was the tempting shortcut and would
        report a value for a point outside the patch, which is the one thing forbidden.
        """
        p = np.array([point[self.axes[0]], point[self.axes[1]]], dtype=float)
        corners = self.flat[self.triangles]  # (t, 3, 2)
        # Cross products of each edge with the point: all three the same sign means inside.
        d1 = corners[:, 1] - corners[:, 0]
        d2 = corners[:, 2] - corners[:, 1]
        d3 = corners[:, 0] - corners[:, 2]
        v1 = p - corners[:, 0]
        v2 = p - corners[:, 1]
        v3 = p - corners[:, 2]
        # **`>= -eps` rather than `>= 0`**: a point exactly on an edge belongs to both triangles,
        # and a floating-point off-by-one would otherwise say "outside" for a boundary point.
        eps = 1e-12
        s1 = d1[:, 0] * v1[:, 1] - d1[:, 1] * v1[:, 0]
        s2 = d2[:, 0] * v2[:, 1] - d2[:, 1] * v2[:, 0]
        s3 = d3[:, 0] * v3[:, 1] - d3[:, 1] * v3[:, 0]
        inside = ((s1 >= -eps) & (s2 >= -eps) & (s3 >= -eps)) | (
            (s1 <= eps) & (s2 <= eps) & (s3 <= eps)
        )
        found = np.flatnonzero(inside)
        if found.size == 0:
            raise OutsideSurface("this point is outside the surface")
        return int(found[0])

    def interpolate(self, point: np.ndarray, values: np.ndarray) -> float:
        """The value at `point`, by barycentric interpolation on the containing triangle.

        **Exact for a linear field**, which is the property the 2.5 % inverse-distance answer
        failed. The weights solve `[1,1,1; x0,x1,x2; y0,y1,y2] @ w = [1, px, py]` - the matrix is
        that triangle's corner coordinates, which is what makes the result exact rather than a
        weighted average of neighbours.
        """
        corner = self.simplices[self._simplex_of(point)]
        p = np.array([point[self.axes[0]], point[self.axes[1]]], dtype=float)
        matrix = np.vstack([np.ones(3, dtype=float), self.flat[corner].T])
        weights = np.linalg.solve(matrix, np.array([1.0, p[0], p[1]]))
        return float(weights @ values[corner])


def triangulate(positions: np.ndarray) -> Triangulation:
    """Build a surface's triangulation. **Expensive** - build it once and cache it."""
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ValueError(f"positions must be (n, 3); got {positions.shape}")
    if positions.shape[0] < 4:
        raise OutsideSurface("this surface has too few points to triangulate")
    # **Flatten onto the plane it is closest to**: a patch thin in z is triangulated in (x, y).
    # **`argsort(...)[-2:]`, not `[:2]`** - the axes to KEEP are the two LARGEST, and the first
    # version took the two smallest. Measured: a flat patch has extents `[1, 1, 0]`, ascending
    # argsort gives `[2, 0]`, so it kept z (extent 0), dropped y, and Qhull answered "initial
    # simplex is flat". The `sorted` keeps the pair stable whatever order the sort returns.
    extents = positions.max(axis=0) - positions.min(axis=0)
    axes = tuple(sorted(np.argsort(extents)[-2:].tolist()))
    flat = positions[:, list(axes)]
    # **Colinear is refused before triangulating, not detected from the output**: vtkDelaunay2D
    # happily returns cells for points on a line (even the same cell twice), so "no triangles" is
    # not a reliable signal. The rank of the centred points is: rank 1 means every point is on one
    # line and there is no surface to interpolate on.
    if np.linalg.matrix_rank(flat - flat.mean(axis=0)) < 2:
        raise OutsideSurface("this surface cannot be triangulated: its points are in a line")
    triangles = _delaunay(flat)
    if triangles.shape[0] == 0:
        raise OutsideSurface("this surface cannot be triangulated: its points are in a line")
    axes_made = (int(axes[0]), int(axes[1]))
    return Triangulation(flat=flat, axes=axes_made, simplices=triangles, triangles=triangles)


def triangulation_of(positions: np.ndarray, triangles: np.ndarray) -> Triangulation:
    """A triangulation from triangles the surface already has (a slice's own cells).

    No Delaunay: the corner indices are used as given, so a hole or a concave edge of the surface
    stays outside it. The two axes kept are the two widest, as in `triangulate`.
    """
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ValueError(f"positions must be (n, 3); got {positions.shape}")
    if triangles.ndim != 2 or triangles.shape[1] != 3 or triangles.shape[0] == 0:
        raise OutsideSurface("this surface has no triangles")
    extents = positions.max(axis=0) - positions.min(axis=0)
    axes = tuple(sorted(np.argsort(extents)[-2:].tolist()))
    tris = triangles.astype(np.intp, copy=False)
    return Triangulation(
        flat=np.asarray(positions[:, list(axes)], dtype=float),
        axes=(int(axes[0]), int(axes[1])),
        simplices=tris,
        triangles=tris,
    )


def _delaunay(flat: np.ndarray) -> np.ndarray:
    """The triangles of `flat`, as `(t, 3)` corner indices, from VTK.

    **VTK, not scipy.** `tests/test_explore_imports.py` restricts `scipy` to `stokes.explore`, and
    that rule is right: scipy is a numerical-analysis dependency, and importing it from a plotting
    package would drag its import cost into every post-processing path. VTK is already a declared,
    permitted dependency of `stokes.post`, and `vtkDelaunay2D` is the same algorithm.
    """
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkPolyData
    from vtkmodules.vtkFiltersCore import vtkDelaunay2D

    points = vtkPoints()
    for x, y in flat:
        points.InsertNextPoint(float(x), float(y), 0.0)
    poly = vtkPolyData()
    poly.SetPoints(points)
    delaunay = vtkDelaunay2D()
    delaunay.SetInputData(poly)
    delaunay.Update()
    cells = delaunay.GetOutput().GetPolys()
    if cells is None or cells.GetNumberOfCells() == 0:
        return np.empty((0, 3), dtype=np.int64)
    # **`GetConnectivityArray`, not `GetData`**: VTK 9.6 deprecates the legacy cell array, and the
    # modern accessor already gives the corner indices with no counts to strip.
    from vtkmodules.util.numpy_support import vtk_to_numpy

    # **VTK's converters are untyped** (the  override says so): the annotation is
    # what tells a reader the shape is the corner indices, not a guess.
    ids: np.ndarray = vtk_to_numpy(cells.GetConnectivityArray())  # type: ignore[no-untyped-call]
    return ids.astype(np.int64, copy=False).reshape(-1, 3)


#: How many triangulations may be built at once, across the server.
#:
#: **Two, matching `MAX_CONCURRENT_CUTS`** (`stokes/run/planes.py`): both are the same kind of
#: work - a server-side numeric pass over a large array - and the reason for a limit is the same,
#: that a request queue behind a CPU-bound build is worse than an honest "preparing".
MAX_CONCURRENT_BUILDS = 2

#: How long a probe waits for a build slot before saying "preparing".
BUILD_QUEUE_TIMEOUT_S = 2.0


class TriangulationCache:
    """One triangulation per surface, built at most `MAX_CONCURRENT_BUILDS` at a time.

    **The "preparing" answer is the contract, not a fallback.** claude-win2: *"keep
    inverse-distance only as the fallback while the triangulation is not ready? No: show
    'preparing', never a less accurate number silently."* So a probe that finds no triangulation
    **does not build it inline** - that would put 319 ms inside a command's 200 ms budget - it
    starts a build and raises `TriangulationNotReady`, which the caller turns into "preparing".

    **A failed build is remembered**, so a surface that cannot be triangulated (a degenerate
    patch) does not re-attempt the 319 ms on every probe. That mirrors `RunStore._slice_errors`.
    """

    def __init__(self) -> None:
        self._built: dict[str, Triangulation] = {}
        self._failed: dict[str, str] = {}
        self._building: set[str] = set()
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(MAX_CONCURRENT_BUILDS)

    def get(self, key: str) -> Triangulation:
        """The triangulation for `key`, or `TriangulationNotReady` - **never a build inline**."""
        with self._lock:
            found = self._built.get(key)
            reason = self._failed.get(key)
        if found is not None:
            return found
        if reason is not None:
            raise OutsideSurface(reason)
        raise TriangulationNotReady(f"preparing the surface {key}; try again shortly")

    def request(self, key: str, positions: np.ndarray) -> None:
        """Start building `key` unless it is built, building, or known to fail. **Idempotent.**"""
        with self._lock:
            if key in self._built or key in self._building or key in self._failed:
                return  # already built, being built, or known to fail
            self._building.add(key)
        thread = threading.Thread(target=self._build, args=(key, positions), daemon=True)
        thread.start()

    def _build(self, key: str, positions: np.ndarray) -> None:
        if not self._slots.acquire(timeout=BUILD_QUEUE_TIMEOUT_S):
            # **Give the slot back to the queue**: a build that never started is not a failure of
            # the surface, so it must be retried rather than remembered as broken.
            with self._lock:
                self._building.discard(key)
            return
        try:
            built = triangulate(positions)
        except (OutsideSurface, ValueError) as exc:
            with self._lock:
                self._failed[key] = str(exc)
            return
        finally:
            self._slots.release()
            with self._lock:
                self._building.discard(key)
        with self._lock:
            self._built[key] = built

    def forget(self, key: str | None = None) -> None:
        """Drop one surface's triangulation, or all (a new run's results replace the old)."""
        with self._lock:
            if key is None:
                self._built.clear()
                self._failed.clear()
            else:
                self._built.pop(key, None)
                self._failed.pop(key, None)
