"""Fast mode's zones and temperature (ADR-0047 addendum 2): several bodies, fluids that do not mix,
solids that conduct, and the streams' figures.

The packing, the streams and the reading of a multi-region project run without a GPU; the measured
checks (a duct's Nusselt number, a two-layer slab, a counter-flow exchanger against
effectiveness-NTU) are ``gpu`` tests.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.fast import lattice
from stokes.fast.case import FastCase, Zone
from stokes.fast.errors import FastInvalid
from stokes.fast.grid import Grid
from stokes.fast.ports import (
    INLET,
    OUTLET,
    PORT_STRIDE,
    WALL_FLUX,
    WALL_TEMPERATURE,
    Port,
    pack_ports,
    port_from_triangles,
    zone_base,
)
from stokes.fast.runner import FastRunner
from stokes.fast.zones import (
    PortSample,
    fluid_diffusivity,
    reference_temperature,
    start_temperature,
    streams,
    zone_rows,
    zone_texts,
)
from stokes.implicit.features import box
from stokes.implicit.graph import to_text
from tests.test_implicit_sketch_faces import needs_mesher

MM = 1e3


def _rect(axis: int, at: float, low: tuple[float, float], high: tuple[float, float]) -> np.ndarray:
    """Two triangles of the axis-aligned rectangle at ``at`` (m) spanning the other two axes."""
    corners = [(low[0], low[1]), (high[0], low[1]), (high[0], high[1]), (low[0], high[1])]
    points = []
    for a, b in corners:
        p = [0.0, 0.0, 0.0]
        p[axis] = at
        first, second = (i for i in range(3) if i != axis)
        p[first], p[second] = a, b
        points.append(tuple(p))
    return np.array([[points[0], points[1], points[2]], [points[0], points[2], points[3]]])


def _box_text(size: tuple[float, float, float], centre: tuple[float, float, float]) -> str:
    return to_text(box(size, centre).field())


def _counterflow(conductivity: float = 6.0, wall: float = 200.0) -> FastCase:
    """Two 5 mm square channels 100 mm long, hot and cold in counter-flow at Re 50, with a 1.25 mm
    wall between them."""
    a, t, length, speed = 0.005, 0.00125, 0.10, 0.01
    hot = _box_text((length * MM, a * MM, a * MM), (length * MM / 2, a * MM / 2, a * MM / 2))
    mid = _box_text((length * MM, t * MM, a * MM), (length * MM / 2, (a + t / 2) * MM, a * MM / 2))
    cold = _box_text(
        (length * MM, a * MM, a * MM), (length * MM / 2, (a + t + a / 2) * MM, a * MM / 2)
    )
    y0, y1 = a + t, 2 * a + t
    ports = (
        port_from_triangles(
            _rect(0, 0.0, (0, 0), (a, a)),
            "hot_in",
            INLET,
            (-1, 0, 0),
            velocity=(speed, 0, 0),
            zone=0,
            temperature=350.0,
        ),
        port_from_triangles(_rect(0, length, (0, 0), (a, a)), "hot_out", OUTLET, (1, 0, 0), zone=0),
        port_from_triangles(
            _rect(0, length, (y0, 0), (y1, a)),
            "cold_in",
            INLET,
            (1, 0, 0),
            velocity=(-speed, 0, 0),
            zone=1,
            temperature=300.0,
        ),
        port_from_triangles(
            _rect(0, 0.0, (y0, 0), (y1, a)), "cold_out", OUTLET, (-1, 0, 0), zone=1
        ),
    )
    zones = (
        Zone("hot", "fluid", hot, nu=1e-6, rho=1000.0, cp=4000.0, kappa=conductivity),
        Zone("cold", "fluid", cold, nu=1e-6, rho=1000.0, cp=4000.0, kappa=conductivity),
        Zone("wall", "solid", mid, rho=2700.0, cp=900.0, kappa=wall),
    )
    return FastCase(hot, (0, 0, 0), (length, y1, a), ports, 1e-6, zones=zones, thermal=True)


# ------------------------------------------------------------------------------ without a GPU


def test_a_case_with_no_zones_is_its_one_fluid() -> None:
    case = FastCase("_0 var-x", (0, 0, 0), (1, 1, 1), (), 2e-6)
    assert [(z.kind, z.nu) for z in case.all_zones] == [("fluid", 2e-6)]
    assert zone_texts(case) == [("_0 var-x", False)]
    assert not case.thermal and fluid_diffusivity(case) == 0.0


def test_the_zones_rows_hold_rates_conductivities_and_a_capped_solid() -> None:
    """A fluid's row has its own relaxation rates and its true lattice diffusivity; a solid's heat
    capacity is the one that steps it at the cap, whatever its own."""
    case = _counterflow(conductivity=0.6)
    scaling = lattice.scaling_for(1.5e-4, 0.01, 1e-6)
    rows = zone_rows(case, scaling)
    assert rows.shape == (3, PORT_STRIDE)
    assert rows[0, 0] == pytest.approx(scaling.omega_plus) and rows[0, 1] > 0.0
    assert rows[0, 2] == pytest.approx(0.6) and rows[2, 2] == pytest.approx(200.0)
    alpha = 0.6 / (1000.0 * 4000.0) * scaling.dt / scaling.dx**2
    assert rows[0, 2] / rows[0, 3] == pytest.approx(alpha, rel=1e-5)
    assert rows[2, 2] / rows[2, 3] == pytest.approx(lattice.DIFFUSIVITY_LATTICE_MAX)
    assert rows[0, 6] == 0.0 and rows[2, 6] == 1.0
    # Temperatures are carried less the mean of the inlets'.
    assert reference_temperature(case) == pytest.approx(325.0)
    assert [start_temperature(case, i) for i in range(3)] == [350.0, 300.0, 325.0]
    assert rows[:, 7] == pytest.approx([25.0, -25.0, 0.0])


def test_the_packed_table_puts_the_zones_behind_the_ports() -> None:
    case = _counterflow()
    scaling = lattice.scaling_for(1.5e-4, 0.01, 1e-6)
    table, _masks = pack_ports(
        list(case.ports),
        scaling.speed,
        {},
        zone_rows=zone_rows(case, scaling),
        reference_temperature=325.0,
        cell_size=scaling.dx,
    )
    assert zone_base(len(case.ports)) == 4 and table.shape == (4 + 3, PORT_STRIDE)
    assert table[0, 24] == pytest.approx(25.0) and table[2, 24] == pytest.approx(-25.0)
    assert list(table[:4, 25]) == [0.0, 0.0, 1.0, 1.0]  # each port's zone
    assert zone_base(0) == 1  # a table is never empty


def test_a_thermal_wall_packs_its_temperature_or_its_flux_times_the_cell() -> None:
    square = _rect(0, 0.0, (0, 0), (0.01, 0.01))
    held = port_from_triangles(square, "hot", WALL_TEMPERATURE, (-1, 0, 0), temperature=360.0)
    heated = port_from_triangles(square, "heater", WALL_FLUX, (-1, 0, 0), heat_flux=5000.0)
    table, _ = pack_ports([held, heated], 1.0, {}, reference_temperature=300.0, cell_size=2e-4)
    assert table[0, 3] == 2.0 and table[0, 24] == pytest.approx(60.0)
    assert table[1, 3] == 3.0 and table[1, 24] == pytest.approx(5000.0 * 2e-4)
    with pytest.raises(FastInvalid, match="thermal wall"):
        port_from_triangles(square, "x", "slip", (-1, 0, 0))


def _sample(port: Port, speed: float, warmth: float, cells: int = 100) -> PortSample:
    velocity = np.tile(np.asarray(port.normal, dtype=np.float32) * speed, (cells, 1))
    return PortSample(
        port, velocity, np.zeros(cells, dtype=np.float32), np.full(cells, warmth, np.float32)
    )


def test_the_streams_are_the_results_tables_figures() -> None:
    """Each fluid's flow, its inlet and mixing-cup outlet temperatures and the heat it gained: mass
    flow times specific heat times the rise, as the Results stage works its streams out."""
    case = _counterflow()
    dx = 0.005 / 10  # so a hundred cells cover each 5 mm port
    scaling = lattice.Scaling(dx=dx, dt=dx * 0.05 / 0.01, nu=1e-6)
    hot_in, hot_out, cold_in, cold_out = case.ports
    samples = [
        _sample(hot_in, -0.05, 25.0),
        _sample(hot_out, 0.05, 15.0),
        _sample(cold_in, -0.05, -25.0),
        _sample(cold_out, 0.05, -15.0),
    ]
    found = streams(case, samples, scaling, 0.0)
    assert [s["name"] for s in found] == ["hot", "cold"]  # the solid is not a stream
    flow = 0.01 * 0.005 * 0.005
    hot, cold = found
    assert hot["flow"] == pytest.approx(flow) and hot["flow_out"] == pytest.approx(flow)
    assert hot["mass_flow"] == pytest.approx(1000.0 * flow)
    assert (hot["t_in"], hot["t_out"]) == pytest.approx((350.0, 340.0))
    assert hot["heat"] == pytest.approx(-1000.0 * flow * 4000.0 * 10.0)
    assert cold["heat"] == pytest.approx(-hot["heat"])
    assert hot["dp"] == pytest.approx(0.0, abs=1e-9)
    assert hot["inlets"] == ["hot_in"] and hot["outlets"] == ["hot_out"]


def test_a_stream_with_no_temperature_reports_its_flow_alone() -> None:
    case = FastCase(
        "_0 var-x",
        (0, 0, 0),
        (1, 1, 1),
        (
            port_from_triangles(
                _rect(0, 0.0, (0, 0), (0.01, 0.01)), "in", INLET, (-1, 0, 0), velocity=(0.1, 0, 0)
            ),
        ),
        1e-6,
    )
    scaling = lattice.Scaling(dx=1e-3, dt=5e-4, nu=1e-6)
    (found,) = streams(case, [_sample(case.ports[0], -0.05, 0.0)], scaling, 0.0)
    assert found["flow"] == pytest.approx(0.1 * 1e-4) and "heat" not in found and "dp" not in found


def test_the_scaling_takes_the_stiffest_and_the_fastest_of_the_fluids() -> None:
    """Two fluids share one time step: the cell Reynolds number is the larger of theirs, and the
    more viscous one's lattice viscosity is the one capped."""
    both = lattice.scaling_for(1e-4, 0.01, 1e-6, zones=[(0.01, 1e-6), (0.5, 1e-6)])
    assert both.reynolds == pytest.approx(50.0) and both.les
    assert both.to_lattice(0.5) == pytest.approx(lattice.SPEED)  # the fastest inlet of them all
    thick = lattice.scaling_for(1e-4, 0.01, 1e-6, zones=[(0.01, 1e-6), (0.01, 1e-3)])
    assert 3.0 * 1e-3 * thick.dt / thick.dx**2 + 0.5 == pytest.approx(1.0)
    plus, minus = thick.rates(1e-3)
    assert plus == pytest.approx(1.0) and minus > 0.0
    # A fluid that conducts faster than the temperature's step allows lowers the speed too.
    quick = lattice.scaling_for(1e-4, 0.01, 1e-6, diffusivity=1e-3)
    assert 1e-3 * quick.dt / quick.dx**2 == pytest.approx(lattice.DIFFUSIVITY_LATTICE_MAX)


