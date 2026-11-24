"""Fast mode's pure parts (ADR-0047): the field compiler, the lattice, the grid, the ports, the
case. No GPU: the shaders are generated and checked as text here and run in ``test_fast_gpu.py``."""

from __future__ import annotations

import dataclasses
import math
import re

import numpy as np
import pytest

from stokes.fast import lattice
from stokes.fast.case import FastCase
from stokes.fast.errors import FastInvalid, FastPortNotPlanar, FastTooFast
from stokes.fast.grid import CELLS_ACROSS, PADDING, Grid, grid_for
from stokes.fast.kernel import step_shader
from stokes.fast.ports import (
    INLET,
    OUTLET,
    PORT_STRIDE,
    pack_ports,
    port_from_triangles,
)
from stokes.fast.wgsl import (
    classify_shader,
    compile_field,
    gather_shader,
    slice_shader,
)
from stokes.implicit.features import box, cylinder
from stokes.implicit.graph import to_text

# ------------------------------------------------------------------------------ the field compiler


def test_the_compiler_writes_one_let_per_line_of_the_modellers_text() -> None:
    text = to_text(cylinder(4.0, 12.0, (0.0, 0.0, 0.0), "x").field())
    wgsl = compile_field(text)
    assert wgsl.startswith("fn phi(p: vec3f) -> f32 {")
    lines = [line for line in text.splitlines() if line.strip()]
    assert wgsl.count("  let v") == len(lines)
    assert f"return v{len(lines) - 1};" in wgsl
    assert "p.x" in wgsl and "p.y" in wgsl and "p.z" in wgsl


def test_mod_is_the_floored_remainder_the_numpy_evaluator_uses() -> None:
    wgsl = compile_field("_0 var-x\n_1 const 3.0\n_2 mod _0 _1")
    assert "v0 - v1 * floor(v0 / v1)" in wgsl


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("_0 var-x\n_1 bogus _0", "unknown operation"),
        ("_0 var-x\n_1 add _0 _7", "defined above"),
        ("_0 const nan", "not finite"),
        ("_0 const inf", "not finite"),
        ("_0 const 1e9999", "not finite"),
        ("_0 const two", "not a number"),
        ("x var-x", "badly"),
        ("_0 var-x\n_0 var-y", "badly"),
        ("", "empty"),
    ],
)
def test_the_compiler_refuses_what_is_not_the_closed_operation_set(text: str, match: str) -> None:
    with pytest.raises(FastInvalid, match=match):
        compile_field(text)


def test_every_shader_is_generated_for_a_real_field() -> None:
    field = compile_field(to_text(box((20.0, 10.0, 10.0), (0.0, 0.0, 0.0)).field()))
    step = step_shader([(field, False)], 40, 20)
    assert step.count("var f") >= 18 and "counters.guard" in step and "P.smag" in step
    assert "P.ramp" in step  # the equilibrium inlet layer takes the ramp
    assert "flags[id] = code | (adj << 8u)" in classify_shader([(field, False)], 40, 20)
    assert "out[at]" in slice_shader() and "arrayLength(&cells)" in gather_shader()


# ------------------------------------------------------------------------------ the lattice


def test_the_d3q19_lattice_has_its_moments() -> None:
    c = np.array([lattice.CX, lattice.CY, lattice.CZ], dtype=float).T
    w = np.array(lattice.W)
    assert math.isclose(w.sum(), 1.0)
    assert np.allclose(w @ c, 0.0)
    assert np.allclose(
        (w[:, None, None] * c[:, :, None] * c[:, None, :]).sum(axis=0), np.eye(3) / 3
    )
    for d, o in enumerate(lattice.OPP):
        assert np.array_equal(c[o], -c[d])


def test_the_scaling_converts_both_ways_and_switches_the_les_on_above_the_laminar_limit() -> None:
    laminar = lattice.scaling_for(dx=1e-4, fastest=0.01, nu=1e-6)  # cell Reynolds number 1
    assert not laminar.les
    assert math.isclose(laminar.to_si(laminar.to_lattice(0.01)), 0.01)
    assert math.isclose(laminar.to_lattice(0.01), lattice.SPEED)
    assert math.isclose(laminar.pressure(laminar.density(0.3)), 0.3)
    assert laminar.tau > 0.5
    turbulent = lattice.scaling_for(dx=1e-3, fastest=0.05, nu=1e-6)  # 50
    assert turbulent.les
    assert lattice.cell_reynolds(1e-3, 0.05, 1e-6) == pytest.approx(50.0)


