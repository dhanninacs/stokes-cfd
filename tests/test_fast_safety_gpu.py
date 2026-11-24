"""Fast mode's safety checks on the GPU (ADR-0047 addendum 5): a union whose parts share a face is
one fluid at every fidelity, a fluid in pieces is refused, and a run that runs away fails rather
than report its numbers.

``gpu`` tests: skipped without a hardware adapter (``tests/conftest.py``).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from stokes.fast.case import FastCase
from stokes.fast.grid import CELLS_ACROSS, grid_for
from stokes.fast.ports import INLET, OUTLET, port_from_triangles
from stokes.fast.runner import FastRunner
from stokes.fast.zones import connected
from stokes.implicit.features import box, difference, union
from stokes.implicit.graph import to_text

pytestmark = pytest.mark.gpu

LENGTH, SIDE = 12.0, 6.0  # mm


def _square(x: float, side: float) -> np.ndarray:
    p = [(x, 0.0, 0.0), (x, side, 0.0), (x, side, side), (x, 0.0, side)]
    return np.array([[p[0], p[1], p[2]], [p[0], p[2], p[3]]])


def _seam(fidelity: int) -> float:
    """Where the parts meet (mm): on a plane of cell centres of the grid at ``fidelity`` (the
    grid over this box has a face, not a centre, at every multiple of the cell)."""
    dx = SIDE / CELLS_ACROSS[fidelity]
    return (round(9.0 / dx) + 0.5) * dx


def _union(seam: float, gap: float = 0.0, speed: float = 0.01) -> FastCase:
    """A box 0..seam and a box seam + gap..12 mm along x, 6 mm square: a core and a plenum."""
    core = box((seam, SIDE, SIDE), (seam / 2, SIDE / 2, SIDE / 2))
    rest = LENGTH - seam - gap
    plenum = box((rest, SIDE, SIDE), (LENGTH - rest / 2, SIDE / 2, SIDE / 2))
    text = to_text(union([core, plenum]).field())
    side, length = SIDE * 1e-3, LENGTH * 1e-3
    inlet = port_from_triangles(
        _square(0.0, side), "inlet", INLET, (-1, 0, 0), velocity=(speed, 0, 0)
    )
    outlet = port_from_triangles(_square(length, side), "outlet", OUTLET, (1, 0, 0))
    return FastCase(text, (0.0, 0.0, 0.0), (length, side, side), (inlet, outlet), 1e-6)


def _wait(runner: FastRunner, done: Any, seconds: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        time.sleep(0.25)
        status = runner.status()
        if done(status):
            return status
    raise AssertionError(f"the run did not get there in {seconds} s: {status}")


@pytest.mark.parametrize("fidelity", [1, 2, 3, 4, 5])
def test_a_union_whose_parts_share_a_face_is_one_fluid(fidelity: int) -> None:
    """Cell centres exactly on the shared face (the field is zero there) were solid, and cut the
    fluid in two (measured on this box before the fix: two pieces at every fidelity)."""
    from stokes.fast.device import Lattice, open_device, params_bytes
    from stokes.fast.lattice import scaling_for
    from stokes.fast.ports import pack_ports

    case = _union(_seam(fidelity))
    grid = grid_for(case.low, case.high, fidelity)
    seam = _seam(fidelity) * 1e-3
    on_seam = np.isclose(grid.origin[0] + (np.arange(grid.nx) + 0.5) * grid.dx, seam, atol=1e-9)
    assert on_seam.sum() == 1  # the grid puts a plane of centres on the seam
    device, _info = open_device()
    lattice = Lattice(device, grid)
    try:
        table, masks = pack_ports([], 1.0, {})
        lattice.load(case.field_text, table, masks)
        lattice.classify(params_bytes(grid, scaling_for(grid.dx, 0.01, 1e-6), 1, 0, init=True))
        fluid = ((lattice.read_flags() & 0xFF) == 0).reshape(grid.nz, grid.ny, grid.nx)
        _labels, pieces = connected(fluid)
        assert pieces == 1
        across = CELLS_ACROSS[fidelity]
        assert fluid[:, :, on_seam].sum() == across * across  # the seam's plane is all fluid
        assert fluid.sum() == across * across * 2 * across
    finally:
        lattice.destroy()
        device.destroy()


@pytest.mark.parametrize("fidelity", [1, 2])
def test_a_run_through_a_shared_face_carries_its_flow(fidelity: int) -> None:
    """The run through the seam delivers its inflow at the outlet (it had none, and a pressure
    climbing without end, with the fluid in pieces)."""
    speed = 0.01
    runner = FastRunner(lambda *_a, **_k: None)
    runner.configure(axis=1)
    runner.start(_union(_seam(fidelity), speed=speed), fidelity=fidelity, warm_start=False)
    try:
        status = _wait(runner, lambda s: s["frame"]["step"] >= 20_000 or s["state"] == "failed")
        assert status["state"] == "running", status
        frame = status["frame"]
        flow = speed * (SIDE * 1e-3) ** 2
        assert frame["flow_out"] == pytest.approx(flow, rel=0.05)
        assert not frame["unstable"] and frame["dp"] > 0.0
    finally:
        runner.stop()


def test_a_fluid_in_pieces_is_refused_not_run() -> None:
    """Two boxes a millimetre apart: the inlet reaches no outlet, and the run fails at once with
    ``fast.invalid`` naming the fluid, instead of stepping a pressure up without end."""
    runner = FastRunner(lambda *_a, **_k: None)
    runner.start(_union(9.0, gap=1.0), fidelity=2, warm_start=False)
    try:
        status = _wait(runner, lambda s: s["state"] in ("failed", "stopped"), 60.0)
        assert status["state"] == "failed"
        error = status["error"]
        assert error["code"] == "fast.invalid"
        assert error["details"]["reason"] == "fast.disconnected"
        assert error["details"]["fluid"] == "fluid"
        assert "'fluid'" in error["message"]
        assert "'inlet' reaches no outlet and 'outlet' no inlet" in error["message"]
    finally:
        runner.stop()


def test_a_lattice_that_runs_away_fails_and_never_reports_valid_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A laminar lattice at a cell Reynolds number of 167 (tau 0.5007), with its hand-over to the
    LES switched off, runs away without going NaN: the guard holds the flow, the outflow runs 20 to
    50 % off the inflow, and it kept reporting so (measured before the check). Now its frames say
    ``unstable``, lowering the lattice speed is tried twice, and the run fails with
    ``fast.unstable``."""
    from stokes.fast import lattice
    from stokes.fast import runner as runner_module

    monkeypatch.setattr(lattice, "RE_CELL_LAMINAR", 1e9)
    monkeypatch.setattr(runner_module, "GUARD_FRAMES", 10**9)
    frames: list[dict[str, Any]] = []
    warnings: list[str] = []

    def emit(event: str, **payload: Any) -> None:
        if event == "fast.frame":
            frames.append(payload)
        elif event == "fast.warning":
            warnings.append(payload["code"])

    side = 10.0
    inlet = port_from_triangles(
        _square(0.0, 0.01), "inlet", INLET, (-1, 0, 0), velocity=(0.4, 0, 0)
    )
    outlet = port_from_triangles(_square(0.06, 0.01), "outlet", OUTLET, (1, 0, 0))
    text = to_text(box((60.0, side, side), (30.0, side / 2, side / 2)).field())
    case = FastCase(text, (0.0, 0.0, 0.0), (0.06, 0.01, 0.01), (inlet, outlet), 1e-6)
    runner = FastRunner(emit)
    runner.configure(axis=1)
    runner.start(case, fidelity=1, warm_start=False)
    try:
        status = _wait(runner, lambda s: s["state"] in ("failed", "stopped"), 150.0)
        assert status["state"] == "failed", status
        assert status["error"]["code"] == "fast.unstable"
        assert status["error"]["details"]["restarts"] == 2
        assert warnings.count("fast.unstable") == 2
        assert status["frame"]["unstable"] and status["frame"]["instability"]
        assert not any(f["steady"] for f in frames)
        assert any(f["unstable"] for f in frames)
    finally:
        runner.stop()