def test_the_closure_is_shear_improved_until_the_cells_are_coarse() -> None:
    fine = lattice.scaling_for(1e-4, 0.2, 1e-6)  # cell Reynolds number 20
    coarse = lattice.scaling_for(1e-4, 0.5, 1e-6)  # 50
    assert (fine.les, fine.closure) == (True, "shear_improved")
    assert coarse.closure == "smagorinsky"
    assert lattice.scaling_for(1e-4, 0.5, 1e-6, closure="shear_improved").closure == (
        "shear_improved"
    )
    with pytest.raises(FastInvalid, match="closure"):
        lattice.scaling_for(1e-4, 0.5, 1e-6, closure="k-epsilon")


def test_the_runner_takes_a_closure_and_refuses_an_unknown_one() -> None:
    runner = FastRunner(lambda *_a, **_k: None)
    assert runner.status()["turbulence"] == "auto"
    runner.configure(turbulence="smagorinsky")
    assert runner.status()["turbulence"] == "smagorinsky"
    with pytest.raises(FastInvalid, match="turbulence"):
        runner.configure(turbulence="k-epsilon")


def test_the_temperatures_mesh_covers_the_solids_and_is_in_kelvin() -> None:
    from stokes.fast.plane import thermal_mesh

    grid = Grid(6, 5, 3, (0.0, 0.0, 0.0), 0.001, 2)
    scalars = np.zeros((5, 6, 4), dtype=np.float32)
    scalars[:, :, 1] = 1.0  # outside every body
    scalars[1:4, 1:3, 1] = 0.0  # a fluid
    scalars[1:4, 3:5, 1] = 0x82  # a solid beside it
    scalars[1:4, 1:3, 0] = 10.0
    scalars[1:4, 3:5, 0] = -10.0
    positions, triangles, temperature = thermal_mesh(grid, 2, 1, scalars, 320.0)
    assert positions.shape[0] == 30 and triangles.shape[0] == 2 * 5 * 4
    assert temperature.min() == pytest.approx(310.0) and temperature.max() == pytest.approx(330.0)
    assert positions[:, 0].min() == pytest.approx(0.001) and positions[:, 0].max() == pytest.approx(
        0.005
    )