def test_cells_far_too_coarse_for_the_flow_are_refused_with_the_number() -> None:
    with pytest.raises(FastTooFast, match="cell Reynolds number") as caught:
        lattice.scaling_for(dx=1e-2, fastest=1.0, nu=1e-6)
    assert caught.value.details["cell_reynolds"] > lattice.RE_CELL_MAX


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan])
def test_the_scaling_refuses_a_value_that_is_not_positive(bad: float) -> None:
    with pytest.raises(FastInvalid):
        lattice.scaling_for(dx=bad, fastest=0.1, nu=1e-6)


# ------------------------------------------------------------------------------ the grid


def test_the_fidelity_sets_the_cells_across_the_smallest_side_plus_padding() -> None:
    grid = grid_for((0.0, 0.0, 0.0), (0.2, 0.01, 0.02), fidelity=2)
    assert grid.dx == pytest.approx(0.01 / CELLS_ACROSS[2])
    assert grid.ny == CELLS_ACROSS[2] + 2 * PADDING
    assert grid.nx == math.ceil(0.2 / grid.dx) + 2 * PADDING
    # The box sits in the middle of the grid.
    low_gap = 0.0 - grid.origin[0]
    high_gap = grid.origin[0] + grid.nx * grid.dx - 0.2
    assert low_gap == pytest.approx(high_gap)
    assert not grid.coarsened


def test_a_cap_on_the_cells_coarsens_the_grid_and_says_so() -> None:
    grid = grid_for((0.0, 0.0, 0.0), (1.0, 0.01, 0.01), fidelity=5, max_cells=200_000)
    assert grid.cells <= 200_000
    assert grid.coarsened


def test_the_fidelity_is_one_to_five() -> None:
    with pytest.raises(FastInvalid, match="1 to 5"):
        grid_for((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), fidelity=6)
    with pytest.raises(FastInvalid, match="finite"):
        grid_for((0.0, 0.0, 0.0), (0.0, 1.0, 1.0))


# ------------------------------------------------------------------------------ the ports


def _square(x: float, side: float, flip: bool = False) -> np.ndarray:
    p = [(x, 0.0, 0.0), (x, side, 0.0), (x, side, side), (x, 0.0, side)]
    tris = np.array([[p[0], p[1], p[2]], [p[0], p[2], p[3]]])
    return tris[:, ::-1] if flip else tris


def test_a_flat_square_is_a_port_whose_mask_covers_its_area() -> None:
    port = port_from_triangles(_square(0.0, 0.01), "in", INLET, outward=(-1.0, 0.0, 0.0))
    assert port.normal == pytest.approx((-1.0, 0.0, 0.0))
    assert port.area == pytest.approx(1e-4)
    assert port.origin == pytest.approx((0.0, 0.005, 0.005))
    covered = float(port.mask.sum()) * port.pixel**2
    assert covered == pytest.approx(port.area, rel=0.1)  # the rim is dilated by one pixel


def test_the_outward_normal_decides_the_side_whatever_the_winding() -> None:
    port = port_from_triangles(_square(0.0, 0.01, flip=True), "in", INLET, outward=(1.0, 0.0, 0.0))
    assert port.normal == pytest.approx((1.0, 0.0, 0.0))


def test_two_faces_at_an_angle_are_not_one_port() -> None:
    side = np.array([[(0, 0, 0), (0.01, 0, 0), (0.01, 0, 0.01)]], dtype=float)
    end = _square(0.0, 0.01)
    with pytest.raises(FastPortNotPlanar, match="not one plane"):
        port_from_triangles(np.concatenate([end, side]), "in", INLET)


def test_a_port_leaves_out_the_slivers_of_the_faces_round_its_rim() -> None:
    """An opening tagged with a face that wraps its rim (a sliver on the plane, the rest on the side
    wall): the triangles off the plane are 2 % of the area and are left out, as the solve takes
    such a tag; a side face of real size is still refused (the test above)."""
    end = _square(0.0, 0.01)
    sliver = np.array([[(0, 0, 0), (0.0004, 0, 0), (0.0004, 0, 0.01)]], dtype=float)
    port = port_from_triangles(np.concatenate([end, sliver]), "in", INLET, (-1.0, 0.0, 0.0))
    assert port.area == pytest.approx(1e-4, rel=1e-6)
    assert port.normal == pytest.approx((-1.0, 0.0, 0.0), abs=1e-9)


def test_two_parallel_faces_apart_are_not_one_port() -> None:
    near = _square(0.0, 0.01)
    far = _square(0.005, 0.01)
    with pytest.raises(FastPortNotPlanar):
        port_from_triangles(np.concatenate([near, far]), "out", OUTLET)


