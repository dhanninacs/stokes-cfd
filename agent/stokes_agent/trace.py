"""Streamlines through the 3-D volume of a solved case (ADR-0030).

The agent already reads a finished case with `vtkOpenFOAMReader` for the slices and the
iso-surfaces (`stokes_agent.extract`); this module integrates the velocity through the
*internal mesh itself*, so a line is free to leave any plane and run the length of the pipe.
VTK is BSD-licensed and is the integrator (`vtkStreamTracer`); numpy carries the arrays.

**What a trace is told, in the units a user can reason about**

* `max_length` - the longest a line may run, in metres (default: three times the mesh's diagonal);
* the step is measured in **cells**: it starts at `step_cells` of the cell the line is in, and the
  adaptive Runge-Kutta 4-5 integrator may grow it to `max_step_cells` or shrink it to
  `min_step_cells`;
* `max_steps` guards a runaway (a line looping in a recirculation) and `terminal_fraction` stops a
  line that has slowed to that fraction of the case's largest speed (a stagnation point).

**Why a line stopped is part of the answer** (`reasons`): `left_domain` (an outlet or a wall),
`length`, `steps` or `stagnation`. A line that ran out of steps is not a line that reached an
end, and the picture can say so.

A line longer than `max_points` is resampled by index (ends kept), never refused.

**Where a line starts** (ADR-0030 amendment 1): the points given, or seeds spread over named
boundary patches (the inlets, `patch_seed_points`); either way they are moved into the fluid first
(`into_domain`), because a seed on a face or a hair outside it starts no line.

Import rules: numpy at module level; VTK inside the functions. The reader half (`trace_case`) is the
only part that needs a case on disk; `trace_mesh` takes any VTK dataset with a point vector `U`,
which
is what the tests build in memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

#: The most seeds one trace may start from (the same cap as `stokes.post.seeds.MAX_SEEDS`).
MAX_SEEDS = 200
#: The most points one returned line may hold.
MAX_POINTS = 1000
#: The most integration steps one line may take.
MAX_STEPS = 20000

#: VTK's `ReasonForTermination` values, by name. Anything else is `unknown`.
REASONS: dict[int, str] = {
    1: "left_domain",
    4: "length",
    5: "steps",
    6: "stagnation",
}


class TraceInvalid(ValueError):
    """A trace request that cannot be answered as asked."""


def _check(seeds: np.ndarray, direction: str, max_length: float | None) -> None:
    if seeds.ndim != 2 or seeds.shape[1] != 3 or not np.all(np.isfinite(seeds)):
        raise TraceInvalid("seeds must be (n, 3) finite points")
    if seeds.shape[0] == 0:
        raise TraceInvalid("no seeds: give at least one point")
    if seeds.shape[0] > MAX_SEEDS:
        raise TraceInvalid(f"{seeds.shape[0]} seeds is more than the {MAX_SEEDS} one trace takes")
    if direction not in ("forward", "backward", "both"):
        raise TraceInvalid(f"direction must be forward, backward or both; got {direction!r}")
    if max_length is not None and not (np.isfinite(max_length) and max_length > 0):
        raise TraceInvalid("the maximum length must be a positive number of metres")


def trace_mesh(
    mesh: Any,
    seeds: np.ndarray,
    *,
    direction: str = "forward",
    max_length: float | None = None,
    step_cells: float = 0.2,
    min_step_cells: float = 0.01,
    max_step_cells: float = 0.5,
    max_steps: int = MAX_STEPS,
    terminal_fraction: float = 1e-4,
    max_points: int = MAX_POINTS,
) -> dict[str, Any]:
    """Trace from `seeds` through `mesh` (a VTK dataset whose point data holds a vector `U`).

    Returns `{count, points, speeds, offsets, reasons}`: `points` is float32 `(n, 3)` bytes (little
    endian), `speeds` float32 `(n,)` bytes, `offsets` has `count + 1` integers (line i is
    `points[offsets[i]:offsets[i+1]]`) and `reasons` one name per line. A seed outside the mesh
    gives no line, so `count` can be below the number of seeds.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy

    seeds = np.asarray(seeds, dtype=float)
    _check(seeds, direction, max_length)
    velocity = mesh.GetPointData().GetArray("U")
    if velocity is None or velocity.GetNumberOfComponents() != 3:
        raise TraceInvalid("the mesh has no point velocity U to trace")
    all_speeds = np.linalg.norm(vtk_to_numpy(velocity), axis=1)  # type: ignore[no-untyped-call]
    top = float(all_speeds.max()) if all_speeds.size else 0.0
    if top <= 0.0:
        raise TraceInvalid("the velocity is zero everywhere; there is nothing to trace")
    if max_length is None:
        low, high = mesh.GetBounds()[0::2], mesh.GetBounds()[1::2]
        max_length = 3.0 * float(np.linalg.norm(np.asarray(high) - np.asarray(low)))
    tracer = _tracer(
        mesh,
        seeds,
        direction=direction,
        max_length=float(max_length),
        steps=(step_cells, min_step_cells, max_step_cells),
        max_steps=max_steps,
        terminal_speed=float(terminal_fraction) * top,
    )
    tracer.Update()
    return _collect(tracer.GetOutput(), max_points)