def test_an_offset_is_a_length_or_a_number_of_metres() -> None:
    from stokes.api.fast_inputs import offset_metres

    assert offset_metres("2 [mm]") == pytest.approx(0.002)
    assert offset_metres(0.003) == 0.003 and offset_metres(None) is None
    for bad in ("2 [kg]", "soon", True):
        with pytest.raises(FastInvalid, match="length"):
            offset_metres(bad)


def test_fast_mode_can_be_switched_off_by_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from stokes.fast.device import DISABLE_ENV, open_device
    from stokes.fast.errors import FastUnavailable

    monkeypatch.setenv(DISABLE_ENV, "1")
    with pytest.raises(FastUnavailable, match="switched off") as caught:
        open_device()
    assert caught.value.details["reason"] == "disabled"


def test_a_run_is_not_steady_while_its_heat_still_moves() -> None:
    """A heated solid warms long after the flow has settled: the streams' heat is watched too."""
    from stokes.fast.steady import Sample, steady

    warming = [Sample(0.1 * i, 1000 * i, 1.0, 2.0, 1.0 + 0.002 * i) for i in range(40)]
    assert not steady(warming, steps=500)
    settled = [Sample(0.1 * i, 1000 * i, 1.0, 2.0, 3.0) for i in range(40)]
    assert steady(settled, steps=500)


