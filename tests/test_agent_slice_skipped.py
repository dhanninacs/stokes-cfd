"""A slice through a cell VTK will not contour (the T0 review's item 4).

`vtkPolyhedron` refuses to contour a polyhedron it judges non-manifold and the cutter drops the
cell with a warning, leaving a hole in the slice. **The synthetic cell is an open polyhedron**:
a unit cube whose side face carries a point on edge 1-5 that the neighbouring face does not, which
VTK 9.6 reports as "not watertight or non-manifold" and cuts to nothing - measured, zero polygons at
z = 0.25, 0.5 and 0.75, where a closed cube gives one. The tetrahedral re-cut must give back the
whole unit cross-section, and the count must reach the slice header.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

CUBE = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CLOSED = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
#: Face [0, 1, 8, 5, 4] goes through point 8 on edge 1-5; face [1, 2, 6, 5] does not: open.
OPEN = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 8, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]


def _grid(cells: list[tuple[list[tuple[float, float, float]], list[list[int]]]]) -> Any:
    """A `vtkUnstructuredGrid` of polyhedra, each `(points, faces)`, with a `p` point array."""
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkCommonCore import vtkIdList, vtkPoints
    from vtkmodules.vtkCommonDataModel import VTK_POLYHEDRON, vtkUnstructuredGrid

    grid = vtkUnstructuredGrid()
    points = vtkPoints()
    for cell_points, faces in cells:
        base = points.GetNumberOfPoints()
        for point in cell_points:
            points.InsertNextPoint(*point)
        ids = vtkIdList()
        ids.InsertNextId(len(faces))
        for face in faces:
            ids.InsertNextId(len(face))
            for index in face:
                ids.InsertNextId(base + index)
        grid.InsertNextCell(VTK_POLYHEDRON, ids)
    grid.SetPoints(points)
    xyz = np.array([points.GetPoint(i) for i in range(points.GetNumberOfPoints())])
    pressure = numpy_to_vtk(xyz[:, 2].copy(), deep=True)  # p = z, so a cut at z reads z
    pressure.SetName("p")
    grid.GetPointData().AddArray(pressure)
    return grid


class _Reader:
    """Just enough of the OpenFOAM reader for `cut_plane`: an `internalMesh` block."""

    def __init__(self, mesh: Any) -> None:
        from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet, vtkMultiBlockDataSet

        self.output = vtkMultiBlockDataSet()
        self.output.SetNumberOfBlocks(1)
        self.output.SetBlock(0, mesh)
        self.output.GetMetaData(0).Set(vtkCompositeDataSet.NAME(), "internalMesh")

    def GetOutput(self) -> Any:
        return self.output


def _area(positions: np.ndarray, triangles: np.ndarray) -> float:
    corners = positions[triangles.astype("int64")]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    return float(np.linalg.norm(cross, axis=1).sum() / 2)


def test_the_open_cell_is_dropped_by_the_plain_cutter() -> None:
    """The premise, measured: the repro is a cell VTK's cutter really skips."""
    pytest.importorskip("vtkmodules")
    from vtkmodules.vtkCommonDataModel import vtkPlane

    from stokes_agent import extract

    plane = vtkPlane()
    plane.SetOrigin(0.3, 0.4, 0.5)
    plane.SetNormal(0, 0, 1)
    assert extract._cut(_grid([(CUBE, CLOSED)]), plane).GetNumberOfCells() == 1
    assert extract._cut(_grid([([*CUBE, (1, 0, 0.5)], OPEN)]), plane).GetNumberOfCells() == 0


