"""Fast mode on the GPU (ADR-0047 decision 9): the compiled field classifies cells as numpy does,
and the lattice reproduces a published laminar result and stays sound in a turbulent preview.

``gpu`` tests: skipped without a hardware adapter (``tests/conftest.py``).
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from stokes.fast.case import FastCase
from stokes.fast.ports import INLET, OUTLET, port_from_triangles
from stokes.fast.runner import FastRunner
from stokes.implicit.features import box
from stokes.implicit.graph import to_text

pytestmark = pytest.mark.gpu

#: Shah and London's fully developed laminar flow in a square duct: Fanning f times Reynolds number.
SQUARE_DUCT_FRE = 14.227


def _square(x: float, side: float) -> np.ndarray:
    p = [(x, 0.0, 0.0), (x, side, 0.0), (x, side, side), (x, 0.0, side)]
    return np.array([[p[0], p[1], p[2]], [p[0], p[2], p[3]]])


def _duct(side_mm: float, length_mm: float, speed: float, nu: float = 1e-6) -> FastCase:
    """A square duct along x from 0, inlet at x = 0 and outlet at its end."""
    text = to_text(
        box((length_mm, side_mm, side_mm), (length_mm / 2, side_mm / 2, side_mm / 2)).field()
    )
    side, length = side_mm * 1e-3, length_mm * 1e-3
    inlet = port_from_triangles(
        _square(0.0, side), "inlet", INLET, (-1, 0, 0), velocity=(speed, 0, 0)
    )
    outlet = port_from_triangles(_square(length, side), "outlet", OUTLET, (1, 0, 0))
    return FastCase(text, (0.0, 0.0, 0.0), (length, side, side), (inlet, outlet), nu)


def _run(case: FastCase, steps: int, fidelity: int = 2, axis: int = 1) -> tuple[FastRunner, dict]:  # type: ignore[type-arg]
    runner = FastRunner(lambda *_a, **_k: None)
    runner.configure(axis=axis)
    runner.start(case, fidelity=fidelity)
    deadline = time.monotonic() + 180
    status: dict = {}  # type: ignore[type-arg]
    while time.monotonic() < deadline:
        time.sleep(0.5)
        status = runner.status()
        assert status["state"] not in ("failed", "stopped"), status
        if status["frame"]["step"] >= steps:
            break
    return runner, status


def test_the_laminar_square_duct_gives_shah_and_londons_friction() -> None:
    """Re 50 in a 10 mm duct 120 mm long: the pressure gradient past the entrance (40 to 110 mm)
    gives fRe within 1 % of 14.227 (measured -0.05 %; +1.6 % before the incompressible
    equilibrium), the inlet delivers its area times its speed, and what leaves is what entered."""
    speed, side = 0.005, 0.01
    runner, status = _run(_duct(10.0, 120.0, speed), steps=40_000)
    try:
        frame = runner.frame()
        assert not status["les"]
        assert frame.guard == 0 and not frame.unstable
        x = frame.positions[:, 0]
        window = (x > 0.04) & (x < 0.11)
        columns = np.unique(np.round(x[window], 7))
        means = [frame.values["p"][window][np.isclose(x[window], c)].mean() for c in columns]
        gradient = np.polyfit(columns, means, 1)[0]
        reynolds = speed * side / 1e-6
        f_re = -gradient * side / (2 * speed**2) * reynolds
        assert f_re == pytest.approx(SQUARE_DUCT_FRE, rel=0.01)
        assert frame.inlet_speed == pytest.approx(speed, rel=0.005)
        assert frame.flow_in == pytest.approx(speed * side * side, rel=0.005)
        assert abs(frame.imbalance) < 0.005
        assert frame.dp > 0.0
        # The fully developed peak is 2.096 times the mean (Shah and London).
        assert float(frame.values["U"][x > 0.1].max()) == pytest.approx(2.096 * speed, rel=0.01)
        assert status["grid"]["fluid_cells"] == 32 * 32 * 384
    finally:
        runner.stop()


def test_a_turbulent_preview_holds_its_inflow_and_its_pressure_drop() -> None:
    """Re 1000 in a 20 mm duct, 32 cells across (cell Reynolds number 31): the LES keeps it stable,
    the inlet delivers its speed, and the pressure falls from inlet to outlet."""
    runner, status = _run(_duct(20.0, 60.0, 0.05), steps=60_000)
    try:
        frame = runner.frame()
        assert status["les"] and status["cell_reynolds"] > 8
        assert not frame.unstable and frame.guard == 0
        assert frame.inlet_speed == pytest.approx(0.05, rel=0.02)
        assert frame.dp > 0.0
        # A smooth field: the two-relaxation-time collision left cell-to-cell noise of three to four
        # times the inlet speed behind the inlet (measured 2026-11-22); the regularised one, 1.44.
        assert float(frame.values["U"].max()) < 1.8 * 0.05
        assert abs(frame.imbalance) < 0.03
    finally:
        runner.stop()


def test_a_slow_viscous_flow_keeps_its_flow_and_its_friction() -> None:
    """Re 0.5 in the same duct (a cell Reynolds number of 0.016, a fluid a hundred times as viscous
    as water): fRe within 1.5 % (measured -0.07 %). At the default lattice speed tau was 10.1, the
    density hit the guard's cap and a fifth of the flow got through."""
    speed, side, nu = 0.005, 0.01, 1e-4
    runner, status = _run(_duct(10.0, 120.0, speed, nu=nu), steps=14_000)
    try:
        frame = runner.frame()
        assert status["tau"] == pytest.approx(1.0)
        assert frame.guard == 0 and not frame.unstable and not frame.restrictive
        x = frame.positions[:, 0]
        window = (x > 0.04) & (x < 0.11)
        columns = np.unique(np.round(x[window], 7))
        means = [frame.values["p"][window][np.isclose(x[window], c)].mean() for c in columns]
        gradient = np.polyfit(columns, means, 1)[0]
        f_re = -gradient * side / (2 * speed**2) * (speed * side / nu)
        assert f_re == pytest.approx(SQUARE_DUCT_FRE, rel=0.015)
        assert abs(frame.imbalance) < 0.005
    finally:
        runner.stop()


