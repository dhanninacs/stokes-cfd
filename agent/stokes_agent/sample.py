"""Velocity vectors sampled through the 3-D volume of a solved case (ADR-0030 addendum).

Arrows on a slice show the flow in one plane; a mixing elbow's jet, a swirl or a recirculation
leaves that plane at once. This samples the case's velocity on an even lattice **through the
fluid itself** - the internal mesh `stokes_agent.trace` integrates through - so the arrows fill
the volume the way the flow does.

**Even in space, not by speed**: the lattice is regular and the points outside the fluid are
dropped (no cell contains them), so a region the flow barely moves through still gets
its arrows and reads as slow, rather than going empty because the fast core took them all.

**The lattice is sized to the FLUID, not the box**: a first coarse probe measures what share of the
bounding box is fluid, and the second lattice is spaced so about `count` of its points land inside.
An L-shaped elbow fills a third of its box; a lattice sized to the box would give a third of the
arrows asked for.

The velocity is the reader's cell-to-point `U` interpolated at each lattice point (the same field
the streamlines follow), so an arrow and a line through the same point agree.

Import rules: numpy at module level; VTK inside the functions. `sample_mesh` takes any VTK dataset
with a point vector `U`, which is what the tests build in memory.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

#: The most arrows one sample returns: a picture of more is a smear, and each is an instance.
MAX_SAMPLES = 2000
#: The points of the first, measuring lattice.
PROBE_POINTS = 4096
#: The most lattice points one probe may ask for.
MAX_LATTICE = 200_000


class SampleInvalid(ValueError):
    """A sample request that cannot be answered as asked."""


def lattice(low: np.ndarray, high: np.ndarray, points: int) -> tuple[np.ndarray, float]:
    """About `points` cell-centred lattice points filling the box `low`..`high`, and the spacing.

    A flat box (a 2-D case one cell thick) gets one layer along its thin axis rather than none.
    """
    size = np.maximum(np.asarray(high, dtype=float) - np.asarray(low, dtype=float), 0.0)
    live = size[size > 0]
    if live.size == 0:
        raise SampleInvalid("the mesh has no extent to sample")
    spacing = (float(np.prod(live)) / max(1, int(points))) ** (1.0 / live.size)
    counts = [max(1, math.ceil(float(s) / spacing)) if s > 0 else 1 for s in size]
    axes = [
        np.asarray(low, dtype=float)[a] + (np.arange(counts[a]) + 0.5) * (size[a] / counts[a])
        for a in range(3)
    ]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    return grid, spacing


def _probe(mesh: Any, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`U` at `points` and which of them lie in the mesh: the containing cell's own interpolation.

    **One `vtkStaticCellLocator` (BSD) and the cell's weights**, not `vtkProbeFilter`: measured on
    a snappyHexMesh elbow (77 k cells, polyhedra), the probe filter took 3.8 s for 5,115 points and
    this takes a fraction of a second, with the same answer (the weights are the cell's own
    `EvaluatePosition`, which is what the probe uses).
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkGenericCell, vtkStaticCellLocator

    raw = vtk_to_numpy(mesh.GetPointData().GetArray("U"))  # type: ignore[no-untyped-call]
    field: np.ndarray = np.asarray(raw, dtype=np.float64)
    locator = vtkStaticCellLocator()
    locator.SetDataSet(mesh)
    locator.BuildLocator()
    cell = vtkGenericCell()
    weights = [0.0] * max(8, int(mesh.GetMaxCellSize()))
    pcoords = [0.0, 0.0, 0.0]
    out = np.zeros((len(points), 3))
    mask = np.zeros(len(points), dtype=bool)
    for index, point in enumerate(np.asarray(points, dtype=float)):
        found = locator.FindCell([float(v) for v in point], 0.0, cell, pcoords, weights)
        if found < 0:
            continue
        ids = cell.GetPointIds()
        corners = [ids.GetId(k) for k in range(ids.GetNumberOfIds())]
        out[index] = np.asarray(weights[: len(corners)]) @ field[corners]
        mask[index] = True
    return out, mask


def sample_mesh(mesh: Any, count: int = 400) -> dict[str, Any]:
    """About `count` velocity samples on an even lattice through `mesh`'s fluid.

    Returns `{count, points, vectors, spacing}`: `points` and `vectors` are float32 `(n, 3)` bytes
    (little endian), `spacing` the lattice step in metres (an arrow's natural length). `count` is
    clamped to 1..`MAX_SAMPLES`; fewer come back when the fluid is too thin for the lattice.
    """
    velocity = mesh.GetPointData().GetArray("U")
    if velocity is None or velocity.GetNumberOfComponents() != 3:
        raise SampleInvalid("the mesh has no point velocity U to sample")
    if not (isinstance(count, int | float) and math.isfinite(count)):
        raise SampleInvalid("the sample count must be a number")
    want = max(1, min(int(count), MAX_SAMPLES))
    bounds = np.asarray(mesh.GetBounds(), dtype=float)
    low, high = bounds[0::2], bounds[1::2]
    first, _ = lattice(low, high, max(PROBE_POINTS, 4 * want))
    _, inside = _probe(mesh, first)
    share = float(inside.mean()) if inside.size else 0.0
    if share <= 0.0:
        return _answer(np.zeros((0, 3)), np.zeros((0, 3)), 0.0)
    points, spacing = lattice(low, high, min(MAX_LATTICE, max(want, round(want / share))))
    vectors, inside = _probe(mesh, points)
    points, vectors = points[inside], vectors[inside]
    if points.shape[0] > want:
        # Evenly by index along the lattice's own order, so the thinning stays even in space.
        keep = np.unique(np.linspace(0, points.shape[0] - 1, want).round().astype(int))
        points, vectors = points[keep], vectors[keep]
    return _answer(points, vectors, spacing)


def _answer(points: np.ndarray, vectors: np.ndarray, spacing: float) -> dict[str, Any]:
    return {
        "count": int(points.shape[0]),
        "points": np.ascontiguousarray(points, dtype="<f4").tobytes(),
        "vectors": np.ascontiguousarray(vectors, dtype="<f4").tobytes(),
        "spacing": float(spacing),
    }


def unpack(answer: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """The `(points, vectors)` of a `sample_mesh` answer as `(n, 3)` arrays."""
    points = np.frombuffer(answer["points"], dtype="<f4").reshape(-1, 3)
    vectors = np.frombuffer(answer["vectors"], dtype="<f4").reshape(-1, 3)
    return points, vectors


def sample_case(
    case_dir: Path, count: int = 400, *, time: float | None = None, engine: str | None = None
) -> dict[str, Any]:
    """Sample the velocity through the internal mesh of the solved case in `case_dir`."""
    from stokes_agent import extract, trace

    reader, used = trace.open_volume(case_dir, patches=False, time=time, engine=engine)
    mesh = extract._internal_mesh(reader)
    if mesh is None:
        raise SampleInvalid("the case has no internal mesh to sample")
    answer = sample_mesh(mesh, count)
    answer["time"] = used
    return answer