@pytest.mark.parametrize("z", [0.25, 0.5, 0.75])
def test_a_skipped_cell_is_found_and_cut_again(z: float) -> None:
    pytest.importorskip("vtkmodules")
    from stokes_agent import extract

    shifted = [(x + 1.0, y, zz) for x, y, zz in CUBE]
    mesh = _grid([([*CUBE, (1, 0, 0.5)], OPEN), (shifted, CLOSED)])
    straddling = extract.straddling_cells(extract._with_cell_ids(mesh), (0.3, 0.4, z), (0, 0, 1))
    assert straddling.tolist() == [0, 1]

    positions, triangles, edges, found, counts = extract.cut_plane(
        _Reader(mesh), (0.3, 0.4, z), (0, 0, 1), ("p",)
    )
    assert counts == {"skipped_cells": 0, "recut_cells": 1}
    # **The whole cross-section is drawn**: the open cell's unit square plus the closed one's.
    assert _area(positions, triangles) == pytest.approx(2.0, abs=1e-6)
    # The re-cut pieces carry the same fields, interpolated: p = z on every point of the plane.
    assert found["p"].shape == (positions.shape[0], 1)
    assert np.allclose(found["p"][:, 0], z, atol=1e-6)
    # **Edges only from the cutter's own polygons** (the closed cube's square): the tets' pieces
    # add none, so no tet diagonal is drawn as a cell edge.
    assert edges.shape == (4, 2)


def test_a_plane_touching_a_cell_does_not_count_it_skipped() -> None:
    pytest.importorskip("vtkmodules")
    from stokes_agent import extract

    mesh = _grid([(CUBE, CLOSED)])
    assert extract.straddling_cells(mesh, (0.5, 0.5, 1.0), (0, 0, 1)).size == 0
    assert extract.straddling_cells(mesh, (0.5, 0.5, 2.0), (0, 0, 1)).size == 0
    *_rest, counts = extract.cut_plane(_Reader(mesh), (0.5, 0.5, 0.5), (0, 0, 1), ("p",))
    assert counts == {"skipped_cells": 0, "recut_cells": 0}


def test_cells_the_re_cut_cannot_recover_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-cut that gives nothing leaves the cell skipped, and says how many."""
    pytest.importorskip("vtkmodules")
    from vtkmodules.vtkCommonDataModel import vtkPolyData

    from stokes_agent import extract

    real_cut = extract._cut
    calls = {"n": 0}

    def cut(dataset: Any, plane: Any) -> Any:
        calls["n"] += 1
        return real_cut(dataset, plane) if calls["n"] == 1 else vtkPolyData()

    monkeypatch.setattr(extract, "_cut", cut)
    mesh = _grid([([*CUBE, (1, 0, 0.5)], OPEN)])
    positions, triangles, _edges, _found, counts = extract.cut_plane(
        _Reader(mesh), (0.3, 0.4, 0.5), (0, 0, 1), ("p",)
    )
    assert counts == {"skipped_cells": 1, "recut_cells": 0}
    assert positions.shape[0] == 0 and triangles.shape[0] == 0


def test_the_count_reaches_the_slice_header() -> None:
    """`pack_fields` writes `skipped_cells` (and `recut_cells`) only when they are not zero."""
    from stokes_agent import extract

    def slice_of(**counts: int) -> dict[str, Any]:
        return {
            "origin": [0.0, 0.0, 0.5],
            "normal": [0.0, 0.0, 1.0],
            "positions": np.array([[0, 0, 0.5], [1, 0, 0.5], [0, 1, 0.5]], dtype="float32"),
            "triangles": np.array([[0, 1, 2]], dtype="uint32"),
            "edges": np.zeros((0, 2), dtype="uint32"),
            "fields": {},
            **counts,
        }

    _data, header = extract.pack_fields([], slice_of(skipped_cells=3, recut_cells=2), 0.0, {})
    assert header["slice"]["skipped_cells"] == 3
    assert header["slice"]["recut_cells"] == 2
    _data, header = extract.pack_fields([], slice_of(skipped_cells=0, recut_cells=0), 0.0, {})
    assert "skipped_cells" not in header["slice"] and "recut_cells" not in header["slice"]


def test_run_fields_says_how_many_cells_the_slice_could_not_draw() -> None:
    """`run.fields`' `slice_skipped`, read defensively from the agent's header."""
    from stokes.run.store import RunStore

    assert RunStore._slice_skipped({"slice": {"triangles": 4, "skipped_cells": 3}}) == 3
    assert RunStore._slice_skipped({"slice": {"triangles": 4}}) == 0
    assert RunStore._slice_skipped({"slice": None}) == 0
    for wrong in (-1, True, "3", 2.5):
        assert RunStore._slice_skipped({"slice": {"skipped_cells": wrong}}) == 0