def test_a_duct_at_an_angle_to_the_lattice_carries_its_inlets_flow() -> None:
    """The square duct turned 30 degrees about z, so its walls and both ports cut the cells at an
    angle: the developed peak is within 3 % of 2.096 times the inlet speed (measured +0.9 %), which
    is the flow the inlet delivers, and the outlet's reading agrees within 5 %. A gain from the
    cells' count alone delivered a quarter less."""
    from stokes.implicit.features import transformed
    from stokes.implicit.frame import rotation

    speed, side, length = 0.005, 0.01, 0.08
    motion = rotation((0.0, 0.0, 1.0), math.radians(30.0))  # world to the duct's own axes
    body = transformed(box((80.0, 10.0, 10.0), (40.0, 5.0, 5.0)), motion)
    back = motion.inverse()
    turn = np.array(back.linear)

    def placed(triangles: np.ndarray) -> np.ndarray:
        return np.asarray(triangles @ turn.T)

    def turned(vector: tuple[float, float, float]) -> tuple[float, float, float]:
        out = turn @ np.array(vector)
        return (float(out[0]), float(out[1]), float(out[2]))

    inlet = port_from_triangles(
        placed(_square(0.0, side)),
        "inlet",
        INLET,
        turned((-1, 0, 0)),
        velocity=turned((speed, 0, 0)),
    )
    outlet = port_from_triangles(placed(_square(length, side)), "outlet", OUTLET, turned((1, 0, 0)))
    low, high = body.bounded()
    case = FastCase(
        to_text(body.field()),
        (low[0] * 1e-3, low[1] * 1e-3, low[2] * 1e-3),
        (high[0] * 1e-3, high[1] * 1e-3, high[2] * 1e-3),
        (inlet, outlet),
        1e-6,
    )
    runner, _status = _run(case, steps=30_000, axis=2)
    try:
        frame = runner.frame()
        assert frame.guard == 0 and not frame.unstable
        assert frame.flow_in == pytest.approx(speed * side * side, rel=0.005)
        assert abs(frame.imbalance) < 0.05
        along = frame.positions @ np.array(turned((1, 0, 0)))  # the distance down the duct
        developed = frame.values["U"][along > 0.06]
        assert float(developed.max()) == pytest.approx(2.096 * speed, rel=0.03)
    finally:
        runner.stop()


_BINARY = {
    "add": np.add,
    "sub": np.subtract,
    "mul": np.multiply,
    "max": np.maximum,
    "min": np.minimum,
    "mod": lambda a, b: a - b * np.floor(a / b),
}
_UNARY = {"sin": np.sin, "cos": np.cos, "abs": np.abs, "neg": np.negative}