def test_the_cells_across_a_fluid_are_its_hydraulic_diameter_in_cells() -> None:
    """A long duct 32 cells square reads about 32 whatever its length, a slot four cells high about
    eight (twice its gap, as a hydraulic diameter is), and a solid is not listed."""
    from stokes.fast.zones import RESOLVED_CELLS, cells_across

    codes = np.ones((40, 40, 400), dtype=np.uint32)
    codes[4:36, 4:36, 2:398] = 0  # the first fluid: a duct
    codes[2:3, :, :] = 0x82  # a solid sheet, zone 2
    zones = (
        Zone("duct", "fluid", ""),
        Zone("slot", "fluid", ""),
        Zone("sheet", "solid", ""),
    )
    slot = np.ones((12, 200, 200), dtype=np.uint32)
    slot[4:8, 2:198, 2:198] = 2  # the second fluid's code
    (duct,) = [r for r in cells_across(codes, zones) if r["name"] == "duct"][:1]
    assert duct["cells"] == 32 * 32 * 396 and duct["cells_across"] == pytest.approx(32.0, rel=0.05)
    found = {r["name"]: r["cells_across"] for r in cells_across(slot, zones)}
    assert found["slot"] == pytest.approx(8.0, rel=0.1) and found["slot"] < RESOLVED_CELLS
    assert found["duct"] == 0.0 and "sheet" not in found


