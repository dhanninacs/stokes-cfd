"""Streamlines that follow walls: a tracer for passages a few cells wide (lattices, fin gaps).

`vtkStreamTracer` integrates the point-interpolated velocity and stops a line the moment it leaves
the mesh. In a channel two or three cells across that is almost at once: nearly every point is a
wall point, the interpolated velocity points through the wall beside it, and the line ends at the
wall long before an outlet. Measured on a gyroid plate exchanger (0.8 mm cells, 1.23 M cells): the
solved hot stream runs the whole 40 mm core at a mean 0.1 m/s along it, yet the median
`vtkStreamTracer` line from the inlet ran 24 mm and stopped 4 mm into the core, whatever the
integrator, step unit or step size, on the region's own mesh, the whole mesh and a tetrahedralised
copy alike. Walked with this module, all 50 lines from the hot inlet reached the hot outlet (median
118 mm) and 46 of 50 from the cold inlet reached the cold outlet.

This walks each line through the **fluid cells only** (a cell with a velocity: the solid of a
conjugate case is still), with the velocity interpolated from those cells alone, so a wall point
carries the velocity of the fluid beside it rather than the solid's zero. A step that would leave
the fluid is turned along the wall: the wall's normal is taken from the nearest point of the fluid's
surface, the step loses its part along it, and the line goes on just inside (or, failing every
nudge, from the middle of the cell it is in). It ends where it reaches an **open** boundary (an
inlet or outlet patch, `left_domain`), where the flow has slowed to `terminal_fraction` of the
case's largest speed (`stagnation`), where it makes no headway (`wall`), or at `max_length` or
`max_steps`.

The answer is `trace.trace_mesh`'s: points, speeds, offsets, reasons and the carried scalars.
Import rules: numpy at module level; VTK inside the functions.
"""

from __future__ import annotations

from typing import Any

import numpy as np

#: A step is this fraction of the size of the cell it starts in.
STEP_CELLS = 0.3
#: A line whose last `WINDOW` steps moved it less than `HEADWAY_CELLS` cell sizes is going nowhere.
WINDOW = 200
HEADWAY_CELLS = 0.5
#: A line that leaves the fluid within this many cell sizes of an open patch has left through it.
OPEN_CELLS = 1.5

#: VTK's reason codes, which `trace.REASONS` names.
LEFT, LENGTH, STEPS, STAGNATION, WALL = 1, 4, 5, 6, 7


def _fluid(mesh: Any) -> Any:
    """The cells with a velocity, as their own dataset, with point values from them alone."""
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonCore import vtkIdList
    from vtkmodules.vtkFiltersCore import vtkCellDataToPointData, vtkExtractCells

    cell_u = mesh.GetCellData().GetArray("U")
    if cell_u is None:
        raise ValueError("the mesh has no cell velocity U")
    speeds = np.linalg.norm(vtk_to_numpy(cell_u), axis=1)
    moving = np.flatnonzero(speeds > 0.0)
    ids = vtkIdList()
    ids.SetNumberOfIds(int(moving.size))
    for index, cell in enumerate(moving):
        ids.SetId(index, int(cell))
    extract = vtkExtractCells()
    extract.SetInputData(mesh)
    extract.SetCellList(ids)
    extract.Update()
    fluid = extract.GetOutput()
    fluid.GetPointData().Initialize()
    to_points = vtkCellDataToPointData()
    to_points.SetInputData(fluid)
    to_points.PassCellDataOn()
    to_points.Update()
    return to_points.GetOutput()


def _cell_sizes(mesh: Any) -> np.ndarray:
    """Each cell's size: the cube root of its volume (a thousandth of the box where it has none)."""
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkFiltersVerdict import vtkCellSizeFilter

    sizes = vtkCellSizeFilter()
    sizes.SetInputData(mesh)
    sizes.ComputeVertexCountOff()
    sizes.ComputeLengthOff()
    sizes.ComputeAreaOff()
    sizes.ComputeVolumeOn()
    sizes.Update()
    volume = vtk_to_numpy(sizes.GetOutput().GetCellData().GetArray("Volume"))
    out = np.cbrt(np.abs(np.asarray(volume, dtype=float)))
    small = out <= 0.0
    if small.any():
        span = np.asarray(mesh.GetBounds(), dtype=float)
        out[small] = 1e-3 * float(np.linalg.norm(span[1::2] - span[0::2]))
    return out


