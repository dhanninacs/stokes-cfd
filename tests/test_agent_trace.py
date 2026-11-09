"""`stokes_agent.trace`: streamlines through a volume, on a dataset VTK builds in memory (ADR-0030).

A hand-written OpenFOAM case has one cell, which cannot carry a trace, so the integration is
tested on
a `vtkImageData` box with a known velocity and the reader half is left to the engine tests.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("vtkmodules")

from stokes_agent import trace


def box(
    nx: int = 21, ny: int = 11, nz: int = 11, velocity: tuple[float, float, float] = (1.0, 0.0, 0.0)
):  # type: ignore[no-untyped-def]
    """A box 2 x 1 x 1 m with the same velocity everywhere (point data U)."""
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkCommonDataModel import vtkImageData

    grid = vtkImageData()
    grid.SetDimensions(nx, ny, nz)
    grid.SetSpacing(2.0 / (nx - 1), 1.0 / (ny - 1), 1.0 / (nz - 1))
    count = nx * ny * nz
    array = numpy_to_vtk(np.tile(np.array(velocity, dtype=float), (count, 1)), deep=1)
    array.SetName("U")
    grid.GetPointData().AddArray(array)
    grid.GetPointData().SetActiveVectors("U")
    # Tetrahedra, as an OpenFOAM mesh's cells are small compared with the domain.
    from vtkmodules.vtkFiltersGeneral import vtkDataSetTriangleFilter

    tets = vtkDataSetTriangleFilter()
    tets.SetInputData(grid)
    tets.Update()
    return tets.GetOutput()


def test_a_line_runs_the_whole_length_of_the_box_in_the_volume() -> None:
    answer = trace.trace_mesh(box(), np.array([[0.05, 0.5, 0.5]]))
    lines = trace.unpack(answer)
    assert len(lines) == 1
    line = lines[0]
    assert line["points"][0][0] == pytest.approx(0.05, abs=1e-3)
    assert line["points"][-1][0] > 1.9  # the far wall is at x = 2
    assert np.allclose(line["points"][:, 1:], 0.5, atol=1e-4)  # straight, in 3-D
    assert line["reason"] == "left_domain"
    assert np.allclose(line["speeds"], 1.0, atol=1e-4)


def test_the_length_limit_is_in_metres_and_is_the_reason() -> None:
    lines = trace.unpack(trace.trace_mesh(box(), np.array([[0.05, 0.5, 0.5]]), max_length=0.5))
    travelled = float(np.linalg.norm(lines[0]["points"][-1] - lines[0]["points"][0]))
    assert travelled == pytest.approx(0.5, abs=0.06)
    assert lines[0]["reason"] == "length"


def test_a_backward_trace_goes_upstream_and_both_makes_one_line_each_way_joined() -> None:
    back = trace.unpack(trace.trace_mesh(box(), np.array([[1.0, 0.5, 0.5]]), direction="backward"))[
        0
    ]["points"]
    assert back[-1][0] < 0.1
    both = trace.unpack(trace.trace_mesh(box(), np.array([[1.0, 0.5, 0.5]]), direction="both"))
    xs = np.concatenate([line["points"][:, 0] for line in both])
    assert xs.min() < 0.1 and xs.max() > 1.9


def test_a_long_line_is_resampled_not_refused_and_keeps_its_ends() -> None:
    seed = np.array([[0.05, 0.5, 0.5]])
    full = trace.unpack(trace.trace_mesh(box(), seed, max_step_cells=0.05))[0]["points"]
    short = trace.unpack(trace.trace_mesh(box(), seed, max_step_cells=0.05, max_points=10))[0][
        "points"
    ]
    assert len(short) <= 10 < len(full)
    assert np.allclose(short[0], full[0]) and np.allclose(short[-1], full[-1])


def test_a_seed_outside_the_mesh_gives_no_line() -> None:
    answer = trace.trace_mesh(box(), np.array([[5.0, 5.0, 5.0], [0.05, 0.5, 0.5]]))
    assert answer["count"] == 1


def test_the_steps_cap_is_a_reason_of_its_own() -> None:
    lines = trace.unpack(
        trace.trace_mesh(box(), np.array([[0.05, 0.5, 0.5]]), max_steps=3, max_step_cells=0.05)
    )
    assert lines[0]["reason"] == "steps"


@pytest.mark.parametrize(
    "bad",
    [
        {"seeds": np.zeros((0, 3))},
        {"seeds": np.zeros((2, 2))},
        {"seeds": np.full((1, 3), np.nan)},
        {"seeds": np.zeros((201, 3))},
        {"seeds": np.array([[0.1, 0.5, 0.5]]), "direction": "sideways"},
        {"seeds": np.array([[0.1, 0.5, 0.5]]), "max_length": -1.0},
    ],
)
def test_bad_requests_are_refused(bad: dict) -> None:  # type: ignore[type-arg]
    seeds = bad.pop("seeds")
    with pytest.raises(trace.TraceInvalid):
        trace.trace_mesh(box(), seeds, **bad)


def test_a_still_fluid_has_nothing_to_trace() -> None:
    with pytest.raises(trace.TraceInvalid):
        trace.trace_mesh(box(velocity=(0.0, 0.0, 0.0)), np.array([[0.5, 0.5, 0.5]]))