def test_the_walls_keep_a_cells_mass_and_the_guard_holds_the_speed_alone() -> None:
    """What the interpolated walls return less what was sent to them comes off the rest population
    (they lost a tenth of the flow in channels eight cells wide), and the density is not capped:
    with the incompressible equilibrium it is the pressure alone."""
    from stokes.fast.kernel import step_shader
    from stokes.fast.wgsl import compile_field

    step = step_shader([(compile_field("_0 var-x"), False)], 8, 8)
    assert step.count("leak += f") == 18 and "  f0 -= leak;" in step
    assert step.index("  f0 -= leak;") < step.index("var rho0 = 0.0;")
    assert "clamp(rho0" not in step and "if (speed > 0.3) {" in step


def test_a_frame_says_when_the_temperature_is_catching_up() -> None:
    from stokes.fast.runner import THERMAL_CATCH_UP, Frame

    assert THERMAL_CATCH_UP > 1
    assert Frame().to_json()["catching_up"] is False
    assert Frame(catching_up=True).to_json()["catching_up"] is True


# ------------------------------------------------------------------------------ a real project


def _faces(session: Any, part: str, axis: int, sign: float) -> list[int]:  # Any: Session
    from stokes.geometry.retag import faces_of
    from stokes.geometry.store import GeometryStore

    mesh = GeometryStore.of(session).mesh(part)
    assert mesh is not None
    faces, _ = faces_of(mesh)
    return [i for i, f in faces.items() if f.normal[axis] * sign > 0.99]


def _exchanger(tmp_path: Path) -> Any:  # Any: Session
    """Hot and cold water channels with an aluminium wall between them, set up through the
    commands a user's clicks send."""
    pytest.importorskip("OCP")
    import stokes.geometry.model  # noqa: F401 - the operation node types
    from tests.test_implicit_operations import implicit, new_session

    session = new_session(tmp_path)

    def body(size: list[float], at: list[float], name: str) -> str:
        info = implicit(session, "implicit_primitive", kind="box", size=size, position=at)
        part = str(info["outputs"][0])
        session.execute("node.rename", node=part, name=name)
        return part

    hot = body([0.06, 0.005, 0.005], [0.0, 0.0, 0.0], "hot")
    wall = body([0.06, 0.00125, 0.005], [0.0, 0.003125, 0.0], "plate")
    cold = body([0.06, 0.005, 0.005], [0.0, 0.00625, 0.0], "cold")
    session.execute("material.add_from_library", fluid="water_liquid", name="water")
    session.execute("material.add_from_library", fluid="aluminium", name="aluminium")
    session.execute("region.create", name="hot", kind="fluid", bodies=[hot], material="water")
    session.execute("region.create", name="cold", kind="fluid", bodies=[cold], material="water")
    session.execute(
        "region.create", name="plate", kind="solid", bodies=[wall], material="aluminium"
    )
    for part, name, into, temperature in ((hot, "hot", -1.0, 350.0), (cold, "cold", 1.0, 300.0)):
        session.execute(
            "boundary.assign",
            target=[{"part": part, "faces": _faces(session, part, 0, into)}],
            kind="velocity_inlet",
            name=f"{name}_in",
            values={"velocity": "0.02 [m/s]", "temperature": f"{temperature} [K]"},
        )
        session.execute(
            "boundary.assign",
            target=[{"part": part, "faces": _faces(session, part, 0, -into)}],
            kind="pressure_outlet",
            name=f"{name}_out",
            values={},
        )
    return session