def _tracer(
    mesh: Any,
    seeds: np.ndarray,
    *,
    direction: str,
    max_length: float,
    steps: tuple[float, float, float],
    max_steps: int,
    terminal_speed: float,
) -> Any:
    """The configured `vtkStreamTracer` (untyped, so `Any`)."""
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkPolyData
    from vtkmodules.vtkFiltersFlowPaths import vtkStreamTracer

    poly = vtkPolyData()
    points = vtkPoints()
    for s in seeds:
        points.InsertNextPoint(float(s[0]), float(s[1]), float(s[2]))
    poly.SetPoints(points)
    tracer = vtkStreamTracer()
    tracer.SetInputData(mesh)
    tracer.SetInputArrayToProcess(0, 0, 0, 0, "U")
    tracer.SetSourceData(poly)
    tracer.SetIntegratorTypeToRungeKutta45()
    # vtkStreamTracer.CELL_LENGTH_UNIT is 2 (LENGTH_UNIT is 1): the step is a fraction of the cell.
    tracer.SetIntegrationStepUnit(2)
    tracer.SetInitialIntegrationStep(float(steps[0]))
    tracer.SetMinimumIntegrationStep(float(steps[1]))
    tracer.SetMaximumIntegrationStep(float(steps[2]))
    tracer.SetMaximumPropagation(max_length)
    tracer.SetMaximumNumberOfSteps(int(min(max_steps, MAX_STEPS)))
    tracer.SetTerminalSpeed(terminal_speed)
    if direction == "backward":
        tracer.SetIntegrationDirectionToBackward()
    elif direction == "both":
        tracer.SetIntegrationDirectionToBoth()
    else:
        tracer.SetIntegrationDirectionToForward()
    return tracer


def _collect(out: Any, max_points: int) -> dict[str, Any]:
    """The tracer's output polydata as the answer's arrays, long lines resampled."""
    from vtkmodules.util.numpy_support import vtk_to_numpy

    if out is None or out.GetNumberOfPoints() == 0:
        return _answer([], [], [0], [])
    coords: np.ndarray = vtk_to_numpy(out.GetPoints().GetData())  # type: ignore[no-untyped-call]
    carried = out.GetPointData().GetArray("U")
    along: np.ndarray = (
        np.linalg.norm(vtk_to_numpy(carried), axis=1)  # type: ignore[no-untyped-call]
        if carried is not None
        else np.zeros(coords.shape[0])
    )
    cells = out.GetLines()
    offsets: np.ndarray = vtk_to_numpy(cells.GetOffsetsArray())  # type: ignore[no-untyped-call]
    connectivity: np.ndarray = vtk_to_numpy(cells.GetConnectivityArray())  # type: ignore[no-untyped-call]
    why = out.GetCellData().GetArray("ReasonForTermination")
    code_of_line = (
        vtk_to_numpy(why).astype(int)  # type: ignore[no-untyped-call]
        if why is not None
        else np.zeros(cells.GetNumberOfCells(), dtype=int)
    )
    line_points: list[np.ndarray] = []
    line_speeds: list[np.ndarray] = []
    reasons: list[str] = []
    for i in range(cells.GetNumberOfCells()):
        ids = connectivity[int(offsets[i]) : int(offsets[i + 1])]
        if ids.size < 2:
            continue
        if ids.size > max_points:
            ids = ids[np.unique(np.linspace(0, ids.size - 1, max_points).round().astype(int))]
        line_points.append(coords[ids])
        line_speeds.append(along[ids])
        reasons.append(REASONS.get(int(code_of_line[i]) if i < code_of_line.size else 0, "unknown"))
    boundaries = [0]
    for line in line_points:
        boundaries.append(boundaries[-1] + line.shape[0])
    return _answer(line_points, line_speeds, boundaries, reasons)