def test_the_packed_table_holds_each_ports_plane_footprint_and_condition() -> None:
    inlet = port_from_triangles(_square(0.0, 0.01), "in", INLET, (-1, 0, 0), velocity=(0.2, 0, 0))
    outlet = port_from_triangles(_square(0.1, 0.01), "out", OUTLET, (1, 0, 0), pressure=5.0)
    table, masks = pack_ports([inlet, outlet], to_lattice=4.0, density_of={"out": 0.01})
    assert table.shape == (2, PORT_STRIDE) and table.dtype == np.float32
    assert table[0, 3] == 0.0 and table[1, 3] == 1.0
    assert table[0, 0:3] == pytest.approx(np.array(inlet.origin) * 1e3)
    assert table[0, 20] == pytest.approx(0.2 / 4.0)
    assert table[1, 19] == pytest.approx(0.01)  # the density's deviation from 1
    assert table[1, 7] == inlet.mask.size  # the second mask starts after the first
    assert masks.size == inlet.mask.size + outlet.mask.size


def test_a_case_with_no_port_still_packs_a_bindable_table() -> None:
    table, masks = pack_ports([], to_lattice=1.0, density_of={})
    assert table.shape == (1, PORT_STRIDE) and masks.size == 1


# ------------------------------------------------------------------------------ the case


def test_the_geometry_key_follows_the_field_and_the_ports_but_not_their_values() -> None:
    text = to_text(box((20.0, 10.0, 10.0), (10.0, 5.0, 5.0)).field())
    inlet = port_from_triangles(_square(0.0, 0.01), "in", INLET, (-1, 0, 0), velocity=(0.1, 0, 0))
    case = FastCase(text, (0, 0, 0), (0.02, 0.01, 0.01), (inlet,), 1e-6)
    faster = dataclasses.replace(case, ports=(dataclasses.replace(inlet, velocity=(0.3, 0, 0)),))
    assert faster.geometry_key == case.geometry_key
    assert faster.fastest == pytest.approx(0.3)
    longer = dataclasses.replace(
        case, field_text=to_text(box((30.0, 10.0, 10.0), (15.0, 5.0, 5.0)).field())
    )
    assert longer.geometry_key != case.geometry_key


# ------------------------------------------------------- what the review of 2026-11-22 changed


def test_a_slow_viscous_flow_lowers_the_lattice_speed_to_hold_tau_at_one() -> None:
    """A cell Reynolds number of 0.016 at the default lattice speed had tau 10.1 and a fifth of
    its flow; the lattice viscosity is capped instead, and the speed follows."""
    slow = lattice.scaling_for(dx=3.125e-4, fastest=0.005, nu=1e-4)
    assert slow.tau == pytest.approx(1.0)
    assert slow.nu_lattice == pytest.approx(lattice.NU_LATTICE_MAX)
    assert slow.inlet_lattice_speed == pytest.approx(0.015625 * lattice.NU_LATTICE_MAX)
    assert slow.to_lattice(0.005) == pytest.approx(slow.inlet_lattice_speed)
    assert not slow.les
    # At and above the cross-over the default speed stands.
    usual = lattice.scaling_for(dx=1e-4, fastest=0.01, nu=1e-6)
    assert usual.inlet_lattice_speed == lattice.SPEED and usual.tau < 1.0


def test_the_equilibrium_is_the_incompressible_one_on_the_densitys_deviation() -> None:
    """No velocity is a momentum divided by a density, and no density sum starts at 1: the first
    made the volume flow follow the pressure (5 % along a duct), the second lost a slow flow in
    f32 round-off."""
    from stokes.fast.kernel import step_shader
    from stokes.fast.wgsl import gather_shader, slice_shader

    step = step_shader([(compile_field("_0 var-x"), False)], 8, 8)
    for text in (step, slice_shader(), gather_shader()):
        assert re.search(r"var \w+ = 1\.0;", text) is None  # no sum starts at 1
        assert "/ rho" not in text and "m / r" not in text and "jl / rl" not in text
    assert "var rho0 = 0.0;" in step
    # The rest direction's equilibrium: the weight times the deviation, and a kinetic term with
    # no density in front of it.
    assert "let e0 = 0.3333333333333333 * rho + 0.3333333333333333 * (3.0 *" in step