@needs_mesher  # the bodies are built and tagged by the geometry worker
def test_a_multi_region_project_reads_as_zones_with_their_streams(tmp_path: Path) -> None:
    from stokes.api.fast_inputs import case_from_session
    from stokes.geometry.store import GeometryStore

    session = _exchanger(tmp_path)
    case, density = case_from_session(session)
    assert [(z.name, z.kind) for z in case.zones] == [
        ("hot", "fluid"),
        ("cold", "fluid"),
        ("plate", "solid"),
    ]
    assert case.thermal  # several bodies exchange heat, whatever the settings say
    hot, _cold, plate = case.zones
    assert 5e-7 < hot.nu < 2e-6 and hot.cp > 4000.0 and 0.5 < hot.kappa < 0.7
    assert plate.kappa > 100.0 and plate.nu == 0.0
    assert 900.0 < density < 1100.0
    by_name = {p.name: p for p in case.ports}
    assert {n: (p.kind, p.zone) for n, p in by_name.items()} == {
        "hot_in": (INLET, 0),
        "hot_out": (OUTLET, 0),
        "cold_in": (INLET, 1),
        "cold_out": (OUTLET, 1),
    }
    assert by_name["hot_in"].temperature == pytest.approx(350.0)
    assert by_name["cold_in"].velocity[0] == pytest.approx(-0.02, rel=1e-5)
    # The box is every body's, the solid's too.
    assert case.low[1] == pytest.approx(-0.0025) and case.high[1] == pytest.approx(0.00875)
    GeometryStore.of(session).close()


@needs_mesher
def test_a_fluid_with_an_inlet_and_no_outlet_is_refused_by_name(tmp_path: Path) -> None:
    from stokes.errors import StokesError
    from stokes.geometry.store import GeometryStore

    session = _exchanger(tmp_path)
    outlet = next(
        n
        for n in session.require_project().root.walk()
        if n.type == "boundary" and n.name == "cold_out"
    )
    session.execute("boundary.set", boundary=outlet.id, kind="wall")
    with pytest.raises(StokesError) as caught:
        session.execute("fast.start")
    assert caught.value.code == "fast.invalid" and "cold" in caught.value.message
    assert caught.value.details["code"] == "fast.no_outlet"
    GeometryStore.of(session).close()


# ------------------------------------------------------------------------------ on the GPU


def _steps(runner: FastRunner, steps: int, seconds: float = 240.0) -> dict[str, Any]:  # Any: JSON
    deadline = time.monotonic() + seconds
    status: dict[str, Any] = {}  # Any: JSON
    while time.monotonic() < deadline:
        time.sleep(0.5)
        status = runner.status()
        assert status["state"] not in ("failed", "stopped"), status
        if status["frame"]["step"] >= steps:
            return status
    raise AssertionError(f"timed out: {status}")