def _cell_centres(mesh: Any) -> np.ndarray:
    """Each cell's centre, as `vtkCellCenters` places it (inside for a convex cell)."""
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkFiltersCore import vtkCellCenters

    centres = vtkCellCenters()
    centres.SetInputData(mesh)
    centres.VertexCellsOff()
    centres.Update()
    points = centres.GetOutput().GetPoints().GetData()
    return np.asarray(vtk_to_numpy(points), dtype=float)


class _Walker:
    """The fluid of one mesh, its locators and the walk of one line."""

    def __init__(self, mesh: Any, open_points: np.ndarray | None) -> None:
        from vtkmodules.util.numpy_support import vtk_to_numpy
        from vtkmodules.vtkCommonDataModel import vtkGenericCell, vtkStaticCellLocator
        from vtkmodules.vtkFiltersGeometry import vtkGeometryFilter

        from stokes_agent import trace

        self.fluid = _fluid(mesh)
        if self.fluid.GetNumberOfCells() == 0:
            raise trace.TraceInvalid("the velocity is zero everywhere; there is nothing to trace")
        data = self.fluid.GetPointData()
        self.point_u = np.asarray(vtk_to_numpy(data.GetArray("U")), dtype=float)
        cell_data = self.fluid.GetCellData().GetArray("U")
        self.cell_u = np.asarray(vtk_to_numpy(cell_data), dtype=float)
        self.top = float(np.linalg.norm(self.cell_u, axis=1).max())
        self.carried = {
            name: np.asarray(vtk_to_numpy(array), dtype=float)
            for name in trace.CARRIED_FIELDS
            if (array := data.GetArray(name)) is not None and array.GetNumberOfComponents() == 1
        }
        self.sizes = _cell_sizes(self.fluid)
        self.centres = _cell_centres(self.fluid)
        self.cells = vtkStaticCellLocator()
        self.cells.SetDataSet(self.fluid)
        self.cells.BuildLocator()
        skin = vtkGeometryFilter()
        skin.SetInputData(self.fluid)
        skin.Update()
        self.skin = skin.GetOutput()
        self.walls = vtkStaticCellLocator()
        self.walls.SetDataSet(self.skin)
        self.walls.BuildLocator()
        self.work = vtkGenericCell()
        self.pcoords = [0.0, 0.0, 0.0]
        self.weights = [0.0] * 512
        self.opening: Any = None
        self.open_xyz = np.zeros((0, 3))
        if open_points is not None and len(open_points):
            self._open(np.asarray(open_points, dtype=float).reshape(-1, 3))

    def _open(self, xyz: np.ndarray) -> None:
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkStaticPointLocator

        held = vtkPoints()
        for point in xyz:
            held.InsertNextPoint(float(point[0]), float(point[1]), float(point[2]))
        cloud = vtkPolyData()
        cloud.SetPoints(held)
        self.opening = vtkStaticPointLocator()
        self.opening.SetDataSet(cloud)
        self.opening.BuildLocator()
        self.open_xyz = xyz

    def locate(self, point: np.ndarray) -> int:
        """The fluid cell holding `point`, or -1; leaves its point ids and weights in `work`."""
        at = [float(v) for v in point]
        return int(self.cells.FindCell(at, 0.0, self.work, self.pcoords, self.weights))

    def sample(self, point: np.ndarray, cell: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The velocity at `point` in `cell`, and the point ids and weights it came from."""
        found = self.locate(point)
        ids = self.work.GetPointIds()
        count = ids.GetNumberOfIds()
        if found != cell or count == 0:
            return self.cell_u[cell], np.zeros(0, dtype=np.int64), np.zeros(0)
        index = np.asarray([ids.GetId(k) for k in range(count)], dtype=np.int64)
        weights = np.asarray(self.weights[:count], dtype=float)
        velocity = weights @ self.point_u[index]
        # A point value pulled down by a corner of the cell is not the flow in it.
        if float(np.linalg.norm(velocity)) < 0.25 * float(np.linalg.norm(self.cell_u[cell])):
            velocity = self.cell_u[cell]
        return velocity, index, weights

    def near_open(self, point: np.ndarray, size: float) -> bool:
        if self.opening is None:
            return False
        nearest = int(self.opening.FindClosestPoint([float(v) for v in point]))
        if nearest < 0:
            return False
        return float(np.linalg.norm(self.open_xyz[nearest] - point)) < OPEN_CELLS * size

    def wall_at(self, point: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        """The nearest point of the fluid's surface to `point`, and the outward normal there."""
        from vtkmodules.vtkCommonCore import reference

        closest = [0.0, 0.0, 0.0]
        cell_id, sub_id, dist2 = reference(-1), reference(0), reference(0.0)
        # The stub types the out-parameters as int and float; at run time they are references.
        self.walls.FindClosestPoint(  # type: ignore[call-overload]
            [float(v) for v in point], closest, self.work, cell_id, sub_id, dist2
        )
        if int(cell_id) < 0:
            return None
        foot = np.asarray(closest, dtype=float)
        normal = point - foot
        length = float(np.linalg.norm(normal))
        return (foot, normal / length) if length > 0.0 else None

    def slide(
        self, point: np.ndarray, cell: int, ahead: np.ndarray, velocity: np.ndarray, step: float
    ) -> tuple[np.ndarray, int]:
        """Where a step that left the fluid goes instead: along the wall, else the cell's middle."""
        tries: list[np.ndarray] = []
        wall = self.wall_at(ahead)
        if wall is not None:
            foot, normal = wall
            along = velocity - float(velocity @ normal) * normal
            speed = float(np.linalg.norm(velocity))
            if float(np.linalg.norm(along)) > 1e-3 * speed:
                tangent = along / float(np.linalg.norm(along))
                tries += [point + tangent * step, foot - normal * 0.3 * step + tangent * 0.5 * step]
                tries.append(point + tangent * 0.3 * step)
            tries.append(foot - normal * 0.3 * step)
        # Last, the middle of the cell the line is in: always inside, so a wall never ends a line by
        # itself; one that keeps coming back here makes no headway and stops (`WINDOW`).
        tries.append(self.centres[cell])
        for candidate in tries:
            found = self.locate(candidate)
            if found >= 0:
                return candidate, found
        return point, -1

    def walk(
        self,
        seed: np.ndarray,
        sign: float,
        *,
        step_cells: float,
        max_length: float,
        max_steps: int,
        terminal: float,
    ) -> dict[str, Any] | None:
        """One line from `seed`, forward (`sign` 1) or backward (-1)."""
        point = np.asarray(seed, dtype=float)
        cell = self.locate(point)
        if cell < 0:
            return None
        line = _Line(self.carried)
        velocity, index, weights = self.sample(point, cell)
        line.add(point, velocity, index, weights)
        travelled, reason = 0.0, STEPS
        for _ in range(int(max_steps)):
            velocity = velocity * sign
            speed = float(np.linalg.norm(velocity))
            if speed <= terminal:
                reason = STAGNATION
                break
            size = float(self.sizes[cell])
            step = float(step_cells) * size
            ahead = point + velocity / speed * step
            next_cell = self.locate(ahead)
            if next_cell < 0:
                if self.near_open(ahead, size):
                    reason = LEFT
                    break
                ahead, next_cell = self.slide(point, cell, ahead, velocity, step)
                if next_cell < 0:
                    reason = WALL
                    break
            travelled += float(np.linalg.norm(ahead - point))
            point, cell = ahead, next_cell
            velocity, index, weights = self.sample(point, cell)
            line.add(point, velocity, index, weights)
            if line.stalled(HEADWAY_CELLS * size):
                reason = WALL
                break
            if travelled >= max_length:
                reason = LENGTH
                break
        return line.finish(reason)


class _Line:
    """One line's points, speeds and carried values as it is walked."""

    def __init__(self, carried: dict[str, np.ndarray]) -> None:
        self.carried = carried
        self.points: list[np.ndarray] = []
        self.speeds: list[float] = []
        self.values: dict[str, list[float]] = {name: [] for name in carried}

    def add(
        self, point: np.ndarray, velocity: np.ndarray, index: np.ndarray, weights: np.ndarray
    ) -> None:
        self.points.append(np.asarray(point, dtype=float).copy())
        self.speeds.append(float(np.linalg.norm(velocity)))
        for name, column in self.carried.items():
            self.values[name].append(float(weights @ column[index]) if index.size else float("nan"))

    def stalled(self, headway: float) -> bool:
        """Whether the last `WINDOW` steps moved the line less than `headway`."""
        if len(self.points) <= WINDOW:
            return False
        return float(np.linalg.norm(self.points[-1] - self.points[-1 - WINDOW])) < headway

    def finish(self, reason: int) -> dict[str, Any] | None:
        if len(self.points) < 2:
            return None
        return {
            "points": np.asarray(self.points, dtype=float),
            "speeds": np.asarray(self.speeds, dtype=float),
            "values": {name: np.asarray(v, dtype=float) for name, v in self.values.items()},
            "reason": reason,
        }


def _joined(back: dict[str, Any], ahead: dict[str, Any]) -> dict[str, Any]:
    """A backward half reversed and a forward half after it, as one line."""

    def join(first: np.ndarray, second: np.ndarray) -> np.ndarray:
        return np.concatenate([first[::-1], second[1:]])

    return {
        "points": join(back["points"], ahead["points"]),
        "speeds": join(back["speeds"], ahead["speeds"]),
        "values": {
            name: join(back["values"][name], ahead["values"][name]) for name in back["values"]
        },
        "reason": ahead["reason"],
    }


def walk_mesh(
    mesh: Any,
    seeds: np.ndarray,
    *,
    open_points: np.ndarray | None = None,
    direction: str = "forward",
    max_length: float | None = None,
    step_cells: float = STEP_CELLS,
    max_steps: int = 20000,
    terminal_fraction: float = 1e-4,
    max_points: int = 1000,
    **_ignored: Any,
) -> dict[str, Any]:
    """Lines from `seeds` through the fluid of `mesh`, sliding along walls (module docstring).

    `mesh` holds `U` (and the carried scalars) as cell data; `open_points` are points on the open
    patches (inlets and outlets), where a line that leaves the fluid has left through an opening.
    `direction` "both" walks backward and forward from each seed and joins the two halves. The
    tracer's other options (`max_step_cells` and the like) do not apply and are ignored.
    """
    from stokes_agent import trace

    walker = _Walker(mesh, open_points)
    if max_length is None:
        span = np.asarray(walker.fluid.GetBounds(), dtype=float)
        max_length = 3.0 * float(np.linalg.norm(span[1::2] - span[0::2]))
    terminal = float(terminal_fraction) * walker.top

    def go(seed: np.ndarray, sign: float) -> dict[str, Any] | None:
        return walker.walk(
            seed,
            sign,
            step_cells=float(step_cells),
            max_length=float(max_length),
            max_steps=int(max_steps),
            terminal=terminal,
        )

    lines: list[dict[str, Any]] = []
    for index, seed in enumerate(np.asarray(seeds, dtype=float).reshape(-1, 3)):
        if direction == "both":
            back = go(seed, -1.0)
            ahead = go(seed, 1.0)
            found = _joined(back, ahead) if back and ahead else (back or ahead)
        else:
            found = go(seed, -1.0 if direction == "backward" else 1.0)
        if found is not None:
            found["seed"] = index
            lines.append(found)
    return trace._pack(lines, int(max_points))
