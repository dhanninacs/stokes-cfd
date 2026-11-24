"""Fast mode, round 4 (ADR-0047 addendum 4), on the GPU: the closures that leave a laminar flow
alone, the warm start, and one population buffer in 32 and in 16 bits.

``gpu`` tests: skipped without a hardware adapter (``tests/conftest.py``).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from stokes.fast.case import FastCase
from stokes.fast.ports import INLET, OUTLET, port_from_triangles
from stokes.fast.runner import FastRunner
from stokes.implicit.features import box
from stokes.implicit.graph import to_text

pytestmark = pytest.mark.gpu

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


def _friction(frame: Any, side: float, speed: float, nu: float, low: float, high: float) -> float:
    """fRe from the pressure gradient between x = low and high."""
    x = frame.positions[:, 0]
    window = (x > low) & (x < high)
    columns = np.unique(np.round(x[window], 7))
    means = [frame.values["p"][window][np.isclose(x[window], c)].mean() for c in columns]
    gradient = np.polyfit(columns, means, 1)[0]
    return float(-gradient * side / (2 * speed**2) * (speed * side / nu))


def _wait(runner: FastRunner, done: Any, seconds: float = 180.0) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        time.sleep(0.25)
        status = runner.status()
        assert status["state"] not in ("failed", "stopped"), status
        if done(status):
            return status
    raise AssertionError(f"the run did not get there in {seconds} s: {status}")


def _run(case: FastCase, steps: int, **options: Any) -> tuple[FastRunner, dict[str, Any]]:
    fidelity = options.pop("fidelity", 2)
    start = {k: options.pop(k) for k in ("warm_start", "storage") if k in options}
    runner = FastRunner(lambda *_a, **_k: None)
    runner.configure(axis=1, **options)
    runner.start(case, fidelity=fidelity, **start)
    return runner, _wait(runner, lambda s: s["frame"]["step"] >= steps)


# ------------------------------------------------------------------------------ the closures


def test_wale_and_vreman_leave_a_laminar_duct_its_friction() -> None:
    """Re 300 in a 10 mm duct at 32 cells across (cell Reynolds number 9.4): laminar in truth, a
    turbulent preview to the lattice. Plain Smagorinsky adds an eddy viscosity to the laminar
    shear and reads the friction about 5 % high; WALE and Vreman add none (measured: within
    0.05 % of the same collision with no model at all)."""
    speed, side = 0.03, 0.01
    found: dict[str, float] = {}
    for closure in ("smagorinsky", "wale", "vreman"):
        runner, status = _run(_duct(10.0, 120.0, speed), steps=30_000, turbulence=closure)
        try:
            frame = runner.frame()
            assert status["les"] and status["closure"] == closure
            assert frame.guard == 0 and not frame.unstable
            found[closure] = _friction(frame, side, speed, 1e-6, 0.07, 0.11)
        finally:
            runner.stop()
    assert found["wale"] == pytest.approx(found["vreman"], rel=0.003)
    assert found["wale"] < 0.975 * found["smagorinsky"]


# ------------------------------------------------------------------------------ the warm start


def test_a_fine_run_settles_a_coarse_lattice_first_and_starts_from_it() -> None:
    """Asked for at fidelity 4, the duct first runs at fidelity 2 (``warming``, never called
    steady), then the fine lattice starts from that flow: its first frames already carry the
    inlet's flow and the duct's pressure drop, and it settles on Shah and London's friction."""
    speed, side = 0.002, 0.01
    runner = FastRunner(lambda *_a, **_k: None)
    runner.configure(axis=1)
    runner.start(_duct(10.0, 60.0, speed), fidelity=4)
    try:
        coarse = _wait(runner, lambda s: s.get("grid", {}).get("fidelity") == 2)
        assert coarse["warming"] and coarse["fidelity"] == 4 and coarse["warm_start"]
        assert not coarse["frame"]["steady"]
        fine = _wait(
            runner, lambda s: s.get("grid", {}).get("fidelity") == 4 and s["frame"]["step"] > 0
        )
        assert not fine["warming"] and fine["warmed_from"] == 2
        # The cells counted are this grid's or not yet counted, never the coarse grid's.
        assert fine["grid"]["fluid_cells"] in (0, 64 * 64 * 384)
        first = _wait(runner, lambda s: s["frame"]["geometry_version"] >= 2)["frame"]
        # Not from rest: a cold start's first frames have a hundredth of the flow (the ramp).
        assert first["flow_in"] == pytest.approx(speed * side * side, rel=0.02)
        assert first["flow_out"] == pytest.approx(speed * side * side, rel=0.05)
        done = _wait(runner, lambda s: s["frame"]["steady"], 240.0)
        frame = runner.frame()
        assert done["grid"]["fidelity"] == 4 and not frame.unstable and frame.guard == 0
        assert done["warmed_from"] is None and frame.warmed_from is None  # said until steady
        assert frame.dp == pytest.approx(first["dp"], rel=0.05)
        friction = _friction(frame, side, speed, 1e-6, 0.025, 0.05)
        assert friction == pytest.approx(SQUARE_DUCT_FRE, rel=0.01)
    finally:
        runner.stop()