@pytest.mark.gpu
def test_a_duct_with_walls_at_one_temperature_gives_its_nusselt_number() -> None:
    """Re 50, Pr 1, a square duct with walls at 350 K: the mixing-cup temperature's decay past the
    entrance gives Nu within 2 % of 2.976 (Shah and London; measured -0.2 %)."""
    side, length, speed, nu, rho, cp = 0.01, 0.12, 0.005, 1e-6, 1000.0, 4000.0
    kappa = rho * cp * nu
    text = _box_text((length * MM, side * MM, side * MM), (length * MM / 2, side * MM / 2, 5.0))
    ports = (
        port_from_triangles(
            _rect(0, 0.0, (0, 0), (side, side)),
            "in",
            INLET,
            (-1, 0, 0),
            velocity=(speed, 0, 0),
            temperature=300.0,
        ),
        port_from_triangles(_rect(0, length, (0, 0), (side, side)), "out", OUTLET, (1, 0, 0)),
    )
    zone = Zone(
        "water", "fluid", text, nu=nu, rho=rho, cp=cp, kappa=kappa, wall="temperature",
        wall_value=350.0,
    )  # fmt: skip
    case = FastCase(text, (0, 0, 0), (length, side, side), ports, nu, zones=(zone,), thermal=True)
    runner = FastRunner(lambda *_a, **_k: None)
    runner.configure(axis=1)
    runner.start(case, fidelity=2)
    try:
        _steps(runner, 30_000)
        frame = runner.frame()
        (stream,) = frame.streams
        assert frame.guard == 0 and not frame.unstable
        # The plane through the axis: the centreline's approach to the wall's temperature decays
        # at the bulk's rate once developed.
        _key, positions, _triangles = frame.mesh_of("T")
        x, z = positions[:, 0], positions[:, 2]
        centre = np.abs(z - side / 2) < 2e-4
        window = centre & (x > 0.05) & (x < 0.105)
        slope = np.polyfit(x[window], np.log(350.0 - frame.values["T"][window]), 1)[0]
        nusselt = -slope * speed * side**2 / (4 * kappa / (rho * cp))
        assert nusselt == pytest.approx(2.976, rel=0.02)
        assert 345.0 < stream["t_out"] < 350.0
        assert stream["heat"] == pytest.approx(
            rho * speed * side**2 * cp * (stream["t_out"] - 300.0), rel=1e-3
        )
    finally:
        runner.stop()


@pytest.mark.gpu
def test_two_solid_layers_conduct_as_resistances_in_series() -> None:
    """k = 10 and 1 W/m/K, 5 mm each, between walls at 350 K and 300 K: no flow, and the plane's
    temperature at the interface is 345.45 K (measured 345.44 to 345.46)."""
    first = _box_text((5.0, 5.0, 5.0), (2.5, 2.5, 2.5))
    second = _box_text((5.0, 5.0, 5.0), (7.5, 2.5, 2.5))
    side = 0.005
    ports = (
        port_from_triangles(
            _rect(0, 0.0, (0, 0), (side, side)), "hot", WALL_TEMPERATURE, (-1, 0, 0), zone=0,
            temperature=350.0,
        ),
        port_from_triangles(
            _rect(0, 0.01, (0, 0), (side, side)), "cold", WALL_TEMPERATURE, (1, 0, 0), zone=1,
            temperature=300.0,
        ),
    )  # fmt: skip
    zones = (
        Zone("a", "solid", first, rho=2700.0, cp=900.0, kappa=10.0, start=325.0),
        Zone("b", "solid", second, rho=2700.0, cp=900.0, kappa=1.0, start=325.0),
    )
    case = FastCase(first, (0, 0, 0), (0.01, side, side), ports, 1e-6, zones=zones, thermal=True)
    runner = FastRunner(lambda *_a, **_k: None)
    runner.configure(axis=2)
    runner.start(case, fidelity=1)
    try:
        _steps(runner, 30_000)
        frame = runner.frame()
        _key, positions, _triangles = frame.mesh_of("T")
        temperature = frame.values["T"]
        middle = np.abs(positions[:, 1] - side / 2) < 1.5e-4
        x = positions[middle, 0]
        t = temperature[middle]
        in_first = (x > 0.0008) & (x < 0.0042)
        in_second = (x > 0.0058) & (x < 0.0092)
        from_first = np.polyval(np.polyfit(x[in_first], t[in_first], 1), 0.005)
        from_second = np.polyval(np.polyfit(x[in_second], t[in_second], 1), 0.005)
        assert from_first == pytest.approx(345.455, abs=0.1)
        assert from_second == pytest.approx(345.455, abs=0.1)
        flux = -10.0 * np.polyfit(x[in_first], t[in_first], 1)[0]
        assert flux == pytest.approx(50.0 / (0.005 / 10.0 + 0.005 / 1.0), rel=0.01)
    finally:
        runner.stop()