def test_the_turbulent_collision_is_the_regularised_one() -> None:
    from stokes.fast.kernel import step_shader

    step = step_shader([(compile_field("_0 var-x"), False)], 8, 8)
    les, laminar = step.split("      o0 = f0 - wp * (f0 - e0);")
    assert "let keep = 1.0 - 1.0 / te;" in les
    assert "o0 = e0 + keep * 1.5 * (" in les  # 4.5 times the rest weight
    assert "wm * (fm - em)" in laminar and "(fm - em)" not in les


def test_a_port_only_takes_crossings_on_its_own_plane() -> None:
    from stokes.fast.kernel import step_shader
    from stokes.fast.wgsl import PORT_PLANE_TOLERANCE

    assert PORT_PLANE_TOLERANCE < 0.5
    step = step_shader([(compile_field("_0 var-x"), False)], 8, 8)
    assert f"anyPortAt(p, {PORT_PLANE_TOLERANCE})" in step and "> tol * P.dx" in step


def test_the_layer_next_to_a_port_is_as_thick_as_its_longest_link() -> None:
    from stokes.fast.ports import layer_thickness

    assert layer_thickness((1.0, 0.0, 0.0)) == pytest.approx(1.0)
    assert layer_thickness((0.0, -1.0, 0.0)) == pytest.approx(1.0)
    diagonal = 1.0 / math.sqrt(2.0)
    assert layer_thickness((diagonal, diagonal, 0.0)) == pytest.approx(math.sqrt(2.0))
    assert layer_thickness((0.8660254, 0.5, 0.0)) == pytest.approx(1.3660254)


def test_an_inlets_gain_restores_the_flow_its_profile_and_its_cells_lose() -> None:
    from stokes.fast.ports import GAIN_RANGE, inlet_gains

    inlet = port_from_triangles(_square(0.0, 0.01), "in", INLET, (-1, 0, 0), velocity=(0.2, 0, 0))
    outlet = port_from_triangles(_square(0.1, 0.01), "out", OUTLET, (1, 0, 0))
    cell = 0.01 / 10
    # Ten by ten cells, the rim at half share: 64 whole and 36 half cells cover 82 % of the port.
    shares = np.ones((10, 10))
    shares[0, :] = shares[-1, :] = shares[:, 0] = shares[:, -1] = 0.5
    gains = inlet_gains([inlet, outlet], [shares.reshape(-1), np.ones(100)], cell)
    assert gains == {"in": pytest.approx(1.0 / 0.82)}
    table, _ = pack_ports([inlet, outlet], 1.0, {}, gains=gains)
    assert table[0, 23] == pytest.approx(1.0 / 0.82) and table[1, 23] == 1.0
    # No cell, or cells that do not describe the port: no gain, or a bounded one.
    assert inlet_gains([inlet], [np.zeros(0)], cell) == {"in": 1.0}
    assert inlet_gains([inlet], [np.full(3, 1.0)], cell) == {"in": GAIN_RANGE[1]}


def test_a_run_is_steady_only_over_a_window_long_enough_in_steps_and_seconds() -> None:
    from stokes.fast.steady import STEADY_SECONDS, Sample, steady

    def samples(count: int, steps_each: int, drift: float = 0.0) -> list[Sample]:
        return [Sample(0.1 * i, steps_each * i, 1.0 + drift * i, 2.0) for i in range(count)]

    # Too short in seconds, then long enough in both.
    assert not steady(samples(10, 1000), steps=500)
    history = samples(int(STEADY_SECONDS / 0.1) + 5, 1000)
    assert steady(history, steps=500)
    assert history[-1].time - history[0].time < STEADY_SECONDS + 0.2  # trimmed to the window
    # Long enough in seconds but not in steps: a large grid's two seconds are few steps.
    assert not steady(samples(40, 10), steps=5000)
    assert steady(samples(600, 10), steps=5000)
    # A drift of more than a hundredth across the window is not steady, in either value.
    assert not steady(samples(40, 1000, drift=0.001), steps=500)
    flow = [Sample(0.1 * i, 1000 * i, 1.0, 2.0 + 0.002 * i) for i in range(40)]
    assert not steady(flow, steps=500)
    assert not steady([], steps=0)


def test_the_settling_time_is_the_sooner_of_a_flow_through_and_the_viscous_time() -> None:
    from stokes.fast.steady import settle_steps

    grid = grid_for((0.0, 0.0, 0.0), (0.12, 0.01, 0.01), fidelity=2)
    usual = lattice.scaling_for(grid.dx, 0.005, 1e-6)
    assert settle_steps(grid, usual) == int(0.5 * grid.nx / lattice.SPEED)
    slow = lattice.scaling_for(grid.dx, 0.005, 1e-3)  # the viscosity crosses long before the flow
    assert settle_steps(grid, slow) == int(0.5 * grid.ny**2 / lattice.NU_LATTICE_MAX)