def test_a_fidelity_raised_while_running_keeps_the_flow_unless_told_not_to() -> None:
    """Raising the fidelity builds a new lattice. With the warm start (the default) it takes the
    old one's flow; without it, it starts from rest and the inlets ramp again."""
    speed, side = 0.005, 0.01
    flows: dict[bool, float] = {}
    for warm in (True, False):
        runner = FastRunner(lambda *_a, **_k: None)
        runner.configure(axis=1)
        runner.start(_duct(10.0, 60.0, speed), fidelity=3, warm_start=warm)
        try:
            _wait(runner, lambda s: s["frame"]["step"] >= 12_000)
            runner.configure(fidelity=4)
            raised = _wait(
                runner,
                lambda s: (
                    s.get("grid", {}).get("fidelity") == 4 and s["frame"]["geometry_version"] >= 2
                ),
            )
            assert not raised["warming"]
            assert raised["frame"]["warmed_from"] == (3 if warm else None)
            flows[warm] = raised["frame"]["flow_out"]
        finally:
            runner.stop()
    assert flows[True] == pytest.approx(speed * side * side, rel=0.05)
    assert abs(flows[False]) < 0.5 * speed * side * side


# ------------------------------------------------------------------------------ one buffer, 16 bits


def test_sixteen_bit_storage_reads_the_ducts_friction_within_a_per_cent() -> None:
    """The square duct of ``test_fast_gpu`` with its populations in 16 bits (half the memory):
    fRe within 1 % of Shah and London's and of the 32-bit run's, the inlet's flow delivered, and
    what leaves is what entered."""
    speed, side = 0.005, 0.01
    runner, status = _run(_duct(10.0, 120.0, speed), steps=40_000, storage="f16")
    try:
        frame = runner.frame()
        assert status["storage"] == "f16" and status["adapter"]["f16"]
        assert not status["les"] and frame.guard == 0 and not frame.unstable
        friction = _friction(frame, side, speed, 1e-6, 0.04, 0.11)
        assert friction == pytest.approx(SQUARE_DUCT_FRE, rel=0.01)
        assert frame.flow_in == pytest.approx(speed * side * side, rel=0.005)
        assert abs(frame.imbalance) < 0.01
    finally:
        runner.stop()


def test_two_fluids_side_by_side_keep_their_own_flow_in_one_buffer() -> None:
    """Two ducts that share a face with no solid cell between them, flowing opposite ways. Each
    fluid's cells are walls to the other's, and under in-place streaming both sides of such a
    link compute: each keeps its own place for it (``stokes.fast.inplace``). Neither stream
    gains or loses flow, and the two read the same pressure drop."""
    from stokes.fast.case import Zone

    a, length, speed = 0.005, 0.04, 0.01

    def text(y: float) -> str:
        return to_text(
            box((length * 1e3, a * 1e3, a * 1e3), (length * 500, y * 1e3, a * 500)).field()
        )

    def face(x: float, y0: float) -> np.ndarray:
        p = [(x, y0, 0.0), (x, y0 + a, 0.0), (x, y0 + a, a), (x, y0, a)]
        return np.array([[p[0], p[1], p[2]], [p[0], p[2], p[3]]])

    ports = (
        port_from_triangles(face(0.0, 0.0), "a_in", INLET, (-1, 0, 0), velocity=(speed, 0, 0)),
        port_from_triangles(face(length, 0.0), "a_out", OUTLET, (1, 0, 0)),
        port_from_triangles(
            face(length, a), "b_in", INLET, (1, 0, 0), velocity=(-speed, 0, 0), zone=1
        ),
        port_from_triangles(face(0.0, a), "b_out", OUTLET, (-1, 0, 0), zone=1),
    )
    zones = (Zone("a", "fluid", text(a / 2), nu=1e-6), Zone("b", "fluid", text(1.5 * a), nu=1e-6))
    case = FastCase(text(a / 2), (0, 0, 0), (length, 2 * a, a), ports, 1e-6, zones=zones)
    runner, status = _run(case, steps=20_000, fidelity=3)
    try:
        frame = runner.frame()
        assert frame.guard == 0 and not frame.unstable and not status["les"]
        one, other = frame.streams
        for stream in (one, other):
            assert stream["flow"] == pytest.approx(speed * a * a, rel=0.01)
            assert stream["flow_out"] == pytest.approx(stream["flow"], rel=0.01)
        assert one["dp"] > 0.0 and one["dp"] == pytest.approx(other["dp"], rel=0.01)
    finally:
        runner.stop()