@pytest.mark.gpu
def test_a_counter_flow_exchanger_balances_and_meets_effectiveness_ntu() -> None:
    """Hot and cold channels with a wall between (thermally developed: Pr 0.67): what the hot
    stream gives is what the cold one takes (measured to 1 mW in 23 W), within 10 % of the
    effectiveness-NTU duty with the one-heated-wall Nusselt number 2.712 (measured +4.3 %), and
    the two fluids keep their own flow rates."""
    case = _counterflow()
    runner = FastRunner(lambda *_a, **_k: None)
    runner.start(case, fidelity=1)
    try:
        _steps(runner, 25_000)
        frame = runner.frame()
        hot, cold = frame.streams
        capacity = 1000.0 * 0.01 * 0.005**2 * 4000.0
        film = 2.712 * 6.0 / 0.005
        ntu = (0.005 * 0.10) / (2.0 / film + 0.00125 / 200.0) / capacity
        duty = ntu / (1.0 + ntu) * capacity * 50.0
        assert frame.guard == 0 and not frame.unstable
        assert hot["mass_flow"] == pytest.approx(2.5e-4, rel=0.01)
        assert cold["mass_flow"] == pytest.approx(2.5e-4, rel=0.01)
        assert hot["flow_out"] == pytest.approx(hot["flow"], rel=0.01)
        assert hot["heat"] < 0.0 < cold["heat"]
        assert frame.heat_balance == pytest.approx(0.0, abs=0.02 * abs(hot["heat"]))
        assert abs(hot["heat"]) == pytest.approx(duty, rel=0.10)
        assert math.isclose(hot["dp"], cold["dp"], rel_tol=0.02) and hot["dp"] > 1.0
    finally:
        runner.stop()


@pytest.mark.gpu
def test_a_solid_heated_by_a_flux_gives_its_power_to_the_stream() -> None:
    """A 5 mm water channel on an aluminium base whose under-side takes 10 kW/m2 (3 W): once the
    flow is steady the temperature takes several steps to each of the lattice's, and the stream's
    heat closes on the power within 8 % (measured 3.12 W after 59,000 steps; 2.76 W after 500,000
    without the catch-up), with steady claimed only then."""
    a, t, length, speed, nu = 0.005, 0.0025, 0.06, 0.02, 1e-6
    fluid = _box_text((60.0, 5.0, 5.0), (30.0, 2.5, 2.5))
    base = _box_text((60.0, 2.5, 5.0), (30.0, -1.25, 2.5))
    ports = (
        port_from_triangles(
            _rect(0, 0.0, (0, 0), (a, a)), "in", INLET, (-1, 0, 0), velocity=(speed, 0, 0),
            zone=0, temperature=300.0,
        ),
        port_from_triangles(_rect(0, length, (0, 0), (a, a)), "out", OUTLET, (1, 0, 0), zone=0),
        port_from_triangles(
            _rect(1, -t, (0, 0), (length, a)), "heater", WALL_FLUX, (0, -1, 0), zone=1,
            heat_flux=1.0e4,
        ),
    )  # fmt: skip
    zones = (
        Zone("water", "fluid", fluid, nu=nu, rho=1000.0, cp=4180.0, kappa=0.6),
        Zone("base", "solid", base, rho=2700.0, cp=900.0, kappa=200.0),
    )
    case = FastCase(fluid, (0, -t, 0), (length, a, a), ports, nu, zones=zones, thermal=True)
    runner = FastRunner(lambda *_a, **_k: None)
    runner.start(case, fidelity=1)
    try:
        deadline = time.monotonic() + 150.0
        caught_up = False
        frame = runner.frame()
        while time.monotonic() < deadline:
            time.sleep(0.5)
            assert runner.status()["state"] not in ("failed", "stopped")
            frame = runner.frame()
            caught_up = caught_up or frame.catching_up
            if frame.steady:
                break
        assert frame.steady and caught_up
        (stream,) = frame.streams
        assert stream["heat"] == pytest.approx(1.0e4 * length * a, rel=0.08)
        assert frame.guard == 0 and not frame.unstable
    finally:
        runner.stop()
