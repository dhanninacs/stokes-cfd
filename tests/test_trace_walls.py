"""Wall-following streamlines (`stokes_agent.walk`): a line that meets a wall slides along it."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

pytest.importorskip("vtkmodules")


def _channel(velocity: tuple[float, float, float]) -> Any:
    """A 20 x 4 x 4 channel along x of 1 mm cells, each carrying `velocity` as cell data."""
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkCommonDataModel import vtkImageData
    from vtkmodules.vtkFiltersCore import vtkAppendFilter

    grid = vtkImageData()
    grid.SetDimensions(21, 5, 5)
    grid.SetSpacing(1e-3, 1e-3, 1e-3)
    cells = grid.GetNumberOfCells()
    u = numpy_to_vtk(np.tile(np.asarray(velocity, dtype=float), (cells, 1)), deep=True)
    u.SetName("U")
    grid.GetCellData().AddArray(u)
    unstructured = vtkAppendFilter()
    unstructured.AddInputData(grid)
    unstructured.Update()
    return unstructured.GetOutput()


def _outlet() -> np.ndarray:
    """Points on the channel's x = 20 mm face, the open end."""
    ys, zs = np.meshgrid(np.linspace(0, 4e-3, 9), np.linspace(0, 4e-3, 9))
    return np.column_stack([np.full(ys.size, 20e-3), ys.ravel(), zs.ravel()])


def test_a_line_driven_into_a_side_wall_slides_along_it_to_the_outlet() -> None:
    from stokes_agent import trace, walk

    # Mostly into the y = 4 mm wall: a tracer that stops at walls ends there within a few cells.
    mesh = _channel((1.0, 3.0, 0.0))
    seed = np.asarray([[1e-3, 1e-3, 2e-3]])
    stopped = trace.unpack(trace.trace_mesh(_pointwise(mesh), seed))
    assert stopped[0]["points"][-1][0] < 5e-3
    walked = trace.unpack(walk.walk_mesh(mesh, seed, open_points=_outlet()))
    assert len(walked) == 1
    end = walked[0]["points"][-1]
    # It ends within `OPEN_CELLS` cells of the open end, having reached it.
    assert end[0] > 20e-3 - walk.OPEN_CELLS * 1e-3 - 1e-4, end
    assert walked[0]["reason"] == "left_domain"
    # It stayed in the channel the whole way.
    assert np.all(walked[0]["points"][:, 1] <= 4e-3 + 1e-9)


def test_backward_runs_upstream_and_both_joins_the_two_halves() -> None:
    from stokes_agent import trace, walk

    mesh = _channel((1.0, 0.0, 0.0))
    seed = np.asarray([[10e-3, 2e-3, 2e-3]])
    back = trace.unpack(walk.walk_mesh(mesh, seed, direction="backward"))
    assert back[0]["points"][-1][0] < 1e-3
    both = trace.unpack(walk.walk_mesh(mesh, seed, direction="both", open_points=_outlet()))
    assert both[0]["points"][0][0] < 1e-3 and both[0]["points"][-1][0] > 18e-3


def _pointwise(mesh: Any) -> Any:
    """The mesh with its cell velocity also as point data, which `trace_mesh` integrates."""
    from vtkmodules.vtkFiltersCore import vtkCellDataToPointData

    convert = vtkCellDataToPointData()
    convert.SetInputData(mesh)
    convert.PassCellDataOn()
    convert.Update()
    return convert.GetOutput()
