"""Streamline budget: 200 lines through a one-million-cell volume (ADR-0030).

The trace is the agent's `stokes_agent.trace.trace_mesh`, timed on an in-memory `vtkImageData` of
99^3 cells (970,299) with a uniform oblique velocity, 200 seeds across the inlet end and lines
resampled to 400 points. The reader that loads the solved case is not in the figure: it is the same
read the slices and iso-surfaces pay for. The answer must stay small enough to cross the wire in
one reply.

Budgets, scaled by the machine factor: 2 s for the trace, 2 MB for the answer.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.perf.harness import budget

TRACE_TARGET_MS = 2000.0
MAX_ANSWER_BYTES = 2 * 1024 * 1024


@pytest.mark.perf
def test_two_hundred_lines_through_a_million_cells() -> None:
    pytest.importorskip("vtkmodules")
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkCommonDataModel import vtkImageData

    from stokes_agent import trace

    n = 100
    grid = vtkImageData()
    grid.SetDimensions(n, n, n)
    grid.SetSpacing(1.0 / (n - 1), 1.0 / (n - 1), 1.0 / (n - 1))
    velocity = np.zeros((n**3, 3))
    velocity[:, 0] = 1.0
    velocity[:, 1] = 0.2
    array = numpy_to_vtk(velocity, deep=1)
    array.SetName("U")
    grid.GetPointData().AddArray(array)
    seeds = np.stack([np.full(200, 0.02), np.linspace(0.05, 0.9, 200), np.full(200, 0.5)], axis=1)

    with budget("streamlines.trace_200_lines_1m_cells", target_ms=TRACE_TARGET_MS):
        answer = trace.trace_mesh(grid, seeds, max_points=400)

    assert answer["count"] == 200
    assert len(answer["points"]) + len(answer["speeds"]) <= MAX_ANSWER_BYTES
