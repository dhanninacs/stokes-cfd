"""Seeds moved into the fluid, inlet-patch seeds, and vectors sampled through a volume (ADR-0030).

The owner's report: *"the stream lines and vectors are not correct and not volumetric"*. These
tests pin the agent half of the fix on datasets VTK builds in memory:

* an **L-shaped duct** (the plan of a mixing elbow: one arm along x, one along y, a corner cut out
  of the bounding box), so a seed grid laid over the BOX misses the fluid where the elbow is not;
* a **linear velocity field** on tetrahedra, which linear interpolation reproduces exactly, so a
  sampled vector can be compared with the formula and not just with itself.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

pytest.importorskip("vtkmodules")

from stokes_agent import sample, trace


def tets(
    low: tuple[float, float, float],
    high: tuple[float, float, float],
    cells: tuple[int, int, int],
    velocity: Any,
    keep: Any = None,
) -> Any:
    """Tetrahedra filling the box `low`..`high` (minus the cells `keep` rejects) with point `U`.

    `velocity(points) -> (n, 3)` and `keep(centres) -> bool mask` are numpy functions.
    """
    from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkImageData
    from vtkmodules.vtkFiltersCore import vtkThreshold
    from vtkmodules.vtkFiltersGeneral import vtkDataSetTriangleFilter

    grid = vtkImageData()
    grid.SetDimensions(cells[0] + 1, cells[1] + 1, cells[2] + 1)
    grid.SetOrigin(*low)
    grid.SetSpacing(*[(high[a] - low[a]) / cells[a] for a in range(3)])
    data: Any = grid
    if keep is not None:
        from vtkmodules.vtkFiltersCore import vtkCellCenters

        centres = vtkCellCenters()
        centres.SetInputData(grid)
        centres.Update()
        mask = keep(vtk_to_numpy(centres.GetOutput().GetPoints().GetData())).astype(float)
        flag = numpy_to_vtk(mask, deep=1)
        flag.SetName("keep")
        grid.GetCellData().AddArray(flag)
        cut = vtkThreshold()
        cut.SetInputData(grid)
        cut.SetInputArrayToProcess(0, 0, 0, 1, "keep")  # 1: cell data
        cut.SetLowerThreshold(0.5)
        cut.SetUpperThreshold(1.5)
        cut.Update()
        data = cut.GetOutput()
    split = vtkDataSetTriangleFilter()
    split.SetInputData(data)
    split.Update()
    mesh = split.GetOutput()
    points = vtk_to_numpy(mesh.GetPoints().GetData())
    array = numpy_to_vtk(np.ascontiguousarray(velocity(points), dtype=float), deep=1)
    array.SetName("U")
    mesh.GetPointData().AddArray(array)
    mesh.GetPointData().SetActiveVectors("U")
    return mesh


def elbow_plan(points: np.ndarray) -> np.ndarray:
    """An L-shaped duct's velocity: +x in the arm along x, +y in the arm along y, and round the
    bend on circles about the inner corner (the origin), so a line keeps its distance from it."""
    x, y = points[:, 0], points[:, 1]
    radius = np.maximum(np.hypot(x, y), 1e-9)
    bend = (x >= 0.0) & (y <= 0.0)
    ux = np.where(x < 0.0, 1.0, np.where(bend, -y / radius, 0.0))
    uy = np.where(x < 0.0, 0.0, np.where(bend, x / radius, 1.0))
    return np.stack([ux, uy, np.zeros_like(x)], axis=1)


def elbow() -> Any:
    """x in [-2, 1], y in [-1, 2], z in [0, 0.5]; the corner x < 0, y > 0 is not fluid (an L)."""
    return tets(
        (-2.0, -1.0, 0.0),
        (1.0, 2.0, 0.5),
        (24, 24, 4),
        elbow_plan,
        keep=lambda c: ~((c[:, 0] < 0.0) & (c[:, 1] > 0.0)),
    )


def inside(mesh: Any, points: np.ndarray) -> np.ndarray:
    from vtkmodules.vtkCommonDataModel import vtkStaticCellLocator

    locator = vtkStaticCellLocator()
    locator.SetDataSet(mesh)
    locator.BuildLocator()
    return np.array([locator.FindCell([float(v) for v in p]) >= 0 for p in points], dtype=bool)


# ---------------------------------------------------------------------------- seeds into the fluid


def test_a_seed_on_an_inlet_face_is_moved_inside_and_then_traces() -> None:
    """**A seed ON the boundary gave no line** - the inlet's face is where a trace must start."""
    mesh = elbow()
    on_face = np.array([[-2.0, -0.5, 0.25], [-2.0, -0.25, 0.1]])
    moved = trace.into_domain(mesh, on_face, inset=True)
    assert moved.shape == (2, 3)
    assert inside(mesh, moved).all()
    assert np.all(moved[:, 0] > -2.0) and np.all(moved[:, 0] < -2.0 + 3.0 / 24)
    lines = trace.unpack(trace.trace_mesh(mesh, moved))
    assert len(lines) == 2
    # The line runs through the VOLUME around the corner and out of the arm along y.
    assert all(line["points"][:, 1].max() > 1.5 for line in lines)