def _answer(
    line_points: list[np.ndarray],
    line_speeds: list[np.ndarray],
    offsets: list[int],
    reasons: list[str],
) -> dict[str, Any]:
    points = np.concatenate(line_points) if line_points else np.zeros((0, 3))
    speeds = np.concatenate(line_speeds) if line_speeds else np.zeros(0)
    return {
        "count": len(line_points),
        "points": np.ascontiguousarray(points, dtype="<f4").tobytes(),
        "speeds": np.ascontiguousarray(speeds, dtype="<f4").tobytes(),
        "offsets": [int(v) for v in offsets],
        "reasons": reasons,
    }


def unpack(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """The lines of a `trace_mesh` answer as `{points: (n, 3), speeds: (n,), reason}` dicts."""
    points = np.frombuffer(answer["points"], dtype="<f4").reshape(-1, 3)
    speeds = np.frombuffer(answer["speeds"], dtype="<f4")
    offsets = answer["offsets"]
    return [
        {
            "points": points[offsets[i] : offsets[i + 1]],
            "speeds": speeds[offsets[i] : offsets[i + 1]],
            "reason": answer["reasons"][i],
        }
        for i in range(answer["count"])
    ]


def into_domain(mesh: Any, seeds: np.ndarray, *, inset: bool = False) -> np.ndarray:
    """`seeds` moved into the fluid: the points a tracer can start from, in their order.

    **Why**: a seed ON a boundary face (an inlet's own surface) or a hair outside it is where the
    field is least defined, and `vtkStreamTracer` answers it with a line of one point or none. A
    seed outside every cell but within one cell's size of the mesh is moved to the nearest cell, a
    third of the way from the nearest point towards that cell's centre; with `inset` every seed is
    (a patch's seeds lie on the boundary by construction, whichever way its faces are wound). A
    seed farther away than its nearest cell's size is dropped: it is in a hole or outside the
    model, and no line starts there.
    """
    from vtkmodules.vtkCommonCore import reference
    from vtkmodules.vtkCommonDataModel import vtkGenericCell, vtkStaticCellLocator

    locator = vtkStaticCellLocator()
    locator.SetDataSet(mesh)
    locator.BuildLocator()
    cell = vtkGenericCell()
    kept: list[np.ndarray] = []
    for seed in np.asarray(seeds, dtype=float).reshape(-1, 3):
        point = [float(seed[0]), float(seed[1]), float(seed[2])]
        found = int(locator.FindCell(point))
        if found >= 0 and not inset:
            kept.append(np.asarray(point))
            continue
        if found < 0:
            # **The cell id only**: measured on a snappyHexMesh case, `FindClosestPoint` names the
            # right polyhedron but answers garbage for its point and -1 for the distance, so the
            # distance is taken from the cell's own box below.
            closest = [0.0, 0.0, 0.0]
            cell_id, sub_id, dist2 = reference(-1), reference(0), reference(0.0)
            # The stub types the out-parameters as int and float; at run time they are references.
            locator.FindClosestPoint(point, closest, cell, cell_id, sub_id, dist2)  # type: ignore[call-overload]
            found = int(cell_id)
            if found < 0:
                continue
        bounds = np.asarray(mesh.GetCell(found).GetBounds(), dtype=float)
        low, high = bounds[0::2], bounds[1::2]
        near = np.clip(np.asarray(point), low, high)
        if float(np.linalg.norm(np.asarray(point) - near)) > float(np.linalg.norm(high - low)):
            continue
        centre = (low + high) / 2.0
        for moved in (near + (centre - near) / 3.0, centre):
            if locator.FindCell([float(v) for v in moved]) >= 0:
                kept.append(moved)
                break
    return np.asarray(kept, dtype=float).reshape(-1, 3)


def surface_seeds(positions: np.ndarray, triangles: np.ndarray, n: int) -> np.ndarray:
    """`n` points spread over a surface by area: triangle centroids at area-weighted quantiles.

    The rule of `stokes.post.seeds.patch_seeds` (deterministic, a large triangle picked more than
    once, a degenerate one never) without the move along the normal: `into_domain` moves the points
    into the fluid whichever way a patch's faces are wound.
    """
    pts = np.asarray(positions, dtype=float).reshape(-1, 3)
    tri = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if n <= 0 or tri.shape[0] == 0:
        return np.zeros((0, 3))
    a, b, c = pts[tri[:, 0]], pts[tri[:, 1]], pts[tri[:, 2]]
    area2 = np.linalg.norm(np.cross(b - a, c - a), axis=1)
    if not np.any(area2 > 0):
        return np.zeros((0, 3))
    cum = np.cumsum(area2)
    quantiles = (np.arange(n) + 0.5) / n * cum[-1]
    picked = np.minimum(np.searchsorted(cum, quantiles), tri.shape[0] - 1)
    return np.asarray((a[picked] + b[picked] + c[picked]) / 3.0, dtype=float)


def _patch_blocks(reader: Any) -> dict[str, Any]:
    """The reader's boundary patches by name (a reader opened with its patches on)."""
    from stokes_agent import extract

    output = reader.GetOutput()
    found: dict[str, Any] = {}
    for index in range(output.GetNumberOfBlocks()):
        meta = output.GetMetaData(index)
        if meta is None or meta.Get(extract._name_key()) != "boundary":
            continue
        boundary = output.GetBlock(index)
        for at in range(boundary.GetNumberOfBlocks()):
            block, info = boundary.GetBlock(at), boundary.GetMetaData(at)
            if block is not None and info is not None and block.GetNumberOfPoints() > 0:
                found[str(info.Get(extract._name_key()))] = block
    return found


def patch_seed_points(reader: Any, names: list[str], count: int) -> np.ndarray:
    """Seeds spread over the named boundary patches of an opened case, **shared evenly by patch**.

    Even by patch, not by area: a mixing elbow's small inlet has a fraction of the large one's area,
    and an area share would give the jet that makes the mixing a line or two. Within a patch the
    seeds follow the area. A name the case does not have is refused, so a typo is not a picture with
    one inlet silently missing.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy

    from stokes_agent import extract

    if not names:
        raise TraceInvalid("no patches to seed from")
    count = max(len(names), min(int(count), MAX_SEEDS))
    blocks = _patch_blocks(reader)
    missing = [name for name in names if name not in blocks]
    if missing:
        raise TraceInvalid(f"the case has no patch {', '.join(map(repr, missing))} to seed from")
    out: list[np.ndarray] = []
    for index, name in enumerate(names):
        share = count // len(names) + (1 if index < count % len(names) else 0)
        block = blocks[name]
        polys = block.GetPolys()
        triangles = extract.triangulate_fan(
            vtk_to_numpy(polys.GetOffsetsArray()),  # type: ignore[no-untyped-call]
            vtk_to_numpy(polys.GetConnectivityArray()),  # type: ignore[no-untyped-call]
        )
        positions = vtk_to_numpy(block.GetPoints().GetData())  # type: ignore[no-untyped-call]
        out.append(surface_seeds(positions, triangles, share))
    return np.concatenate(out)


def open_volume(
    case_dir: Path, *, patches: bool, time: float | None, engine: str | None
) -> tuple[Any, float]:
    """The case's reader at `time` (the latest when None) with cell-to-point values; the time."""
    from stokes_agent import extract

    fields = extract.fields_for(engine)
    reader = extract.open_case(case_dir, patches=patches, internal=True, fields=fields)
    reader.CreateCellToPointOn()
    used = extract.latest_time(reader) if time is None else time
    reader.UpdateTimeStep(used)
    reader.Update()
    return reader, float(used)


def trace_case(
    case_dir: Path,
    seeds: np.ndarray | None,
    *,
    time: float | None = None,
    engine: str | None = None,
    seed_patches: list[str] | None = None,
    seed_count: int = 50,
    **options: Any,
) -> dict[str, Any]:
    """Trace through the internal mesh of the solved case in `case_dir`.

    The seeds are `seeds` (points, metres), or `seed_count` points spread over the boundary patches
    named in `seed_patches` (the inlets: where the flow enters the volume). Either way they are
    moved into the fluid first (`into_domain`), so a seed on a face still starts a line. `seeds` in
    the answer is how many seeds were left to start from.
    """
    from stokes_agent import extract

    reader, used = open_volume(case_dir, patches=bool(seed_patches), time=time, engine=engine)
    mesh = extract._internal_mesh(reader)
    if mesh is None:
        raise TraceInvalid("the case has no internal mesh to trace through")
    if seed_patches:
        found = patch_seed_points(reader, list(seed_patches), seed_count)
        start = into_domain(mesh, found, inset=True)
    else:
        if seeds is None:
            raise TraceInvalid("no seeds: give points or the patches to seed from")
        given = np.asarray(seeds, dtype=float)
        _check(given, str(options.get("direction", "forward")), options.get("max_length"))
        start = into_domain(mesh, given)
    answer = trace_mesh(mesh, start, **options) if start.shape[0] else _answer([], [], [0], [])
    answer["time"] = used
    answer["seeds"] = int(start.shape[0])
    return answer