def test_a_laminar_orifice_filling_with_pressure_reaches_steady() -> None:
    """A 60 x 10 x 10 mm duct with a plate at x = 50 mm and a 2 mm hole, nu 1e-4, 0.02 m/s at
    fidelity 3 (a hole Reynolds number of about 0.4; the guard never acts). Its outflow climbs
    smoothly from 0.4 to 1 of the inflow while the pressure builds; the first runaway check took
    that for an imbalance and failed the run after two restarts (2026-11-24). It must reach steady,
    balanced, with no frame marked unstable and no restart (before: steady at 46 s, dp 682 Pa)."""
    frames: list[dict[str, Any]] = []
    warnings: list[str] = []

    def emit(event: str, **payload: Any) -> None:
        if event == "fast.frame":
            frames.append(payload)
        elif event == "fast.warning":
            warnings.append(payload["code"])

    length, side = 60.0, 10.0
    duct = box((length, side, side), (length / 2, side / 2, side / 2))
    plate = box((1.0, side + 2, side + 2), (50.0, side / 2, side / 2))
    hole = box((3.0, 2.0, 2.0), (50.0, side / 2, side / 2))
    text = to_text(difference(duct, [difference(plate, [hole])]).field())
    inlet = port_from_triangles(
        _square(0.0, side * 1e-3), "inlet", INLET, (-1, 0, 0), velocity=(0.02, 0, 0)
    )
    outlet = port_from_triangles(_square(length * 1e-3, side * 1e-3), "outlet", OUTLET, (1, 0, 0))
    high = (length * 1e-3, side * 1e-3, side * 1e-3)
    case = FastCase(text, (0.0, 0.0, 0.0), high, (inlet, outlet), 1e-4)
    runner = FastRunner(emit)
    runner.configure(axis=1)
    runner.start(case, fidelity=3, warm_start=False)
    try:
        status = _wait(
            runner, lambda s: s["state"] in ("failed", "stopped") or s["frame"]["steady"], 150.0
        )
        assert status["state"] == "running", status.get("error")
        frame = status["frame"]
        assert frame["steady"], frame
        assert abs(frame["imbalance"]) < 0.02
        assert 600.0 < frame["dp_pa"] < 760.0  # 682 Pa on the code before the check
        assert "fast.unstable" not in warnings
        assert not any(f["unstable"] for f in frames)
    finally:
        runner.stop()
