"""Fast mode's safety checks (ADR-0047 addendum 5), without a GPU: the fluid's pieces, the
refusal of a fluid whose ports are not joined, a seam inside a body, and when a run has run away.
The same on the GPU is ``test_fast_safety_gpu.py``."""

from __future__ import annotations

import math

import numpy as np
import pytest

from stokes.fast import health
from stokes.fast.case import FastCase
from stokes.fast.errors import FastInvalid
from stokes.fast.health import Health, instability, settling
from stokes.fast.ports import INLET, OUTLET, port_from_triangles
from stokes.fast.wgsl import SEAM_TOLERANCE, classify_shader, compile_field, zone_fields
from stokes.fast.zones import connected, disconnected


def _square(x: float, side: float) -> np.ndarray:
    p = [(x, 0.0, 0.0), (x, side, 0.0), (x, side, side), (x, 0.0, side)]
    return np.array([[p[0], p[1], p[2]], [p[0], p[2], p[3]]])


def _case() -> FastCase:
    inlet = port_from_triangles(_square(0.0, 0.01), "in", INLET, (-1, 0, 0), velocity=(0.1, 0, 0))
    outlet = port_from_triangles(_square(0.02, 0.01), "out", OUTLET, (1, 0, 0))
    return FastCase("_0 const -1.0", (0.0, 0.0, 0.0), (0.02, 0.01, 0.01), (inlet, outlet), 1e-6)


# ------------------------------------------------------------------------------ the pieces


def test_cells_join_through_faces_and_edges_not_corners() -> None:
    """The lattice's links join cells that share a face or an edge (D3Q19), never a corner."""
    mask = np.zeros((4, 4, 4), bool)
    mask[1, 1, 1] = mask[1, 2, 2] = True  # an edge apart
    labels, pieces = connected(mask)
    assert pieces == 1 and labels[1, 1, 1] == labels[1, 2, 2] != 0
    mask[:] = False
    mask[1, 1, 1] = mask[2, 2, 2] = True  # a corner apart
    labels, pieces = connected(mask)
    assert pieces == 2 and labels[1, 1, 1] != labels[2, 2, 2]
    assert connected(np.zeros((3, 3, 3), bool))[1] == 0


def test_the_pieces_match_a_flood_fill_on_a_random_mask() -> None:
    """Runs along x joined row to row give the same partition as a cell-by-cell flood fill."""
    rng = np.random.default_rng(3)
    mask = rng.random((7, 9, 11)) < 0.3
    labels, pieces = connected(mask)
    offsets = [
        (a, b, c)
        for a in (-1, 0, 1)
        for b in (-1, 0, 1)
        for c in (-1, 0, 1)
        if 0 < abs(a) + abs(b) + abs(c) <= 2
    ]
    seen = np.zeros_like(mask)
    found = 0
    for start in zip(*np.nonzero(mask), strict=True):
        if seen[start]:
            continue
        found += 1
        stack, members = [start], []
        seen[start] = True
        while stack:
            cell = stack.pop()
            members.append(cell)
            for off in offsets:
                n = tuple(cell[i] + off[i] for i in range(3))
                if all(0 <= n[i] < mask.shape[i] for i in range(3)) and mask[n] and not seen[n]:
                    seen[n] = True
                    stack.append(n)
        assert len({int(labels[m]) for m in members}) == 1
    assert pieces == found


def _codes(cut: bool) -> tuple[np.ndarray, list[np.ndarray]]:
    """A duct of fluid cells (code 0) along x in a solid box; ``cut`` puts a solid wall across."""
    codes = np.ones((6, 6, 20), np.uint32)
    codes[1:-1, 1:-1, 1:-1] = 0
    if cut:
        codes[:, :, 10] = 1
    flat = np.arange(codes.size).reshape(codes.shape)
    return codes, [
        flat[1:-1, 1:-1, 1].ravel().astype(np.uint32),
        flat[1:-1, 1:-1, -2].ravel().astype(np.uint32),
    ]


def test_a_fluid_whose_inlet_reaches_its_outlet_is_not_refused() -> None:
    codes, ports = _codes(cut=False)
    assert disconnected(codes, ports, _case(), 2) is None


def test_a_fluid_in_pieces_is_refused_naming_it() -> None:
    codes, ports = _codes(cut=True)
    refusal = disconnected(codes, ports, _case(), 2)
    assert isinstance(refusal, FastInvalid)
    assert refusal.code == "fast.invalid"
    assert refusal.details["reason"] == "fast.disconnected"
    assert refusal.details["fluid"] == "fluid" and refusal.details["pieces"] == 2
    assert refusal.details["ports"] == ["in", "out"]
    assert "'fluid'" in refusal.message and "fidelity 2" in refusal.message
    assert "'in' reaches no outlet and 'out' no inlet" in refusal.message


def test_a_port_with_no_cells_is_not_judged_here() -> None:
    codes, ports = _codes(cut=True)
    ports[1] = np.zeros(0, np.uint32)
    assert disconnected(codes, ports, _case(), 2) is None


# ------------------------------------------------------------------------------ the seam


def test_every_zone_field_is_read_with_its_seams_taken_out() -> None:
    """The classification and the step read a zone through ``phiN``, which looks to either side of
    a cell whose field is within round-off of zero; the raw field is ``phiRawN``."""
    text = compile_field("_0 var-x\n_1 const 9.0\n_2 sub _0 _1")
    wgsl = zone_fields([(text, False), (text, True)])
    assert "fn phiRaw0(" in wgsl and "fn phiRaw1(" in wgsl
    assert "fn phi0(p: vec3f) -> f32" in wgsl and "fn phi1(p: vec3f) -> f32" in wgsl
    assert wgsl.count("max(phiRaw0(p + ") == 4  # three axes and one oblique direction
    assert "P.dx" in wgsl and 0.0 < SEAM_TOLERANCE < 0.01
    assert "phi0(pos)" in classify_shader([(text, False)], 8, 8)