def test_a_seed_just_outside_is_moved_in_and_one_far_away_is_dropped() -> None:
    mesh = elbow()
    seeds = np.array([[-2.01, -0.5, 0.25], [-1.0, 1.0, 0.25], [9.0, 9.0, 9.0], [-1.0, -0.5, 0.25]])
    moved = trace.into_domain(mesh, seeds)
    # The first is a hair outside the inlet (moved in), the second sits in the elbow's missing
    # corner a cell or more from the fluid and the third is far away (both dropped), the fourth is
    # inside and kept exactly.
    assert moved.shape == (2, 3)
    assert inside(mesh, moved).all()
    assert moved[1] == pytest.approx([-1.0, -0.5, 0.25])


def test_surface_seeds_follow_the_area_and_cover_the_whole_face() -> None:
    # Two triangles: one of area 1, one of area 3, in the plane x = 0.
    positions = np.array([[0, 0, 0], [0, 1, 0], [0, 0, 2], [0, 3, 0], [0, 3, 2], [0, 6, 0]], float)
    triangles = np.array([[0, 1, 2], [3, 4, 5]])
    seeds = trace.surface_seeds(positions, triangles, 8)
    assert seeds.shape == (8, 3)
    assert np.sum(seeds[:, 1] < 1.5) == 2 and np.sum(seeds[:, 1] > 1.5) == 6
    assert trace.surface_seeds(positions, triangles[:0], 8).shape == (0, 3)


def test_box_seeds_miss_an_elbow_where_inlet_seeds_do_not() -> None:
    """**The defect, measured**: the Looks card's Line seeds are a row over the model's BOX.

    Laid across the box just inside its upstream end (5 % along the longest axis), most of the row
    falls in the corner of the box the elbow does not fill, and no line starts there. Seeds on the
    inlet face all start lines and all of them turn the corner.
    """
    mesh = elbow()
    row = np.array([[-2.0 + 0.05 * 3.0, -1.0 + (i + 0.5) / 20 * 3.0, 0.25] for i in range(20)])
    from_box = trace.trace_mesh(mesh, row)
    assert from_box["count"] <= 7  # only the third of the row that is in the arm along x
    face = np.array([[-2.0, -1.0 + (i + 0.5) / 20, 0.05 + 0.4 * (i % 5) / 4] for i in range(20)])
    from_inlet = trace.unpack(trace.trace_mesh(mesh, trace.into_domain(mesh, face, inset=True)))
    assert len(from_inlet) == 20
    assert all(line["reason"] == "left_domain" for line in from_inlet)
    assert all(line["points"][-1][1] > 1.9 for line in from_inlet)


# ------------------------------------------------------------------------------ volume vectors


def test_volume_vectors_fill_the_fluid_evenly_and_only_the_fluid() -> None:
    mesh = elbow()
    answer = sample.sample_mesh(mesh, 300)
    points, _vectors = sample.unpack(answer)
    # About the number asked for, although the fluid is two thirds of the box.
    assert 200 <= answer["count"] <= 300
    assert inside(mesh, points.astype(float)).all()
    # Through the depth, not on one plane: several z layers.
    assert len(np.unique(np.round(points[:, 2], 4))) >= 2
    # Evenly in space: both arms get arrows in proportion to their share of the fluid.
    arm_x = np.sum(points[:, 1] < 0.0)
    arm_y = np.sum(points[:, 0] > 0.0) - np.sum((points[:, 0] > 0.0) & (points[:, 1] < 0.0))
    assert arm_x > 0 and arm_y > 0
    assert 0.5 < arm_x / max(1, answer["count"]) < 0.8  # the x arm (with the corner) is 2/3
    assert answer["spacing"] > 0


def test_a_sampled_vector_is_the_field_at_its_point() -> None:
    """A linear field is reproduced exactly by tetrahedra, so the arrow must equal the formula."""

    def swirl(p: np.ndarray) -> np.ndarray:
        return np.stack([-p[:, 1], p[:, 0], 0.5 + 0.0 * p[:, 2]], axis=1)

    mesh = tets((-1.0, -1.0, 0.0), (1.0, 1.0, 1.0), (10, 10, 5), swirl)
    points, vectors = sample.unpack(sample.sample_mesh(mesh, 200))
    assert points.shape[0] > 100
    assert np.allclose(vectors, swirl(points.astype(float)), atol=1e-5)


def test_a_mesh_without_velocity_is_refused() -> None:
    from vtkmodules.vtkCommonDataModel import vtkUnstructuredGrid

    with pytest.raises(sample.SampleInvalid):
        sample.sample_mesh(vtkUnstructuredGrid(), 10)


def test_the_lattice_is_sized_and_keeps_a_flat_axis() -> None:
    grid, spacing = sample.lattice(np.array([0.0, 0.0, 0.0]), np.array([2.0, 1.0, 0.0]), 200)
    assert 150 <= grid.shape[0] <= 260
    assert np.all(grid[:, 2] == 0.0)
    assert spacing == pytest.approx(0.1, rel=0.01)