def _evaluate_text(text: str, points: np.ndarray) -> np.ndarray:
    """The field at points (mm), straight from the text, as the numpy evaluator reads it."""
    values: dict[str, np.ndarray] = {}
    leaves = {"var-x": 0, "var-y": 1, "var-z": 2}
    last = ""
    for line in text.splitlines():
        name, op, *args = line.split()
        if op in leaves:
            values[name] = points[:, leaves[op]]
        elif op == "const":
            values[name] = np.full(len(points), float(args[0]))
        elif op in _UNARY:
            values[name] = _UNARY[op](values[args[0]])
        else:
            values[name] = _BINARY[op](values[args[0]], values[args[1]])
        last = name
    return values[last]


def test_the_compiled_field_classifies_cells_as_numpy_does() -> None:
    """A wavy, patterned field (mod, sin, cos) inside a box: the GPU's fluid cells are numpy's,
    except where the field is within f32 round-off of zero."""
    from stokes.fast.device import Lattice, open_device, params_bytes
    from stokes.fast.grid import grid_for
    from stokes.fast.lattice import scaling_for
    from stokes.fast.ports import pack_ports

    text = "\n".join(
        [
            "_0 var-x",
            "_1 const 4.0",
            "_2 mod _0 _1",
            "_3 const 2.0",
            "_4 sub _2 _3",
            "_5 abs _4",
            "_6 var-y",
            "_7 sin _6",
            "_8 var-z",
            "_9 cos _8",
            "_10 mul _7 _9",
            "_11 add _5 _10",
            "_12 const 1.2",
            "_13 sub _11 _12",
            "_14 abs _0",
            "_15 const 9.0",
            "_16 sub _14 _15",
            "_17 max _13 _16",
        ]
    )
    low, high = (-0.009, -0.006, -0.006), (0.009, 0.006, 0.006)
    grid = grid_for(low, high, fidelity=3)
    device, _info = open_device()
    lattice = Lattice(device, grid)
    try:
        table, masks = pack_ports([], 1.0, {})
        lattice.load(text, table, masks)
        scaling = scaling_for(grid.dx, 0.01, 1e-6)
        lattice.classify(params_bytes(grid, scaling, 1, 0, init=True))
        fluid_gpu = (lattice.read_flags() & 0xFF) == 0
        i, j, k = np.meshgrid(
            np.arange(grid.nx), np.arange(grid.ny), np.arange(grid.nz), indexing="ij"
        )
        centres = np.stack([grid.origin[a] + ([i, j, k][a] + 0.5) * grid.dx for a in range(3)], -1)
        centres = centres.transpose(2, 1, 0, 3).reshape(-1, 3) * 1e3  # cell order x fastest; mm
        phi = _evaluate_text(text, centres)
        edge = np.zeros_like(fluid_gpu)
        ids = np.arange(grid.cells)
        ii, jj, kk = ids % grid.nx, (ids // grid.nx) % grid.ny, ids // (grid.nx * grid.ny)
        edge = (
            (ii == 0)
            | (jj == 0)
            | (kk == 0)
            | (ii == grid.nx - 1)
            | (jj == grid.ny - 1)
            | (kk == grid.nz - 1)
        )
        fluid_numpy = (phi < 0.0) & ~edge
        settled = np.abs(phi) > 1e-4
        assert fluid_numpy.sum() > 1000
        assert np.array_equal(fluid_gpu[settled], fluid_numpy[settled])
        assert math.isclose(fluid_gpu.sum(), fluid_numpy.sum(), rel_tol=1e-3)
    finally:
        lattice.destroy()
        device.destroy()


def test_a_laminar_lattice_that_needs_the_guard_hands_over_to_the_les(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Started laminar where the lattice is not stable (the square duct at Re 500, cell Reynolds
    number 15.6, measured to blow up laminar), the run sees the guard acting and switches the LES on
    by itself, then holds the inlet's speed with no guard. The laminar limit is raised so the run
    starts laminar: in the app the same hand-over caught a pin bank whose inlet looked laminar while
    the flow between the pins was not (2026-11-22)."""
    from stokes.fast import lattice

    monkeypatch.setattr(lattice, "RE_CELL_LAMINAR", 1000.0)
    runner, status = _run(_duct(10.0, 200.0, 0.05), steps=40_000)
    try:
        frame = runner.frame()
        assert status["les"], status
        assert status["cell_reynolds"] == pytest.approx(15.625)
        assert frame.guard == 0 and not frame.unstable
        assert frame.inlet_speed == pytest.approx(0.05, rel=0.02)
        assert frame.dp > 0.0
        assert float(frame.values["U"].max()) < 4 * 0.05
    finally:
        runner.stop()