# ------------------------------------------------------------------------------ the runaway


def test_a_frame_with_a_sign_of_a_runaway_is_not_valid() -> None:
    ok = {"dp": 5.0, "flow_in": 1e-5, "flow_out": 1.01e-5, "swing": 0.01, "guard": 0}
    assert instability(**ok, cell_steps=1e6) == ""
    assert instability(**{**ok, "dp": math.nan}, cell_steps=1e6) == "not_finite"
    assert instability(**ok, cell_steps=1e6, nan=True) == "not_finite"
    assert instability(**{**ok, "swing": 0.8}, cell_steps=1e6) == "density"
    assert instability(**{**ok, "guard": 50_000}, cell_steps=1e6) == "guard"
    assert instability(**{**ok, "guard": 100}, cell_steps=1e6) == ""
    # An imbalance with the guard holding cells is a sign; alone, only when it does not die away.
    assert instability(**{**ok, "flow_out": 2.1e-5, "guard": 10}, cell_steps=1e6) == "imbalance"
    assert instability(**{**ok, "flow_out": 0.6e-5, "guard": 10}, cell_steps=1e6) == "imbalance"
    assert instability(**{**ok, "flow_out": 0.6e-5}, cell_steps=1e6) == ""  # no frames before
    swinging = [0.5, -0.5, 0.45, -0.5]
    assert instability(**{**ok, "flow_out": 0.6e-5}, cell_steps=1e6, before=swinging) == (
        "imbalance"
    )


#: A restrictive laminar core filling with pressure (measured 2026-11-24 on an orifice duct at a
#: hole Reynolds number of 0.4): the outflow rises smoothly towards the inflow, the guard idle.
FILLING = [0.40 + 0.015 * i for i in range(40)]


def test_a_core_filling_with_pressure_is_never_a_runaway() -> None:
    """The regression of 2026-11-24: a smooth fill-up was flagged as an imbalance and failed."""
    ok = {"dp": 400.0, "flow_in": 1.0, "swing": 0.01, "guard": 0}
    for reynolds in (0.04, 20.0):  # creeping, and well past it: the trend alone clears it
        watch = Health()
        for ratio in FILLING:
            sign = instability(
                **ok,
                flow_out=ratio,
                cell_steps=1e6,
                before=list(watch.imbalances),
                reynolds=reynolds,
            )
            assert sign == ""
            assert not watch.judge(sign, ratio - 1.0)
    # Draining after an edit that lowered the inlet: from the other side, the same.
    draining = [1.0 - b for b in (-0.6, -0.5, -0.45, -0.4, -0.38, -0.33, -0.3, -0.28)]
    assert all(settling(b, draining[:i]) for i, b in enumerate(draining))


def test_an_imbalance_that_keeps_or_grows_its_amplitude_is_a_sign() -> None:
    assert not settling(-0.4, [-0.3, -0.3, -0.35, -0.35])  # growing
    assert not settling(-0.4, [-0.4, -0.4, -0.4, -0.4])  # stuck
    assert not settling(0.3, [-0.5, -0.4])  # through balance and out again
    assert settling(-0.3, [-0.5, -0.45, -0.4, -0.35])  # dying away
    assert settling(-0.9, [])  # too few to tell


def test_a_creeping_flow_with_the_guard_idle_is_never_flagged_for_imbalance() -> None:
    ok = {"dp": 400.0, "flow_in": 1.0, "swing": 0.01, "guard": 0, "cell_steps": 1e6}
    swinging = [0.5, -0.5, 0.45, -0.5]
    assert instability(**ok, flow_out=0.5, before=swinging, reynolds=0.5) == ""
    assert instability(**ok, flow_out=0.5, before=swinging, reynolds=5.0) == "imbalance"
    # With the guard on: past a creeping flow at once; in one, only when it does not settle.
    assert instability(**{**ok, "guard": 5}, flow_out=0.5, reynolds=5.0) == "imbalance"
    assert instability(**{**ok, "guard": 5}, flow_out=0.5, reynolds=0.5) == ""
    assert instability(**{**ok, "guard": 5}, flow_out=0.5, before=swinging, reynolds=0.5) == (
        "imbalance"
    )


def test_a_run_has_run_away_when_most_of_a_window_is_not_valid() -> None:
    """An oscillating runaway passes through valid-looking frames: a share of the window decides."""
    watch = Health()
    signs = ["imbalance", ""] * (health.WINDOW // 2)
    verdicts = [watch.judge(s) for s in signs]
    assert not any(verdicts[:-1]) and verdicts[-1]
    assert watch.reason() == "imbalance"
    calm = Health()
    assert not any(calm.judge("imbalance" if i % 4 == 0 else "") for i in range(5 * health.WINDOW))
    assert Health().judge("not_finite")  # NaN at once


def test_a_run_gets_a_few_restarts_then_fails() -> None:
    watch = Health()
    for _ in range(health.RECOVERIES):
        assert not watch.spent
        watch.restart()
        assert len(watch.recent) == 0
    assert watch.spent


@pytest.mark.parametrize("share", [0.0, 0.25])
def test_a_stable_runs_frames_never_add_up_to_a_runaway(share: float) -> None:
    watch = Health()
    every = int(1 / share) if share else 0
    assert not any(
        watch.judge("guard" if every and i % every == 0 else "") for i in range(10 * health.WINDOW)
    )