def test_a_frame_reports_its_flow_balance() -> None:
    from stokes.fast.runner import Frame

    frame = Frame(flow_in=2.0e-6, flow_out=1.96e-6)
    assert frame.imbalance == pytest.approx(-0.02)
    assert frame.to_json()["imbalance"] == pytest.approx(-0.02)
    assert Frame().imbalance == 0.0


# ------------------------------------------------------------------------------ the shown plane


def _cut(fluid: np.ndarray) -> np.ndarray:
    """A plane sample: unit x velocity and a small density deviation in the fluid cells."""
    cut = np.zeros((*fluid.shape, 4), dtype=np.float32)
    cut[..., 0] = 0.01
    cut[..., 3] = np.where(fluid, 0.003, 1e30)
    return cut


def test_the_plane_reaches_the_walls_and_is_at_rest_there() -> None:
    """Four by three fluid cells in a six by five sample: the mesh spans the cells' own extent (the
    walls of a duct square to the lattice), not the centres' alone, with zero speed on its rim and
    the fluid's pressure there."""
    from stokes.fast.plane import plane_mesh

    grid = Grid(6, 5, 3, (0.0, 0.0, 0.0), 0.001, 2)
    scaling = lattice.scaling_for(grid.dx, 0.01, 1e-6)
    fluid = np.zeros((5, 6), dtype=bool)
    fluid[1:4, 1:5] = True
    positions, triangles, values = plane_mesh(grid, 2, 1, _cut(fluid), scaling, reference=2.0)
    assert positions[:, 0].min() == pytest.approx(0.001) and positions[:, 0].max() == pytest.approx(
        0.005
    )
    assert positions[:, 1].min() == pytest.approx(0.001) and positions[:, 1].max() == pytest.approx(
        0.004
    )
    assert np.allclose(positions[:, 2], 0.0015)
    assert positions.shape[0] == 6 * 5 and triangles.shape[0] == 2 * 5 * 4
    rim = (
        np.isclose(positions[:, 0], 0.001)
        | np.isclose(positions[:, 0], 0.005)
        | np.isclose(positions[:, 1], 0.001)
        | np.isclose(positions[:, 1], 0.004)
    )
    assert rim.sum() == 30 - 12
    assert np.all(values["U"][rim] == 0.0)
    assert values["U"][~rim] == pytest.approx(0.01 * scaling.speed)
    assert values["p"] == pytest.approx(2.0 + 0.003 / 3.0 * scaling.speed**2)
    assert triangles.max() < positions.shape[0]


def test_the_plane_of_a_sample_with_no_fluid_is_empty() -> None:
    from stokes.fast.plane import plane_mesh

    grid = Grid(4, 4, 3, (0.0, 0.0, 0.0), 0.001, 2)
    scaling = lattice.scaling_for(grid.dx, 0.01, 1e-6)
    positions, triangles, values = plane_mesh(
        grid, 0, 1, _cut(np.zeros((3, 4), dtype=bool)), scaling, 0.0
    )
    assert positions.shape == (0, 3) and triangles.shape == (0, 3) and values["U"].size == 0


def test_a_solid_cell_between_two_fluid_cells_stays_where_it_is() -> None:
    from stokes.fast.plane import _wall_ring

    fluid = np.zeros((3, 5), dtype=bool)
    fluid[1, 1] = fluid[1, 3] = True
    ring, shift_a, shift_b = _wall_ring(fluid)
    assert ring[1, 2] and shift_a[1, 2] == 0.0 and shift_b[1, 2] == 0.0
    assert shift_a[1, 0] == 0.5 and shift_a[1, 4] == -0.5  # towards the fluid
    assert shift_a[0, 0] == 0.5 and shift_b[0, 0] == 0.5  # across a corner: along both


def test_the_plane_setting_takes_the_automatic_axis_and_refuses_others() -> None:
    from stokes.fast.runner import AUTOMATIC, FastRunner

    runner = FastRunner(lambda *_a, **_k: None)
    runner.configure(axis=1, offset=0.002)
    assert not runner.status()["automatic_plane"]
    runner.configure(axis=AUTOMATIC)
    assert runner.status()["automatic_plane"]
    with pytest.raises(FastInvalid, match="-1 for the automatic plane"):
        runner.configure(axis=3)
    with pytest.raises(FastInvalid, match="finite"):
        runner.configure(offset=math.nan)
