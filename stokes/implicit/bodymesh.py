"""Mesh one implicit body: the display mesh a run shows and the surface a study measures.

The body is meshed about its own centre (so the mesher's cube is small numbers), at a cell of
1/``cells`` of its largest extent, no coarser than a quarter of its smallest feature and no finer
than 1/``finest`` of the extent (ADR-0035 decision 17's display rule). The positions come back in
metres in the world frame. The geometry worker (``stokes.geometry.worker_main``) and the
``geometry`` study engine (``stokes.explore.engine_geometry``) both call :func:`mesh_body`, so a
measured design point and the part on screen are the same mesh.

Import rule: the package's (numpy inside functions).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from stokes.implicit import graph, runner
from stokes.implicit.features import Body
from stokes.implicit.frame import translation

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

DISPLAY_CELLS = 192
FINEST_DISPLAY_CELLS = 1024
MM_PER_M = 1000.0


@dataclass
class BodyMesh:
    """A meshed body: positions in metres (world frame), triangles, and how it was made."""

    positions: npt.NDArray[np.float64]
    triangles: npt.NDArray[np.uint32]
    outcome: runner.MeshOutcome
    cube: runner.Cube
    nodes: int


@dataclass(frozen=True)
class DisplayRequest:
    """What :func:`mesh_body` asks the mesher for: the request, the body's centre (kernel units,
    the frame the mesher works in is centred on it) and the graph's size."""

    request: runner.MeshRequest
    centre: tuple[float, float, float]
    nodes: int


def display_request(
    body: Body, *, cells: int = DISPLAY_CELLS, finest: int = FINEST_DISPLAY_CELLS
) -> DisplayRequest:
    """The mesher request for ``body`` at ``cells`` across its largest extent (no coarser than a
    quarter of its smallest feature, no finer than ``finest`` across)."""
    low, high = body.bounded()
    centre = ((low[0] + high[0]) / 2, (low[1] + high[1]) / 2, (low[2] + high[2]) / 2)
    root = body.field(translation((-centre[0], -centre[1], -centre[2])))
    nodes = graph.check(root)
    text = graph.to_text(root)
    local = (
        (low[0] - centre[0], low[1] - centre[1], low[2] - centre[2]),
        (high[0] - centre[0], high[1] - centre[1], high[2] - centre[2]),
    )
    extent = max(b - a for a, b in zip(low, high, strict=True))
    cell = max(min(extent / cells, body.smallest / 4), extent / finest)
    return DisplayRequest(runner.MeshRequest(text, runner.cube_for(local, cell)), centre, nodes)


def mesh_body(
    body: Body,
    cache: Path,
    *,
    cells: int = DISPLAY_CELLS,
    finest: int = FINEST_DISPLAY_CELLS,
    cancel: threading.Event | None = None,
    progress: Callable[[float, str], None] | None = None,
    started: Callable[[int, runner.Cube], None] | None = None,
) -> BodyMesh:
    """Mesh ``body`` with the ``stokes-implicit-mesh`` program (cached in ``cache``).

    ``started(nodes, cube)`` is told the graph's size and the cube before the program runs.
    """
    import numpy as np

    want = display_request(body, cells=cells, finest=finest)
    centre, nodes, cube = want.centre, want.nodes, want.request.cube
    if started is not None:
        started(nodes, cube)
    outcome = runner.mesh(want.request, cache, cancel=cancel, progress=progress)
    kernel = outcome.mesh
    positions = (kernel.positions.astype(np.float64) + np.array(centre)) / MM_PER_M
    triangles = np.ascontiguousarray(kernel.triangles, dtype=np.uint32)
    return BodyMesh(positions, triangles, outcome, cube, nodes)
